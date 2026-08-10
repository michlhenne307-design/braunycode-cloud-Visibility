"""Der ganze Lauf gegen einen nachgebauten Ollama.

Warum das noetig ist: Die beiden Abstuerze im Betrieb sassen NICHT in einem
Modul, sondern zwischen zweien.

  1. qwen3-coder schickt tool_calls[].function.arguments als JSON-TEXT. Das
     Python-Paket 'ollama' verlangt dort ein dict und brach ab, bevor unsere
     Behandlung ueberhaupt lief.
  2. Der Agent schickt seinen Verlauf im OpenAI-Format zurueck - dort ist
     arguments ebenfalls ein Text. Ollama will an derselben Stelle ein Objekt
     und antwortete mit 400.

Beide Male war jedes Modul fuer sich richtig und getestet. Kaputt war die
Naht. Ein Test, der jede Seite einzeln prueft, findet so etwas nie.

Dieser Test startet deshalb einen HTTP-Dienst, der sich wie Ollama verhaelt -
einschliesslich seiner Eigenheiten - und laesst die vollstaendige
Werkzeugschleife dagegen laufen: Modellaufruf, Werkzeug, Ergebnis zurueck ins
Gespraech, naechster Aufruf.

Er ersetzt keinen Lauf gegen das echte Modell. Er prueft die Verkabelung,
nicht die Klugheit.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HIER = Path(__file__).resolve().parent
sys.path.insert(0, str(HIER / "app"))

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name} {detail}")


# --------------------------------------------------------------- Falscher Ollama

class FalschesModell:
    """Antwortet nach Drehbuch - und zwar so, wie qwen3-coder wirklich antwortet.

    Der entscheidende Punkt: 'arguments' geht als JSON-TEXT hinaus, nicht als
    Objekt. Genau daran ist der erste Lauf gestorben.
    """

    def __init__(self, drehbuch):
        self.drehbuch = list(drehbuch)
        self.gesehen = []      # jede empfangene Nutzlast, zum Nachpruefen

    def naechste(self, nutzlast):
        self.gesehen.append(nutzlast)
        if not self.drehbuch:
            return {"content": "Nichts mehr zu tun.", "tool_calls": []}
        zug = self.drehbuch.pop(0)
        if "tool" in zug:
            return {
                "content": zug.get("text", ""),
                "tool_calls": [{
                    "function": {
                        "name": zug["tool"],
                        # ALS TEXT - wie das echte Modell.
                        "arguments": json.dumps(zug.get("args", {})),
                    }
                }],
            }
        return {"content": zug.get("text", ""), "tool_calls": []}


def starte_falschen_ollama(modell: FalschesModell):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/api/tags":
                self._json({"models": [{"name": "falsch:1b"}]})
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path != "/api/chat":
                self.send_error(404)
                return
            laenge = int(self.headers.get("content-length", 0))
            nutzlast = json.loads(self.rfile.read(laenge) or b"{}")

            # Ollama ist hier streng: arguments MUESSEN ein Objekt sein.
            # Genau diese Ablehnung hat den zweiten Lauf beendet.
            for nachricht in nutzlast.get("messages", []):
                for aufruf in nachricht.get("tool_calls") or []:
                    args = (aufruf.get("function") or {}).get("arguments")
                    if not isinstance(args, dict):
                        self._json({"error": "invalid tool call arguments: "
                                             f"expected object, got {type(args).__name__}"},
                                   status=400)
                        return

            antwort = modell.naechste(nutzlast)
            self.send_response(200)
            self.send_header("content-type", "application/x-ndjson")
            self.end_headers()
            # Wie Ollama: zeilenweise, das Letzte mit done und Messwerten.
            text = antwort.get("content") or ""
            for i in range(0, len(text), 7) or [0]:
                self._zeile({"message": {"content": text[i:i + 7]}, "done": False})
            self._zeile({
                "message": {"content": "", "tool_calls": antwort.get("tool_calls") or []},
                "done": True,
                "prompt_eval_count": 1234, "prompt_eval_duration": 2_000_000_000,
                "eval_count": 42, "eval_duration": 1_000_000_000,
                "total_duration": 3_000_000_000, "load_duration": 0,
            })

        def _zeile(self, obj):
            self.wfile.write(json.dumps(obj).encode() + b"\n")
            self.wfile.flush()

        def _json(self, obj, status=200):
            roh = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(roh)))
            self.end_headers()
            self.wfile.write(roh)

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


# --------------------------------------------------------------- Lauf

def fahre(drehbuch, aufgabe="Schreibe rechner.py mit addiere(a, b)"):
    """Laesst die echte Werkzeugschleife gegen das falsche Modell laufen."""
    modell = FalschesModell(drehbuch)
    server, port = starte_falschen_ollama(modell)
    os.environ["OLLAMA_HOST"] = f"127.0.0.1:{port}"

    for name in list(sys.modules):
        if name in ("provider", "main", "agentloop", "tools", "workspace"):
            del sys.modules[name]

    import provider, agentloop, tools, workspace  # noqa: E402
    provider.OLLAMA_HOST = f"http://127.0.0.1:{port}"
    provider.MODEL = "falsch:1b"

    arbeitsordner = tempfile.mkdtemp()
    ws = workspace.Workspace(arbeitsordner)
    ereignisse = []

    async def send(typ, text="", **extra):
        ereignisse.append({"type": typ, "text": text, **extra})

    # Bewusst ueber main.model_chat statt direkt an provider.chat: genau dort
    # sitzt die Bruecke vom Arbeitsfaden in den Ereignisloop. Ein Test, der
    # sie umgeht, prueft einen Weg, den es im Betrieb nicht gibt - und
    # verpasst jeden Fehler darin.
    import main
    main.ASK_TIMEOUT = 60
    chat_fn = main.model_chat

    async def lauf():
        return await agentloop.run_tool_agent(send, aufgabe, chat_fn=chat_fn,
                                              toolbox=tools.Toolbox(ws), max_steps=8)

    try:
        ergebnis = asyncio.run(lauf())
    finally:
        server.shutdown()
    return ergebnis, ereignisse, modell, Path(arbeitsordner)


print("[E2E] Ganze Werkzeugschleife gegen einen nachgebauten Ollama")

# In dieser Umgebung gibt es kein Docker, also auch keine Sandbox: run_python
# scheitert. Das ist kein Mangel des Tests - check_syntax ist ebenfalls ein
# Beleg, und die Verkabelung ist dieselbe.
ergebnis, ereignisse, modell, ordner = fahre([
    # Mit Begleittext, damit auch der Strom durch die ganze Kette geprueft
    # wird - vom falschen Ollama ueber den Arbeitsfaden bis ins Ereignis.
    {"tool": "write_file",
     "text": "Ich lege die Funktion an und schreibe einen Test dazu.",
     "args": {"path": "rechner.py",
              "content": "def addiere(a, b):\n    return a + b\n"}},
    {"tool": "write_file", "args": {"path": "test_rechner.py",
                                    "content": "from rechner import addiere\n"
                                               "assert addiere(2, 3) == 5\n"
                                               "print('ok')\n"}},
    {"tool": "check_syntax", "args": {"path": "rechner.py"}},
    {"tool": "check_syntax", "args": {"path": "test_rechner.py"}},
    {"tool": "finish", "args": {"summary": "rechner.py mit Test, Syntax geprüft."}},
])

arten = [e["type"] for e in ereignisse]
fehler = [e for e in ereignisse if e["type"] == "error"]

check("der Lauf kommt bis zum Abschluss", "done" in arten, arten)
check("kein Modellaufruf schlägt fehl",
      not [f for f in fehler if "Modellaufruf" in f.get("text", "")],
      [f["text"][:120] for f in fehler])
check("kein 400 von Ollama",
      not [f for f in fehler if "400" in f.get("text", "")],
      [f["text"][:120] for f in fehler])
check("mehrere Werkzeuge wurden aufgerufen",
      arten.count("tool") >= 4, arten.count("tool"))

# Der Beweis, dass die Uebersetzung in BEIDE Richtungen stimmt: Ab dem
# zweiten Aufruf steht der eigene Werkzeugaufruf im Verlauf - und der
# falsche Ollama weist ihn ab, wenn arguments kein Objekt ist.
spaeter = [n for n in modell.gesehen if len(n.get("messages", [])) > 2]
check("der Verlauf wird mehrfach zurückgeschickt", len(spaeter) >= 3, len(spaeter))
mit_aufrufen = [m for n in spaeter for m in n["messages"] if m.get("tool_calls")]
check("eigene Werkzeugaufrufe stehen im Verlauf", bool(mit_aufrufen))
check("und ihre Argumente sind Objekte, keine Texte",
      all(isinstance(a["function"]["arguments"], dict)
          for m in mit_aufrufen for a in m["tool_calls"]),
      [type(a["function"]["arguments"]).__name__
       for m in mit_aufrufen for a in m["tool_calls"]][:3])

werkzeug_antworten = [m for n in spaeter for m in n["messages"] if m.get("role") == "tool"]
check("Werkzeugergebnisse gehen als role=tool zurück", bool(werkzeug_antworten))
check("und tragen tool_name, wie Ollama es erwartet",
      all("tool_name" in m for m in werkzeug_antworten),
      [sorted(m) for m in werkzeug_antworten[:2]])

check("die Dateien liegen wirklich im Projekt",
      (ordner / "rechner.py").exists() and (ordner / "test_rechner.py").exists(),
      sorted(p.name for p in ordner.iterdir()))
check("die geschriebene Datei hat den richtigen Inhalt",
      "def addiere" in (ordner / "rechner.py").read_text())

abschluss = [e for e in ereignisse if e["type"] == "done"][-1]
check("der Abschluss meldet Erfolg", abschluss.get("ok") is True, abschluss)
check("das Ergebnis der Schleife ist 'ok'", ergebnis == "ok", ergebnis)

messungen = [e for e in ereignisse if e["type"] == "messung"]
check("jeder Schritt meldet Messwerte", len(messungen) >= 4, len(messungen))
check("die Messwerte sind gefüllt",
      messungen and messungen[0].get("prompt_token") == 1234, messungen[:1])

stroeme = [e for e in ereignisse if e["type"] == "delta"]
# Wie VIELE Stuecke ankommen, haengt am Zeitverhalten: der Ausgeber leert
# alles, was gerade da ist, auf einmal - hier liefert der falsche Server
# sofort, also kommt es in einem Stueck. Das ist richtig so. Gepruft wird
# deshalb, DASS der Text ankommt und vollstaendig ist, nicht in wie vielen
# Teilen.
check("Text kommt während der Erzeugung an", len(stroeme) >= 1, len(stroeme))
check("und er ergibt zusammengesetzt den ganzen Satz",
      "".join(e["text"] for e in stroeme).startswith("Ich lege die Funktion an"),
      "".join(e["text"] for e in stroeme)[:60])


# --- Der Beleg-Riegel im ganzen Lauf --------------------------------------
#
# Das Versprechen des Programms lautet: kein Abschluss ohne Beleg. Bisher war
# das nur in tools.py fuer sich geprueft. Hier laeuft es durch die ganze
# Kette - Modell, Werkzeug, Ergebnis, naechster Aufruf.

print("\n[E2E] Kein Abschluss ohne Beleg")

_erg2, _ev2, _m2, _ordner2 = fahre([
    {"tool": "write_file", "args": {"path": "ungeprueft.py",
                                    "content": "def x():\n    return 1\n"}},
    # Direkt fertigmelden, ohne irgendetwas geprueft zu haben.
    {"tool": "finish", "args": {"summary": "Alles bestens."}},
    {"tool": "finish", "args": {"summary": "Doch, wirklich."}},
    {"tool": "finish", "args": {"summary": "Jetzt aber."}},
])
_fehler2 = [e["text"] for e in _ev2 if e["type"] == "error"]
check("ein Abschluss ohne Prüfung wird abgelehnt",
      any("Abschluss abgelehnt" in f for f in _fehler2), _fehler2[:1])
check("die abgelehnte Datei wird beim Namen genannt",
      any("ungeprueft.py" in f for f in _fehler2), _fehler2[:1])

_done2 = [e for e in _ev2 if e["type"] == "done"][-1]
check("der Lauf endet trotzdem, statt ewig zu drehen", bool(_done2), _done2)
check("und er behauptet keinen Erfolg, den es nicht gibt",
      _done2.get("ok") is not True or _done2.get("verified") is False, _done2)

# --- Und der Gegenbeweis: der falsche Ollama muss den alten Fehler auch
#     wirklich melden koennen, sonst prueft der Test oben nichts.
import provider as _prov  # noqa: E402

_kaputt = [{"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": "x", "arguments": '{"a": 1}'}}]}]
_uebersetzt = _prov._ollama_messages(_kaputt)
check("die Übersetzung wandelt Text zu Objekt",
      isinstance(_uebersetzt[0]["tool_calls"][0]["function"]["arguments"], dict))

# --- Der API-Weg ----------------------------------------------------------
#
# Ein 30B-Modell auf 8 CPU-Kernen braucht Minuten je Schritt, eine API
# antwortet in Sekunden. Der Weg dorthin war gebaut, aber nie erprobt - und
# was nie lief, laeuft erfahrungsgemaess beim ersten Mal nicht. Deshalb hier
# ein OpenAI-kompatibler Endpunkt zum Anfassen.

print("\n[E2E] OpenAI-kompatibler Anbieter (DeepSeek & Co.)")

_gesehen_api = []

class _APIHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        laenge = int(self.headers.get("content-length", 0))
        nutzlast = json.loads(self.rfile.read(laenge) or b"{}")
        _gesehen_api.append({"pfad": self.path, "body": nutzlast,
                             "auth": self.headers.get("authorization", "")})
        # So antwortet eine OpenAI-kompatible API: arguments als JSON-TEXT.
        antwort = {"choices": [{"message": {
            "content": "",
            "tool_calls": [{"id": "call_1", "type": "function",
                            "function": {"name": "finish",
                                         "arguments": '{"summary": "fertig"}'}}],
        }}]}
        roh = json.dumps(antwort).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(roh)))
        self.end_headers()
        self.wfile.write(roh)

_s = socket.socket(); _s.bind(("127.0.0.1", 0)); _api_port = _s.getsockname()[1]; _s.close()
_api = HTTPServer(("127.0.0.1", _api_port), _APIHandler)
threading.Thread(target=_api.serve_forever, daemon=True).start()

import provider as _p
_alt = (_p.PROVIDER, _p.API_BASE, _p.API_KEY, _p.MODEL)
_p.PROVIDER = "openai"
_p.API_BASE = f"http://127.0.0.1:{_api_port}/v1"
_p.API_KEY = "geheim-nicht-loggen"
_p.MODEL = "deepseek-chat"
try:
    _antwort = _p.chat([{"role": "user", "content": "mach was"}],
                       [{"type": "function", "function": {"name": "finish"}}])
finally:
    _p.PROVIDER, _p.API_BASE, _p.API_KEY, _p.MODEL = _alt
    _api.shutdown()

check("die API wird unter /chat/completions angesprochen",
      _gesehen_api and _gesehen_api[0]["pfad"].endswith("/chat/completions"),
      _gesehen_api[0]["pfad"] if _gesehen_api else None)
check("der Schlüssel geht als Bearer mit",
      _gesehen_api[0]["auth"] == "Bearer geheim-nicht-loggen", "(nicht ausgegeben)")
check("das Modell steht in der Anfrage",
      _gesehen_api[0]["body"].get("model") == "deepseek-chat")
check("Werkzeuge werden mitgeschickt", "tools" in _gesehen_api[0]["body"])
check("Argumente als Text werden auch hier zu einem Objekt",
      _antwort.tool_calls and _antwort.tool_calls[0].arguments == {"summary": "fertig"},
      _antwort.tool_calls[0].arguments if _antwort.tool_calls else None)
check("die Aufruf-Kennung der API wird übernommen",
      _antwort.tool_calls[0].call_id == "call_1", _antwort.tool_calls[0].call_id)

# Ein Schluessel darf niemals in einer Fehlermeldung landen.
_fehler_text = ""
_api2 = HTTPServer(("127.0.0.1", 0), type("H", (BaseHTTPRequestHandler,), {
    "log_message": lambda *a: None,
    "do_POST": lambda s: (s.send_response(401), s.send_header("content-length", "2"),
                          s.end_headers(), s.wfile.write(b"{}")),
}))
threading.Thread(target=_api2.serve_forever, daemon=True).start()
_alt = (_p.PROVIDER, _p.API_BASE, _p.API_KEY)
_p.PROVIDER, _p.API_BASE = "openai", f"http://127.0.0.1:{_api2.server_port}/v1"
_p.API_KEY = "streng-geheimes-token-xyz"
try:
    _p.chat([{"role": "user", "content": "x"}])
except _p.ProviderError as _e:
    _fehler_text = str(_e)
finally:
    _p.PROVIDER, _p.API_BASE, _p.API_KEY = _alt
    _api2.shutdown()
check("eine Ablehnung nennt den Statuscode", "401" in _fehler_text, _fehler_text[:80])
check("und verrät dabei nie den Schlüssel",
      "streng-geheimes-token-xyz" not in _fehler_text, "(Schlüssel stand drin!)")

# Der Umschaltbefehl selbst.
_ms = (HIER / "deploy" / "modell.sh")
check("es gibt einen Modellumschalter", _ms.exists(), str(_ms))
if _ms.exists():
    _mst = _ms.read_text()
    check("er kennt lokal und deepseek",
          "lokal|ollama)" in _mst and "deepseek)" in _mst)
    check("er gibt den Schlüssel nie aus",
          "Schlüssel: gesetzt" in _mst and "cut -d= -f2-" in _mst)
    check("er schützt die Datei nach dem Schreiben", "chmod 600" in _mst)
    check("er startet den Dienst neu", "systemctl restart braunycode" in _mst)
    check("er benennt, dass Code an den Anbieter geht",
          "an den Anbieter" in _mst)

_mess = (HIER / "deploy" / "messen.sh")
check("es gibt eine Geschwindigkeitsmessung", _mess.exists(), str(_mess))
if _mess.exists():
    _mt = _mess.read_text()
    # Lesen und Antworten sind zwei verschiedene Kosten mit zwei
    # verschiedenen Gegenmitteln. Eine Zahl allein fuehrt zur falschen
    # Massnahme.
    check("sie trennt Prompt-Lesen von Antworten",
          "prompt_eval_count" in _mt and "eval_count" in _mt)
    check("sie misst kalt und warm", "Kalt" in _mt and "Warm" in _mt or "warm" in _mt)
    check("sie prüft, ob keep_alive greift", "keep_alive" in _mt and "laden" in _mt)
    check("sie sagt, was die Zahlen bedeuten", "So liest du das" in _mt)


print(f"\n=== {ok} bestanden, {fail} fehlgeschlagen ===")
sys.exit(1 if fail else 0)
