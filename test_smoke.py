"""Smoke-Tests ohne Docker/Ollama. Aufruf: python test_smoke.py"""
import asyncio
import json
import os
import struct
import shutil
import textwrap
import subprocess
import time
import sys

os.environ["BRAUNY_TOKEN"] = "geheim-test-token"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "app"))

import main  # noqa: E402
import sandbox  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ok = 0
fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name} {detail}")


print("\n[1] extract_code")
check("holt Code aus ```python-Block",
      main.extract_code("Klar!\n```python\nprint(1)\n```\nFertig.") == "print(1)")
check("holt Code aus ``` ohne Sprache",
      main.extract_code("```\nx = 2\n```") == "x = 2")
check("nimmt den laengsten Block bei mehreren",
      main.extract_code("```py\na=1\n```\ntext\n```python\nb=1\nc=2\nd=3\n```")
      == "b=1\nc=2\nd=3")
check("gibt rohen Text zurueck ohne Zaeune",
      main.extract_code("print('hallo')") == "print('hallo')")
check("laesst keine Backticks uebrig",
      "```" not in main.extract_code("```python\nprint(1)\n```"))

print("\n[2] Anbieter liest beide ollama-Client-Formen")
import provider  # noqa: E402

import httpx  # noqa: E402

class OllamaAntwort:
    """Minimalnachbau einer httpx-Antwort von Ollama.

    Eigener Name, weil weiter unten eine andere Klasse 'FakeAntwort' fuer die
    Konnektor-Pruefung steht - die wuerde diese hier sonst verdecken.
    """
    def __init__(self, daten):
        self.daten = daten
    def raise_for_status(self):
        return None
    def json(self):
        return self.daten

_letzte_anfrage = {}

def fake_ollama_chat(daten, tools=None):
    """Setzt httpx.post voruebergehend auf eine feste Ollama-Antwort."""
    orig = httpx.post
    def _post(url, json=None, timeout=None, **rest):
        _letzte_anfrage.clear()
        _letzte_anfrage.update({"url": url, "body": json})
        return OllamaAntwort(daten)
    httpx.post = _post
    try:
        return provider._chat_ollama([{"role": "user", "content": "x"}], tools)
    finally:
        httpx.post = orig

r = fake_ollama_chat({"message": {"content": "hallo"}})
check("Antworttext wird gelesen", r.text == "hallo", r)
check("ohne tool_calls bleibt die Liste leer", r.tool_calls == [])
check("die Anfrage geht an /api/chat", _letzte_anfrage["url"].endswith("/api/chat"),
      _letzte_anfrage.get("url"))
check("es wird nicht gestreamt", _letzte_anfrage["body"]["stream"] is False)
check("Werkzeugaufrufe laufen mit Temperatur 0",
      _letzte_anfrage["body"]["options"]["temperature"] == 0.0)
check("ohne Werkzeuge steht kein tools-Feld in der Anfrage",
      "tools" not in _letzte_anfrage["body"])

fake_ollama_chat({"message": {"content": ""}}, tools=[{"type": "function"}])
check("uebergebene Werkzeuge werden mitgeschickt",
      _letzte_anfrage["body"].get("tools") == [{"type": "function"}])

# Der Absturz aus dem echten Betrieb, wortwoertlich:
#
#   ValidationError: 1 validation error for Message
#   tool_calls.0.function.arguments
#     Input should be a valid dictionary
#     [type=dict_type, input_value='{}', input_type=str]
#
# qwen3-coder schickt 'arguments' als JSON-TEXT. Das Paket 'ollama' verlangt
# an der Stelle ein dict und brach ab, bevor _parse_arguments ueberhaupt lief.
r = fake_ollama_chat({"message": {"content": "", "tool_calls": [
    {"function": {"name": "list_files", "arguments": "{}"}}]}})
check("Argumente als Text '{}' stürzen nicht mehr ab",
      len(r.tool_calls) == 1 and r.tool_calls[0].arguments == {}, r.tool_calls)
check("und der Werkzeugname kommt an", r.tool_calls[0].name == "list_files")

r = fake_ollama_chat({"message": {"tool_calls": [
    {"function": {"name": "read_file", "arguments": '{"path": "main.py"}'}}]}})
check("gefüllte JSON-Textargumente werden geparst",
      r.tool_calls[0].arguments == {"path": "main.py"}, r.tool_calls[0].arguments)

r = fake_ollama_chat({"message": {"tool_calls": [
    {"function": {"name": "read_file", "arguments": {"path": "a.py"}}}]}})
check("Argumente als echtes dict bleiben unverändert",
      r.tool_calls[0].arguments == {"path": "a.py"}, r.tool_calls[0].arguments)

# Ein Aufruf ohne Namen ist nicht ausfuehrbar. Ihn zu uebergehen ist besser,
# als spaeter ueber einen leeren Werkzeugnamen zu stolpern.
r = fake_ollama_chat({"message": {"tool_calls": [
    {"function": {"arguments": "{}"}},
    {"function": {"name": "list_files", "arguments": "{}"}}]}})
check("ein Aufruf ohne Namen wird übergangen",
      [c.name for c in r.tool_calls] == ["list_files"], r.tool_calls)

try:
    fake_ollama_chat({"error": "model 'gibtsnicht' not found"})
    check("Fehlermeldung von Ollama wird weitergereicht", False, "keine Ausnahme")
except provider.ProviderError as exc:
    check("Fehlermeldung von Ollama wird weitergereicht",
          "gibtsnicht" in str(exc), exc)

_prov_src = open(provider.__file__, encoding="utf-8").read()
check("das Paket 'ollama' wird nicht mehr importiert",
      "import ollama" not in _prov_src)
check("stattdessen wird die HTTP-Schnittstelle benutzt", "/api/chat" in _prov_src)
check("OLLAMA_HOST bekommt ein Schema, auch ohne eines",
      provider.OLLAMA_HOST.startswith("http://"), provider.OLLAMA_HOST)
check("der Zeitwert für den Modellaufruf ist großzügig",
      provider.MODELL_TIMEOUT >= 600, provider.MODELL_TIMEOUT)

print("\n[3] make_project_dir")
d = sandbox.make_project_dir({"main.py": "print('x')"})
p = os.path.join(d, "main.py")
check("Datei angelegt", os.path.isfile(p))
check("Inhalt korrekt", open(p).read() == "print('x')")
check("fuer Sandbox-User lesbar", os.stat(d).st_mode & 0o007 != 0)
check("Pfadanteile werden verworfen",
      os.path.isfile(os.path.join(
          sandbox.make_project_dir({"../../evil.py": "x"}), "evil.py")))
sandbox.cleanup(project_dir=d)
check("cleanup entfernt Verzeichnis", not os.path.exists(d))

print("\n[4] stream_logs")
class FakeContainer:
    def __init__(self, lines, hang=False):
        self._lines, self._hang = lines, hang
    def logs(self, **_):
        import time
        for ln in self._lines:
            yield ln
        if self._hang:
            time.sleep(30)

async def collect(container, timeout=5):
    out = []
    async for line in sandbox.stream_logs(container, timeout=timeout):
        out.append(line)
    return out

got = asyncio.run(collect(FakeContainer([b"zeile eins\n", b"zeile zwei\n"])))
check("streamt alle Zeilen", got == ["zeile eins", "zeile zwei"], got)

async def expect_timeout():
    try:
        await collect(FakeContainer([b"start\n"], hang=True), timeout=1)
        return False
    except asyncio.TimeoutError:
        return True
check("bricht bei Timeout ab", asyncio.run(expect_timeout()))

print("\n[5] HTTP-Routen")
client = TestClient(main.app)
r = client.get("/")
check("GET / liefert 200", r.status_code == 200, r.status_code)
check("GET / enthaelt UI", "BraunyCode" in r.text)
check("GET /static/app.css", client.get("/static/app.css").status_code == 200)
check("GET /static/app.js", client.get("/static/app.js").status_code == 200)
r = client.get("/healthz")
check("healthz meldet 503 ohne Backends", r.status_code == 503, r.status_code)
health = r.json()
check("healthz nennt Modell", health.get("model") == "qwen2.5-coder:7b")
check("healthz nennt den Anbieter", health.get("provider", {}).get("anbieter") == "ollama")
check("healthz nennt den Modus", health.get("modus") in ("auto", "tools", "oneshot"))
check("healthz verrät keinen Schlüssel", "api_key" not in r.text.lower())
# Die Oberfaeche liest genau diese Schluessel - Umbenennen ohne Nachziehen
# haette die Statusanzeige stumm kaputtgemacht.
_js = (main.STATIC_DIR / "app.js").read_text()
for key in ("modell_backend", "docker"):
    check(f"healthz liefert '{key}', wie die Oberfläche es erwartet",
          key in health and key in _js, (key in health, key in _js))

print("\n[6] PWA")
r = client.get("/manifest.json")
check("manifest erreichbar", r.status_code == 200)
mani = json.loads(r.text)
check("manifest: start_url", mani.get("start_url") == "/")
check("manifest: display standalone", mani.get("display") == "standalone")
check("manifest: scope /", mani.get("scope") == "/")
check("manifest: 192er Icon", any(i["sizes"] == "192x192" for i in mani["icons"]))
check("manifest: 512er Icon", any(i["sizes"] == "512x512" for i in mani["icons"]))
check("manifest: maskable vorhanden",
      any(i.get("purpose") == "maskable" for i in mani["icons"]))

r = client.get("/sw.js")
check("sw.js erreichbar", r.status_code == 200)
check("sw.js Scope-Header", r.headers.get("service-worker-allowed") == "/")
check("sw.js nicht gecached", r.headers.get("cache-control") == "no-store")

for name, size in [("icon-192.png", 192), ("icon-512.png", 512),
                   ("icon-maskable-512.png", 512)]:
    r = client.get(f"/icons/{name}")
    good = r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    if good:
        w, h = struct.unpack(">II", r.content[16:24])
        good = w == size and h == size
    check(f"{name} ist gueltiges {size}x{size} PNG", good, r.status_code)

check("apple-touch-icon", client.get("/apple-touch-icon.png").status_code == 200)
check("unbekanntes Icon -> 404", client.get("/icons/geheim.png").status_code == 404)
check("Pfad-Traversal -> kein 200",
      client.get("/icons/..%2f..%2fmain.py").status_code != 200)

print("\n[7] Frontend-Verdrahtung")
html = (main.STATIC_DIR / "index.html").read_text()
js = (main.STATIC_DIR / "app.js").read_text()
check("index verlinkt Manifest", 'rel="manifest" href="/manifest.json"' in html)
check("index setzt apple-touch-icon", 'rel="apple-touch-icon"' in html)
check("index setzt theme-color", 'name="theme-color" content="#1c1f24"' in html)
check("index nutzt viewport-fit=cover", "viewport-fit=cover" in html)
check("index ist standalone-faehig", 'apple-mobile-web-app-capable" content="yes"' in html)
check("app.js registriert Service Worker", "serviceWorker" in js and "'/sw.js'" in js)
check("keine innerHTML-Zuweisung im Frontend (XSS)",
      ".innerHTML" not in js, "innerHTML-Zugriff gefunden")
# Frueher stand hier eine feste Liste von sechs Bezeichnern. Die musste bei
# jedem Umbau der Oberflaeche von Hand nachgezogen werden - und genau das
# vergisst man. Jetzt werden die Ziele aus app.js selbst gelesen: was das
# Skript anspricht, muss es im HTML auch geben. Das faengt jeden kuenftigen
# Umbau mit ab, nicht nur diese sechs.
import re as _re_ui  # noqa: E402
_ziele = sorted(set(_re_ui.findall(r"\$\('([a-z0-9-]+)'\)", js)))
check("app.js spricht überhaupt Elemente an", len(_ziele) >= 6, _ziele)
_fehlend = [z for z in _ziele if f'id="{z}"' not in html]
check("jedes von app.js angesprochene Element existiert im HTML",
      not _fehlend, _fehlend)

# --- Gespraechsverlauf statt Formular -------------------------------------
#
# Die Oberflaeche war ein Auftragsformular: Textfeld oben, drei Reiter, ein
# Knopf. Jeder Lauf loeschte den vorigen. Jetzt waechst ein Verlauf mit, in
# dem Auftrag und Arbeit nebeneinander stehen bleiben.
css = (main.STATIC_DIR / "app.css").read_text()

check("es gibt einen fortlaufenden Verlauf", 'id="stream"' in html)
check("die Eingabe sitzt in einem Formular unten",
      'class="composer"' in html and 'id="composer"' in html)
check("Absenden laeuft ueber submit, nicht ueber einen Klick-Handler",
      "'submit'" in js and "preventDefault" in js)
check("das Eingabefeld wächst mit", "hoeheAnpassen" in js and "scrollHeight" in js)

# Werkzeugaufrufe gehoeren zugeklappt - im Normalfall sind sie Rauschen.
check("Werkzeugaufrufe sind aufklappbar",
      "createElement('details')" in js and "'summary'" in js)
check("die Ausgabe eines Werkzeugs steckt in dessen Klappe",
      "if (schritt)" in js)

# Der Kern des Programms sind die Belegzeilen am Ende eines Laufs. Landeten
# die in einer zugeklappten Zeile, waere die Oberflaeche huebsch und nutzlos.
_zweig = js.split("default:")[-1]
check("status-Zeilen schließen die offene Klappe, statt darin zu verschwinden",
      "schritt = null" in _zweig, _zweig[:200])

# Bildschirmtastatur: mit 100vh schiebt sie die Eingabe aus dem Bild.
check("die Höhe folgt der sichtbaren Fläche (dvh/svh)", "dvh" in css and "svh" in css)
check("die sicheren Bereiche werden beachtet", "safe-area-inset" in css)
# Unter 16px zoomen mobile Browser beim Fokus ins Feld - und kommen nicht
# wieder heraus.
check("das Eingabefeld ist mindestens 16px groß",
      "font-size: 16px" in css.split("#prompt {")[1].split("}")[0],
      css.split("#prompt {")[1].split("}")[0])
check("der Startknopf wird beim Laufen zum Abbruch",
      "body.running .send" in css)

# Beim Umbau tatsaechlich passiert und erst auf einer Bildschirmaufnahme
# aufgefallen: 'hidden' setzt display:none nur in der Browservorlage. Eine
# eigene display-Regel auf derselben Klasse ist spezifischer und gewinnt -
# Anmeldung und Verlauf lagen dauerhaft ueber der Seite. Wer 'hidden'
# benutzt, um etwas zu verstecken, muss es also selbst durchsetzen.
_versteckbar = [zeile.split("{")[0].strip()
                for zeile in css.splitlines()
                if "display:" in zeile and zeile.strip().startswith(".")]
check("verstecktes bleibt versteckt: .sheet[hidden] setzt display:none",
      ".sheet[hidden] { display: none; }" in css, _versteckbar)
for _id in ("gate", "history"):
    check(f"#{_id} startet versteckt", f'id="{_id}" hidden' in html)

print("\n[8] WebSocket-Protokoll")
def ws_exchange(payload, raw=None):
    with client.websocket_connect("/ws/agent") as ws:
        ws.send_text(raw if raw is not None else json.dumps(payload))
        return json.loads(ws.receive_text())

ev = ws_exchange(None, raw="kein json")
check("weist Nicht-JSON ab", ev["type"] == "error", ev)

ev = ws_exchange({"token": "falsch", "prompt": "x"})
check("weist falsches Token ab", ev["type"] == "error" and ev.get("code") == "auth", ev)

ev = ws_exchange({"token": "geheim-test-token", "prompt": "   "})
check("lehnt leeren Auftrag ab", ev["type"] == "error" and "Leerer" in ev["text"], ev)

ev = ws_exchange({"token": "geheim-test-token", "prompt": "baue etwas"})
check("korrektes Token passiert das Gate", ev["type"] == "status", ev)
check("erste Meldung nennt das Modell", "qwen2.5-coder:7b" in ev["text"], ev)

def ws_collect(payload, limit=10):
    """Liest bis zum done-Event. Ein Lesen darueber hinaus wuerde blockieren,
    weil der Server dann nichts mehr sendet."""
    events = []
    with client.websocket_connect("/ws/agent") as ws:
        ws.send_text(json.dumps(payload))
        for _ in range(limit):
            try:
                event = json.loads(ws.receive_text())
            except Exception:
                break
            events.append(event)
            if event["type"] == "done":
                break
    return events

events = ws_collect({"token": "geheim-test-token", "prompt": "baue etwas"})
kinds = [e["type"] for e in events]
check("meldet Fehler statt zu haengen wenn Ollama fehlt",
      "error" in kinds and "done" in kinds, kinds)
done = next((e for e in events if e["type"] == "done"), {})
check("done traegt ok=False", done.get("ok") is False, done)
check("done traegt numerische Laufzeit",
      isinstance(done.get("seconds"), (int, float)), done)
check("Laufzeit ist plausibel (keine Epoch-Zeit)",
      0 <= done.get("seconds", -1) < 600, done.get("seconds"))

print("\n[9] Selbstkorrektur-Schleife (run_agent)")

def drive_agent(*, replies, runs, max_attempts=3):
    """Fuehrt run_agent mit gescripteten Modellantworten und Sandbox-Laeufen.

    replies: Liste der ask_fn-Antworten (Plan, dann je ein Code pro Versuch).
    runs:    Liste von (exit_code, ausgabe) je Sandbox-Lauf.
    Gibt die gesammelten Ereignisse und die Zahl der Sandbox-Laeufe zurueck.
    """
    events = []
    reply_iter = iter(replies)
    run_iter = iter(runs)
    sandbox_calls = {"n": 0}

    async def send(t, text="", **extra):
        events.append({"type": t, "text": text, **extra})

    async def ask_fn(_prompt):
        return next(reply_iter)

    async def run_sandbox(files, entry="main.py"):
        sandbox_calls["n"] += 1
        return next(run_iter)

    asyncio.run(main.run_agent(send, "aufgabe", ask_fn=ask_fn,
                               run_sandbox=run_sandbox, max_attempts=max_attempts))
    return events, sandbox_calls["n"]

# Erfolg im ersten Anlauf -> kein Reparaturversuch, genau ein Sandbox-Lauf
events, runs = drive_agent(
    replies=["PLAN", "```python\nprint(1)\n```"],
    runs=[(0, "1")])
done = next(e for e in events if e["type"] == "done")
check("Erfolg beim ersten Versuch", done["ok"] is True and done["attempts"] == 1, done)
check("nur ein Sandbox-Lauf bei Erfolg", runs == 1, runs)

# Erst Fehler, dann repariert -> zwei Laeufe, Erfolg bei Versuch 2
events, runs = drive_agent(
    replies=["PLAN",
             "```python\nprint(1/0)\n```",           # Erstcode: crasht
             "```python\nprint(1)\n```"],            # Reparatur: laeuft
    runs=[(1, "Traceback (most recent call last):\nZeroDivisionError"),
          (0, "1")])
done = next(e for e in events if e["type"] == "done")
codes = [e for e in events if e["type"] == "code"]
check("repariert nach Fehler und meldet Erfolg",
      done["ok"] is True and done["attempts"] == 2, done)
check("zweiter Codestand wird geschickt", len(codes) == 2, len(codes))
check("zweiter Code traegt attempt=2", codes[1].get("attempt") == 2, codes)
check("zwei Sandbox-Laeufe", runs == 2, runs)

# Bleibt kaputt -> alle Versuche ausgeschoepft, done ok=False
events, runs = drive_agent(
    replies=["PLAN",
             "```python\nboom\n```",
             "```python\nboom\n```",
             "```python\nboom\n```"],
    runs=[(1, "NameError: boom"), (1, "NameError: boom"), (1, "NameError: boom")])
done = next(e for e in events if e["type"] == "done")
check("gibt nach max_attempts auf", done["ok"] is False and done["attempts"] == 3, done)
check("genau max_attempts Sandbox-Laeufe", runs == 3, runs)

# Exit 0, aber Traceback in der Ausgabe -> gilt trotzdem als Fehler
events, runs = drive_agent(
    replies=["PLAN",
             "```python\ntry:\n 1/0\nexcept: import traceback; traceback.print_exc()\n```",
             "```python\nprint('ok')\n```"],
    runs=[(0, "Traceback (most recent call last):\nZeroDivisionError: division by zero"),
          (0, "ok")])
done = next(e for e in events if e["type"] == "done")
check("Traceback bei Exit 0 zaehlt als Fehler und triggert Reparatur",
      done["ok"] is True and done["attempts"] == 2, done)

# Modell liefert keinen Code -> sauberer Abbruch, gar kein Sandbox-Lauf
events, runs = drive_agent(replies=["PLAN", "   "], runs=[])
done = next(e for e in events if e["type"] == "done")
check("kein Code -> Abbruch ohne Sandbox-Lauf",
      done["ok"] is False and runs == 0, (done, runs))

check("looks_failed erkennt Traceback",
      main.looks_failed("x\nTraceback (most recent call last):\ny"))
check("looks_failed erkennt sauberen Lauf nicht als Fehler",
      not main.looks_failed("Ergebnis: 42\nfertig"))

print("\n[10] Syntax-Vorpruefung")
check("syntax_error erkennt kaputten Code", main.syntax_error("def f(:\n pass"))
check("syntax_error laesst gueltigen Code durch",
      main.syntax_error("print(1)\n") is None)
check("syntax_error nennt die Zeile", "Zeile" in main.syntax_error("x = ("))

# Erster Code hat einen Syntaxfehler -> keine Sandbox fuer Versuch 1,
# Reparatur liefert gueltigen Code -> genau ein Sandbox-Lauf, Erfolg bei 2
events, runs = drive_agent(
    replies=["PLAN",
             "```python\ndef f(:\n    pass\n```",   # Syntaxfehler
             "```python\nprint('ok')\n```"],         # gueltig
    runs=[(0, "ok")])
done = next(e for e in events if e["type"] == "done")
check("Syntaxfehler wird ohne Sandbox-Start repariert",
      done["ok"] is True and done["attempts"] == 2, done)
check("nur ein echter Sandbox-Lauf trotz zwei Versuchen", runs == 1, runs)
sandbox_events = [e for e in events if e["type"] == "sandbox"]
check("Syntaxfehler taucht in der Ausgabe auf",
      any("SyntaxError" in e["text"] for e in sandbox_events), sandbox_events)

print("\n[11] Auth-Bremse gegen Token-Raten")
main._auth_fails.clear()
check("frische Adresse ist nicht gesperrt", not main.auth_blocked("1.2.3.4"))
for _ in range(main.AUTH_MAX_FAILS - 1):
    main.record_auth_fail("1.2.3.4")
check("unterhalb der Grenze noch frei", not main.auth_blocked("1.2.3.4"))
main.record_auth_fail("1.2.3.4")
check("ab der Grenze gesperrt", main.auth_blocked("1.2.3.4"))
check("andere Adresse bleibt frei", not main.auth_blocked("9.9.9.9"))
main.clear_auth_fails("1.2.3.4")
check("nach Erfolg wieder frei", not main.auth_blocked("1.2.3.4"))

# Alte Fehlversuche fallen aus dem Zeitfenster
main._auth_fails.clear()
past = 1000.0
for _ in range(main.AUTH_MAX_FAILS):
    main.record_auth_fail("5.5.5.5", now=past)
check("im Zeitfenster gesperrt", main.auth_blocked("5.5.5.5", now=past + 1))
check("nach Ablauf des Fensters wieder frei",
      not main.auth_blocked("5.5.5.5", now=past + main.AUTH_WINDOW + 1))

main._auth_fails.clear()
for _ in range(main.AUTH_MAX_FAILS * 5):
    main.record_auth_fail("7.7.7.7")
check("Speicher waechst nicht unbegrenzt",
      len(main._auth_fails["7.7.7.7"]) <= main.AUTH_MAX_FAILS,
      len(main._auth_fails["7.7.7.7"]))
main._auth_fails.clear()

print("\n[12] Auth-Bremse ueber den WebSocket")
main._auth_fails.clear()
for i in range(main.AUTH_MAX_FAILS):
    ev = ws_exchange({"token": "falsch", "prompt": "x"})
    if i == 0:
        check("erster Fehlversuch: Token abgelehnt", "abgelehnt" in ev["text"], ev)
ev = ws_exchange({"token": "falsch", "prompt": "x"})
check("nach zu vielen Fehlversuchen gesperrt",
      "Fehlversuche" in ev["text"] and ev.get("code") == "auth", ev)
# Auch mit richtigem Token bleibt die Adresse in der Sperre
ev = ws_exchange({"token": "geheim-test-token", "prompt": "x"})
check("Sperre gilt auch fuer korrektes Token",
      "Fehlversuche" in ev["text"], ev)
main._auth_fails.clear()
ev = ws_exchange({"token": "geheim-test-token", "prompt": "baue etwas"})
check("nach Zuruecksetzen wieder Zugang", ev["type"] == "status", ev)

print("\n[13] Begrenzung gleichzeitiger Laeufe")
check("Semaphore auf konfigurierten Wert gesetzt",
      main.MAX_CONCURRENT >= 1 and main.run_slots._value <= main.MAX_CONCURRENT,
      (main.MAX_CONCURRENT, main.run_slots._value))
check("Kontingent nach den Laeufen wieder frei",
      main.run_slots._value == main.MAX_CONCURRENT,
      main.run_slots._value)

async def exhaust_slots():
    """Alle Plaetze belegen, dann muss ein weiterer Auftrag abgewiesen werden."""
    for _ in range(main.MAX_CONCURRENT):
        await main.run_slots.acquire()
    try:
        main._auth_fails.clear()
        events = ws_collect({"token": "geheim-test-token", "prompt": "baue etwas"})
        return events
    finally:
        for _ in range(main.MAX_CONCURRENT):
            main.run_slots.release()

events = asyncio.run(exhaust_slots())
check("weist bei vollem Kontingent ab",
      any(e["type"] == "error" and e.get("code") == "busy" for e in events), events)
check("Abweisung endet mit done(ok=False)",
      any(e["type"] == "done" and e["ok"] is False for e in events), events)
check("Kontingent danach wieder vollstaendig frei",
      main.run_slots._value == main.MAX_CONCURRENT, main.run_slots._value)

print("\n[14] Verwaiste Container aufraeumen")
class FakeContainerObj:
    def __init__(self, name, boom=False):
        self.name, self.boom, self.removed = name, boom, False
    def remove(self, force=False):
        if self.boom:
            raise RuntimeError("weg")
        self.removed = True

class FakeDocker:
    def __init__(self, containers):
        self._containers = containers
        self.filters_used = None
    def containers_list(self, all=False, filters=None):
        self.filters_used = filters
        return self._containers

leftovers = [FakeContainerObj("a"), FakeContainerObj("b")]
fake = FakeDocker(leftovers)
class Shim:
    containers = type("C", (), {"list": staticmethod(fake.containers_list)})()
orig_client = sandbox.client
sandbox.client = lambda: Shim()
removed = sandbox.reap_orphans()
check("entfernt alle verwaisten Container", removed == 2, removed)
check("alle als entfernt markiert", all(c.removed for c in leftovers))
check("filtert nach unserem Label",
      fake.filters_used == {"label": f"{sandbox.LABEL_KEY}={sandbox.LABEL_VALUE}"},
      fake.filters_used)

# Ein sperriger Container darf die anderen nicht blockieren
mixed = [FakeContainerObj("gut"), FakeContainerObj("boese", boom=True),
         FakeContainerObj("auch-gut")]
fake2 = FakeDocker(mixed)
class Shim2:
    containers = type("C", (), {"list": staticmethod(fake2.containers_list)})()
sandbox.client = lambda: Shim2()
removed = sandbox.reap_orphans()
check("zaehlt nur erfolgreich entfernte", removed == 2, removed)
check("macht trotz Fehler weiter", mixed[2].removed)
sandbox.client = orig_client

print("\n[15] Zeitlimit fuer Modellaufrufe")
check("ASK_TIMEOUT ist gesetzt", main.ASK_TIMEOUT > 0, main.ASK_TIMEOUT)

async def slow_ask():
    """ask() muss abbrechen statt endlos zu warten."""
    orig_to_thread = asyncio.to_thread
    async def hang(*a, **kw):
        await asyncio.sleep(10)
    asyncio.to_thread = hang
    orig_timeout = main.ASK_TIMEOUT
    main.ASK_TIMEOUT = 1
    try:
        await main.ask("egal")
        return "kein Fehler"
    except TimeoutError as exc:
        return str(exc)
    except Exception as exc:
        return f"falscher Fehler: {type(exc).__name__}"
    finally:
        asyncio.to_thread = orig_to_thread
        main.ASK_TIMEOUT = orig_timeout

msg = asyncio.run(slow_ask())
check("bricht haengenden Modellaufruf ab", "nicht geantwortet" in msg, msg)

print("\n[16] Browser-neutral")
BROWSERS = ("Safari", "Chrome", "Firefox", "Edge")
# Die Oberflaeche und der Installer duerfen keinen bestimmten Browser
# voraussetzen - auf iOS teilen sich ohnehin alle dieselbe Engine.
for path, label in [(main.STATIC_DIR / "index.html", "index.html"),
                    (main.STATIC_DIR / "app.js", "app.js")]:
    text = path.read_text()
    found = [b for b in BROWSERS if b in text]
    check(f"{label} nennt keinen bestimmten Browser", not found, found)

installer = (main.BASE_DIR.parent / "install.sh")
if installer.exists():
    text = installer.read_text()
    found = [b for b in BROWSERS if b in text]
    check("install.sh nennt keinen bestimmten Browser", not found, found)

# Die PWA muss ueberall installierbar bleiben: das Manifest darf keine
# Apple-only-Annahme enthalten, sondern die Standardfelder tragen.
mani = json.loads((main.STATIC_DIR / "manifest.json").read_text())
check("Manifest ist Standard-PWA (display standalone)",
      mani.get("display") == "standalone", mani.get("display"))
check("Manifest hat start_url und scope",
      mani.get("start_url") and mani.get("scope"), mani)

html = (main.STATIC_DIR / "index.html").read_text()
check("index.html traegt sowohl Apple- als auch Standard-Metatag",
      'apple-mobile-web-app-capable' in html and 'name="mobile-web-app-capable"' in html)

print("\n[17] Deterministische Umbauten (refactor)")
import refactor  # noqa: E402
import tempfile  # noqa: E402
import workspace as ws_mod  # noqa: E402

SRC = '''import os
import json

# Kommentar
def calculate_total(items):
    return sum(items)

data = {"calculate_total": "string"}
obj.calculate_total()
f(calculate_total=1)
print(calculate_total([1]), json.dumps(data))
'''

res = refactor.rename_symbol(SRC, "calculate_total", "sum_items")
check("rename ändert Definition und Aufruf", res.changed and "def sum_items" in res.code)
check("rename lässt Strings in Ruhe", '"calculate_total"' in res.code)
check("rename lässt Attribute in Ruhe", "obj.calculate_total()" in res.code)
check("rename lässt Schlüsselwort-Argumente in Ruhe", "f(calculate_total=1)" in res.code)
check("rename zählt korrekt", "2 Vorkommen" in res.summary, res.summary)
check("rename erhält Kommentare", "# Kommentar" in res.code)
check("rename meldet, wenn nichts passt",
      not refactor.rename_symbol(SRC, "gibtsnicht", "x").changed)

res = refactor.add_docstrings(SRC)
check("docstrings werden ergänzt", res.changed and '"""Calculate total."""' in res.code)
check("docstrings nicht doppelt", not refactor.add_docstrings(res.code).changed)
check("einzeiliges def wird ausgelassen",
      not refactor.add_docstrings("def f(): pass\n").changed)

res = refactor.remove_unused_imports(SRC)
check("toter Import entfernt", "import os" not in res.code, res.code[:40])
check("genutzter Import bleibt", "import json" in res.code)
check("Stern-Import wird nicht angefasst",
      not refactor.remove_unused_imports("from x import *\n").changed)
check("__all__ wird nicht angefasst",
      not refactor.remove_unused_imports('import os\n__all__ = ["a"]\n').changed)

check("kaputter Quelltext wird gemeldet",
      "nicht parsebar" in refactor.apply(
          refactor.MechanicalTask("docstrings"), "def f(:\n").summary)

for text, kind in [("benenne foo in bar um", "rename"),
                   ("rename alpha to beta", "rename"),
                   ("füge Docstrings hinzu", "docstrings"),
                   ("entferne ungenutzte Imports", "unused_imports"),
                   ("remove unused imports", "unused_imports")]:
    task = refactor.classify(text)
    check(f"classify: {text!r} -> {kind}", task and task.kind == kind, task)
for text in ["baue mir eine Todo-App", "benenne foo in foo um", "", "   "]:
    check(f"classify: {text!r} -> kein Schnellweg", refactor.classify(text) is None)

print("\n[18] Projektverzeichnis (workspace)")
ws = ws_mod.Workspace(tempfile.mkdtemp())
ws.write("src/main.py", "print(1)\n")
check("schreibt und liest", ws.read("src/main.py") == "print(1)\n")
check("listet Dateien", ws.list_files() == ["src/main.py"], ws.list_files())
check("exists", ws.exists("src/main.py") and not ws.exists("weg.py"))

for bad in ["../../etc/passwd", "/etc/passwd", "src/../../../tmp/x", "", "   "]:
    try:
        ws.resolve(bad)
        check(f"blockiert {bad!r}", False, "durchgelassen")
    except ws_mod.WorkspaceError:
        check(f"blockiert {bad!r}", True)

outside = tempfile.mkdtemp()
open(os.path.join(outside, "secret.txt"), "w").write("geheim")
os.symlink(outside, os.path.join(ws.root, "link"))
try:
    ws.read("link/secret.txt")
    check("Symlink nach draußen blockiert", False, "durchgelassen")
except ws_mod.WorkspaceError:
    check("Symlink nach draußen blockiert", True)

check("git init", ws.git_ready())
sha = ws.git_commit("Erster Stand")
check("erster Commit", bool(sha), sha)
ws.write("src/main.py", "print(2)\n")
check("zweiter Commit", bool(ws.git_commit("Änderung")))
check("kein Leer-Commit", ws.git_commit("nichts") is None)
check("Historie lesbar", len(ws.git_log()) == 2, ws.git_log())

print("\n[19] Mechanischer Schnellweg im Agenten")

def drive_mech(intent, files, replies=None, runs=None):
    """Laesst run_agent auf einem echten Projektverzeichnis laufen."""
    w = ws_mod.Workspace(tempfile.mkdtemp())
    for name, body in files.items():
        w.write(name, body)
    w.git_commit("Start")
    events = []
    reply_iter = iter(replies or [])
    run_iter = iter(runs or [])
    calls = {"ask": 0, "sandbox": 0}

    async def send(t, text="", **extra):
        events.append({"type": t, "text": text, **extra})

    async def ask_fn(_p):
        calls["ask"] += 1
        return next(reply_iter)

    async def run_sandbox(files, entry="main.py"):
        calls["sandbox"] += 1
        return next(run_iter)

    asyncio.run(main.run_agent(send, intent, ask_fn=ask_fn,
                               run_sandbox=run_sandbox, workspace=w))
    return events, calls, w

events, calls, w = drive_mech("benenne calculate_total in sum_items um",
                              {"main.py": SRC})
done = next(e for e in events if e["type"] == "done")
check("Schnellweg meldet Erfolg", done["ok"] is True and done.get("mechanical") is True, done)
check("Schnellweg ruft KEIN Modell", calls["ask"] == 0, calls)
check("Schnellweg startet KEINE Sandbox", calls["sandbox"] == 0, calls)
check("Datei im Projekt geändert", "def sum_items" in w.read("main.py"))
check("Änderung wurde committet", len(w.git_log()) == 2, w.git_log())

events, calls, w = drive_mech("entferne ungenutzte Imports", {"main.py": SRC})
check("Importe deterministisch entfernt", "import os" not in w.read("main.py"))
check("dabei kein Modellaufruf", calls["ask"] == 0)

# Kreativer Auftrag: Schnellweg darf NICHT greifen
events, calls, w = drive_mech("baue eine Todo-Liste", {"main.py": SRC},
                              replies=["PLAN", "```python\nprint('ok')\n```"],
                              runs=[(0, "ok")])
check("kreativer Auftrag geht ans Modell", calls["ask"] == 2, calls)
check("kreativer Auftrag nutzt die Sandbox", calls["sandbox"] == 1, calls)
check("gelungener Code landet im Projekt", "print('ok')" in w.read("main.py"))

# Mehrere .py-Dateien: keine eindeutige Zieldatei -> normaler Weg
events, calls, w = drive_mech("benenne a in b um",
                              {"x.py": "a = 1\n", "y.py": "a = 2\n"},
                              replies=["PLAN", "```python\nprint('ok')\n```"],
                              runs=[(0, "ok")])
check("mehrdeutiges Ziel -> Modellweg", calls["ask"] == 2, calls)
check("Hinweis auf Mehrdeutigkeit",
      any("eindeutige Zieldatei" in e["text"] for e in events if e["type"] == "status"))

check("ohne Projektverzeichnis kein Schnellweg",
      asyncio.run(main.try_mechanical(lambda *a, **k: None, "benenne a in b um", None))
      is False)

print("\n[20] Projekt-Index und Call-Graph")
import codeindex  # noqa: E402

iws = ws_mod.Workspace(tempfile.mkdtemp())
iws.write("cart.py", '''
class Cart:
    """Ein Warenkorb."""
    def add(self, item, qty=1):
        """Legt einen Artikel hinein."""
        return calculate_total(self.items)

    def clear(self):
        self.items = []

def calculate_total(items):
    """Berechnet die Gesamtsumme."""
    return sum(items)
''')
iws.write("checkout.py", '''
from cart import calculate_total

def checkout(cart):
    """Schliesst den Kauf ab."""
    return apply_discount(calculate_total(cart.items))

def apply_discount(total):
    return total * 0.9
''')
iws.write("kaputt.py", "def f(:\n  pass\n")

idx = codeindex.CodeIndex.build(iws)
check("indiziert nur lesbare Dateien", idx.files == ["cart.py", "checkout.py"], idx.files)
check("kaputte Datei wird übersprungen, nicht geworfen",
      idx.skipped and idx.skipped[0][0] == "kaputt.py", idx.skipped)
check("findet alle Symbole", len(idx.symbols) == 6, len(idx.symbols))

names = {s.qualname for s in idx.symbols}
check("Methoden mit Klassenpräfix", "Cart.add" in names, sorted(names))
check("Modulfunktionen ohne Präfix", "calculate_total" in names)

sym = idx.find("Cart.add")[0]
check("Signatur erfasst", sym.signature == "add(self, item, qty=1)", sym.signature)
check("Docstring-Zeile erfasst", sym.doc == "Legt einen Artikel hinein.", sym.doc)

callers = {s.qualname for s in idx.callers("calculate_total")}
check("Call-Graph findet beide Aufrufer",
      callers == {"Cart.add", "checkout"}, callers)
check("Klasse zählt NICHT als Aufrufer ihrer Methoden",
      "Cart" not in callers, callers)

impact = {s.qualname for s in idx.impact("calculate_total")}
check("impact nennt betroffene Aufrufer", impact == {"Cart.add", "checkout"}, impact)
check("callees kennt aufgerufene Symbole",
      "calculate_total" in idx.callees("checkout"), idx.callees("checkout"))

rel = [s.qualname for s in idx.relevant("ändere apply_discount im checkout")]
check("Relevanz findet passende Symbole",
      "apply_discount" in rel, rel)
check("Relevanz bei leerer Aufgabe leer", idx.relevant("") == [])

ov = idx.overview()
check("Übersicht nennt Dateien und Symbole", "cart.py" in ov and "Cart.add" not in ov.split("checkout.py")[0].split("cart.py")[0], ov[:60])
check("Übersicht enthält Signaturen", "add(self, item, qty=1)" in ov)

ctx = idx.context_for("benenne calculate_total um")
check("Kontext enthält Übersicht", "Projekt:" in ctx)
check("Kontext enthält Quelltext der Fundstelle", "def calculate_total(items)" in ctx)
check("Kontext nennt Aufrufer", "Aufrufer:" in ctx, ctx[-200:])
check("Kontext bleibt kompakt (< 4000 Zeichen)", len(ctx) < 4000, len(ctx))

check("leerer Index liefert leeren Kontext",
      codeindex.CodeIndex().context_for("egal") == "")

print("\n[21] Index im Agenten")

events, calls, w = drive_mech(
    "baue eine Rabattfunktion",
    {"main.py": "def bestehende_funktion(x):\n    return x\n"},
    replies=["PLAN", "```python\nprint('ok')\n```"], runs=[(0, "ok")])
check("Agent meldet berücksichtigten Projektkontext",
      any("Projektkontext" in e["text"] for e in events if e["type"] == "status"),
      [e["text"] for e in events if e["type"] == "status"])

# Rename in main.py, aber ein anderer Modulteil nutzt den Namen weiter
events, calls, w = drive_mech(
    "benenne alt_name in neu_name um",
    {"main.py": "def alt_name(x):\n    return x\n",
     "andere.py": "from main import alt_name\n\ndef nutzer():\n    return alt_name(1)\n"})
check("Rename greift trotz zweiter Datei (main.py ist eindeutig)",
      "def neu_name" in w.read("main.py"))
check("warnt vor Aufrufern in anderen Dateien",
      any("ACHTUNG" in e["text"] for e in events if e["type"] == "error"),
      [e["text"] for e in events if e["type"] == "error"])
check("Warnung nennt die betroffene Datei",
      any("andere.py" in e["text"] for e in events if e["type"] == "error"))

events, calls, w = drive_mech(
    "benenne alt_name in neu_name um",
    {"main.py": "def alt_name(x):\n    return alt_name(x)\n"})
check("keine Warnung ohne fremde Aufrufer",
      not any("ACHTUNG" in e["text"] for e in events if e["type"] == "error"),
      [e["text"] for e in events if e["type"] == "error"])

print("\n[22] Werkzeugkasten")
import tools  # noqa: E402

sch = tools.schema()
check("vollständiger Werkzeugkasten",
      {t["function"]["name"] for t in sch} == tools.BASE_NAMES,
      sorted({t["function"]["name"] for t in sch} ^ tools.BASE_NAMES))
check("OpenAI-Format", all(t["type"] == "function" and "name" in t["function"]
                          and "parameters" in t["function"] for t in sch))
check("Pflichtfelder deklariert",
      {t["function"]["name"]: t["function"]["parameters"]["required"]
       for t in sch}["write_file"] == ["path", "content"])

tws = ws_mod.Workspace(tempfile.mkdtemp())
tws.write("main.py", "def gruss():\n    return 'hallo'\n")
tws.write("hilfe.py", "WERT = 42\n")

sandbox_calls = []
async def fake_sandbox(files, entry="main.py"):
    sandbox_calls.append((files, entry))
    return (0, "hallo")

box = tools.Toolbox(tws, run_sandbox=fake_sandbox,
                    index_builder=codeindex.CodeIndex.build)

def call(_werkzeug, **args):
    return asyncio.run(box.call(_werkzeug, args))

check("list_files nennt beide Dateien",
      set(call("list_files").split()) == {"hilfe.py", "main.py"}, call("list_files"))
check("read_file mit Zeilennummern", call("read_file", path="hilfe.py").strip()
      .startswith("1 | WERT = 42"), call("read_file", path="hilfe.py"))
check("write_file schreibt", "Geschrieben" in call("write_file", path="neu.py",
                                                   content="X = 1\n"))
check("write_file merkt sich die Datei", "neu.py" in box.written, box.written)
check("Datei liegt wirklich im Projekt", tws.read("neu.py") == "X = 1\n")
check("write_file ohne content meldet Fehler",
      call("write_file", path="a.py").startswith("FEHLER"))

check("fehlende Datei -> Fehlertext statt Absturz",
      call("read_file", path="gibtsnicht.py").startswith("FEHLER"),
      call("read_file", path="gibtsnicht.py"))
check("Pfadausbruch blockiert",
      call("read_file", path="../../etc/passwd").startswith("FEHLER"))
check("absoluter Pfad blockiert",
      call("write_file", path="/etc/passwd", content="x").startswith("FEHLER"))
check("unbekanntes Werkzeug -> Fehlertext",
      call("nicht_vorhanden").startswith("FEHLER: Unbekanntes Werkzeug"))

check("search findet Treffer", "main.py:1" in call("search", query="gruss"),
      call("search", query="gruss"))
check("search ohne Treffer meldet das",
      "Keine Treffer" in call("search", query="zzz-gibt-es-nicht"))
check("search ohne query meldet Fehler", call("search").startswith("FEHLER"))
check("outline nennt Symbole", "gruss" in call("outline"), call("outline"))
check("outline ohne Index",
      asyncio.run(tools.Toolbox(tws).call("outline", {})) == "Kein Index verfügbar.")

out = call("run_python", path="main.py")
check("run_python nutzt die Sandbox", len(sandbox_calls) == 1, sandbox_calls)
check("run_python meldet Erfolg", out.startswith("Lauf erfolgreich"), out)
check("run_python ohne Sandbox meldet Fehler",
      asyncio.run(tools.Toolbox(tws).call("run_python", {"path": "main.py"}))
      .startswith("FEHLER"))

# finish an einer frischen Toolbox: dieser Test gilt der Zusammenfassung,
# nicht der Abschluss-Sperre - die hat eigene Tests. In 'box' liegt inzwischen
# ein geschriebenes neu.py, das run_python(main.py) nie erreicht hat, und die
# Sperre greift dort zu Recht.
_frisch = tools.Toolbox(tws)
check("finish setzt die Zusammenfassung",
      asyncio.run(_frisch.call("finish", {"summary": "alles gut"})) == "alles gut"
      and _frisch.finished == "alles gut")
# Und genau der Fall, den die alte Fassung stillschweigend durchgelassen hat:
# eine geschriebene Python-Datei, die kein Lauf beruehrt hat, ist NICHT belegt.
check("nie ausgeführte Datei blockiert den Abschluss",
      "neu.py" in box.unverified, sorted(box.unverified))
check("und finish wird deshalb abgewiesen",
      call("finish", summary="alles gut").startswith("FEHLER"))

lang = tools.Toolbox(tws)
tws.write("gross.py", "x = 1\n" * 4000)
check("langes Ergebnis wird gekürzt",
      len(asyncio.run(lang.call("read_file", {"path": "gross.py"})))
      < tools.MAX_OUTPUT + 200)

check("format_result ohne ID",
      "tool_call_id" not in tools.format_result("read_file", "text"))
check("format_result mit ID",
      tools.format_result("read_file", "text", "c1")["tool_call_id"] == "c1")

check("Notnagel liest JSON-Aufruf",
      tools.parse_fallback_call('Ich mache: {"tool": "read_file", '
                                '"arguments": {"path": "main.py"}}')
      == ("read_file", {"path": "main.py"}))
check("Notnagel akzeptiert 'args'",
      tools.parse_fallback_call('{"tool": "list_files", "args": {}}')
      == ("list_files", {}))
check("Notnagel lehnt unbekanntes Werkzeug ab",
      tools.parse_fallback_call('{"tool": "rm_rf", "arguments": {}}') is None)
check("Notnagel bei Fliesstext", tools.parse_fallback_call("nur Text") is None)
check("Notnagel bei kaputtem JSON",
      tools.parse_fallback_call('{"tool": "read_file", }') is None)

print("\n[23] Agentenschleife")
import agentloop  # noqa: E402

def reply(text="", calls=()):
    return provider.Reply(text=text, tool_calls=[
        provider.ToolCall(name=n, arguments=a) for n, a in calls])

def drive_loop(script, files=None, sandbox_result=(0, "ok"), max_steps=12):
    """Laesst die Werkzeugschleife mit gescripteten Modellantworten laufen."""
    w = ws_mod.Workspace(tempfile.mkdtemp())
    for name, body in (files or {"main.py": "print('alt')\n"}).items():
        w.write(name, body)
    w.git_commit("Start")
    events, seen = [], []

    async def send(t, text="", **extra):
        events.append({"type": t, "text": text, **extra})

    async def run_sandbox(_files, _entry="main.py"):
        return sandbox_result

    steps = iter(script)
    async def chat_fn(messages, schema):
        seen.append(list(messages))
        try:
            return next(steps)
        except StopIteration:
            return reply("keine Antwort mehr")

    box = tools.Toolbox(w, run_sandbox=run_sandbox,
                        index_builder=codeindex.CodeIndex.build)
    outcome = asyncio.run(agentloop.run_tool_agent(
        send, "aufgabe", chat_fn=chat_fn, toolbox=box, max_steps=max_steps))
    return outcome, events, w, seen

GUT = "```\n```"  # nur Platzhalter, write_file bekommt echten Inhalt

outcome, events, w, seen = drive_loop([
    reply(calls=[("list_files", {})]),
    reply(calls=[("read_file", {"path": "main.py"})]),
    reply(calls=[("write_file", {"path": "main.py", "content": "print('neu')\n"})]),
    reply(calls=[("run_python", {"path": "main.py"})]),
    reply(calls=[("finish", {"summary": "Datei ersetzt und ausgeführt."})]),
])
types = [e["type"] for e in events]
check("Schleife meldet Erfolg", outcome == "ok", outcome)
check("jeder Werkzeugaufruf wird gemeldet", types.count("tool") == 5, types)
check("Werkzeugmeldung nennt Argumente",
      any("path=main.py" in e["text"] for e in events if e["type"] == "tool"))
check("geschriebene Datei erscheint im Code-Reiter",
      any(e["type"] == "code" and "print('neu')" in e["text"] for e in events))
check("Sandbox-Ausgabe erscheint",
      any(e["type"] == "sandbox" for e in events), types)
check("Datei wurde wirklich geändert", w.read("main.py") == "print('neu')\n")
check("Änderung wurde committet", len(w.git_log()) == 2, w.git_log())
done = next(e for e in events if e["type"] == "done")
check("done trägt die Zusammenfassung", "Datei ersetzt" in done["text"], done)
check("done ist als ausgeführt markiert", done.get("verified") is True, done)
check("genau ein done", types.count("done") == 1, types)
check("Verlauf enthält Werkzeugergebnisse",
      any(m.get("role") == "tool" for m in seen[-1]), seen[-1][-3:])
check("Verlauf beginnt mit Systemanweisung", seen[0][0]["role"] == "system")

# Aufruf und Ergebnis muessen im Verlauf zusammenpassen
verlauf = seen[-1]
paare = [(verlauf[i], verlauf[i + 1]) for i in range(len(verlauf) - 1)
         if verlauf[i + 1].get("role") == "tool"]
check("jedes Ergebnis folgt auf seinen Aufruf",
      all(a.get("role") == "assistant" and
          a["tool_calls"][0]["id"] == t["tool_call_id"] for a, t in paare),
      paare[:1])

# finish ohne Pruefung wird abgewiesen - und zwar im Werkzeug, nicht im Prompt.
# Erst nach FINISH_BLOCK_LIMIT Ablehnungen darf der Lauf enden, dann aber
# ausdruecklich als unbelegt.
outcome, events, w, _ = drive_loop([
    reply(calls=[("write_file", {"path": "main.py", "content": "print(1)\n"})]),
    reply(calls=[("finish", {"summary": "Fertig und getestet."})]),
    reply(calls=[("finish", {"summary": "Fertig und getestet."})]),
    reply(calls=[("finish", {"summary": "Fertig und getestet."})]),
])
fehler = [e["text"] for e in events if e["type"] == "error"]
check("erster Abschlussversuch wird abgewiesen",
      sum("Abschluss abgelehnt" in t for t in fehler) == tools.FINISH_BLOCK_LIMIT,
      fehler)
check("die Ablehnung nennt die offene Datei",
      any("main.py" in t for t in fehler if "Abschluss abgelehnt" in t), fehler)
done = next(e for e in events if e["type"] == "done")
check("Lauf endet trotzdem", outcome == "ok", outcome)
check("ohne Ausführung nicht als geprüft markiert", done.get("verified") is False, done)
check("ungeprüfte Datei wird beim Namen genannt",
      any("Ungeprüft geblieben" in t and "main.py" in t for t in fehler), fehler)

# Umgekehrt: check_syntax hebt die Sperre auf - aber nur fuer die Syntax, und
# das muss anders klingen als ein echter Lauf.
outcome, events, w, _ = drive_loop([
    reply(calls=[("write_file", {"path": "main.py", "content": "print(1)\n"})]),
    reply(calls=[("check_syntax", {"path": "main.py"})]),
    reply(calls=[("finish", {"summary": "Fertig."})]),
])
done = next(e for e in events if e["type"] == "done")
check("check_syntax hebt die Sperre auf", outcome == "ok", outcome)
check("nach check_syntax keine Ablehnung mehr",
      not any("Abschluss abgelehnt" in e["text"]
              for e in events if e["type"] == "error"), events)
check("Beleg steht im Protokoll",
      any("Belegt durch" in e["text"] and "check_syntax" in e["text"]
          for e in events if e["type"] == "status"),
      [e["text"] for e in events if e["type"] == "status"])
check("nur Syntax belegt wird als solches gemeldet",
      any("nur die Syntax" in e["text"]
          for e in events if e["type"] == "status"),
      [e["text"] for e in events if e["type"] == "status"])

# Schrittgrenze
outcome, events, w, _ = drive_loop(
    [reply(calls=[("list_files", {})]) for _ in range(10)], max_steps=3)
done = next(e for e in events if e["type"] == "done")
check("Schrittgrenze beendet die Schleife", outcome == "failed", outcome)
check("Schrittgrenze wird benannt", "Schrittgrenze" in done["text"], done["text"])
check("Schrittgrenze meldet Misserfolg", done["ok"] is False)

# Wiederholungsbremse
outcome, events, w, seen = drive_loop(
    [reply(calls=[("list_files", {})]) for _ in range(6)], max_steps=6)
check("Stupser nach mehrfach gleichem Aufruf",
      any(m.get("role") == "user" and "denselben Argumenten" in m.get("content", "")
          for m in seen[-1]), [m for m in seen[-1] if m.get("role") == "user"])

# Unbekanntes Werkzeug bricht nicht ab
outcome, events, w, _ = drive_loop([
    reply(calls=[("gibt_es_nicht", {})]),
    reply(calls=[("finish", {"summary": "trotzdem fertig"})]),
])
check("unbekanntes Werkzeug bricht nicht ab", outcome == "ok", outcome)
check("Fehler wird gemeldet",
      any("Unbekanntes Werkzeug" in e["text"] for e in events if e["type"] == "error"))

# Modell ohne Werkzeugunterstuetzung
outcome, events, w, _ = drive_loop([reply("Ich wuerde folgendes tun: ...")])
check("Modell ohne Werkzeuge wird erkannt", outcome == "no-tools", outcome)
check("dabei KEIN done gesendet",
      not any(e["type"] == "done" for e in events),
      [e["type"] for e in events])

# Erst reden, dann doch ein Werkzeug: der Stupser wirkt
outcome, events, w, seen = drive_loop([
    reply(calls=[("list_files", {})]),
    reply("Ich denke nach."),
    reply(calls=[("finish", {"summary": "doch noch"})]),
])
check("Stupser holt das Modell zurück", outcome == "ok", outcome)
check("Stupser steht im Verlauf",
      any(agentloop.NUDGE == m.get("content") for m in seen[-1]))

# Zweimal hintereinander nur Text -> Abbruch
outcome, events, w, _ = drive_loop([
    reply(calls=[("list_files", {})]),
    reply("Text eins."),
    reply("Text zwei."),
])
check("zweimal nur Text beendet den Lauf", outcome == "failed", outcome)
check("Abbruch nennt den Modelltext",
      "Text zwei" in next(e for e in events if e["type"] == "done")["text"])

# Notnagel: JSON statt echtem Werkzeugaufruf
outcome, events, w, _ = drive_loop([
    reply('{"tool": "write_file", "arguments": {"path": "main.py", '
          '"content": "print(7)\\n"}}'),
    reply(calls=[("check_syntax", {"path": "main.py"})]),
    reply(calls=[("finish", {"summary": "über JSON geschrieben"})]),
])
check("JSON-Aufruf wird ausgeführt", w.read("main.py") == "print(7)\n", w.read("main.py"))
check("JSON-Weg endet sauber", outcome == "ok", outcome)

# Modellfehler beendet sauber statt die Verbindung zu sprengen
def boom_loop():
    w = ws_mod.Workspace(tempfile.mkdtemp())
    events = []
    async def send(t, text="", **extra):
        events.append({"type": t, "text": text, **extra})
    async def chat_fn(_m, _s):
        raise RuntimeError("Modell weg")
    box = tools.Toolbox(w)
    outcome = asyncio.run(agentloop.run_tool_agent(
        send, "aufgabe", chat_fn=chat_fn, toolbox=box))
    return outcome, events

outcome, events = boom_loop()
check("Modellfehler wird gefangen", outcome == "failed", outcome)
check("Modellfehler meldet done",
      any(e["type"] == "done" and e["ok"] is False for e in events))
check("Modellfehler nennt die Ursache",
      any("Modell weg" in e["text"] for e in events if e["type"] == "error"))

# Verlaufskuerzung darf Aufruf und Ergebnis nicht trennen
lang_verlauf = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
for i in range(20):
    lang_verlauf.append({"role": "assistant", "content": "",
                         "tool_calls": [{"id": f"c{i}"}]})
    lang_verlauf.append({"role": "tool", "name": "x", "tool_call_id": f"c{i}"})
kurz = agentloop.trim(lang_verlauf, limit=10)
check("Kürzung hält die Grenze ein", len(kurz) <= 12, len(kurz))
check("Systemanweisung bleibt", kurz[0]["role"] == "system" and kurz[1]["role"] == "user")
check("kein verwaistes Werkzeugergebnis", kurz[2]["role"] == "assistant", kurz[2])
check("kurzer Verlauf bleibt unverändert",
      agentloop.trim(lang_verlauf[:6], limit=10) == lang_verlauf[:6])

print("\n[24] Anbieter und Wegwahl")

def describe_with_key():
    orig = provider.API_KEY
    provider.API_KEY = "sk-darf-nicht-auftauchen"
    try:
        return str(provider.describe())
    finally:
        provider.API_KEY = orig

beschreibung = describe_with_key()
check("describe meldet nur, DASS ein Schlüssel gesetzt ist",
      "'schluessel_gesetzt': True" in beschreibung, beschreibung)
check("describe verrät den Schlüssel nicht",
      "sk-darf-nicht-auftauchen" not in beschreibung, beschreibung)
check("health kennt den Anbieter", isinstance(provider.health(), tuple))

# API-Fehler darf den Schluessel nicht in die Meldung nehmen
def api_error_message():
    import httpx
    orig_provider, orig_base, orig_key = provider.PROVIDER, provider.API_BASE, provider.API_KEY
    provider.PROVIDER, provider.API_BASE = "openai", "https://example.invalid/v1"
    provider.API_KEY = "sk-streng-geheim-4711"
    orig_client = httpx.Client

    class FakeResponse:
        status_code = 401
        text = "Unauthorized"
    class FakeClient:
        def __init__(self, **_): pass
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def post(self, *_a, **_kw): return FakeResponse()

    httpx.Client = FakeClient
    try:
        provider.chat([{"role": "user", "content": "x"}])
        return "kein Fehler"
    except provider.ProviderError as exc:
        return str(exc)
    finally:
        httpx.Client = orig_client
        provider.PROVIDER, provider.API_BASE, provider.API_KEY = \
            orig_provider, orig_base, orig_key

msg = api_error_message()
check("API-Fehler nennt den Status", "401" in msg, msg)
check("API-Fehler enthält NICHT den Schlüssel", "sk-streng-geheim" not in msg, msg)

def drive_dispatch(intent, script, mode, files=None):
    """Laesst dispatch() laufen und zaehlt, welcher Weg genommen wurde."""
    w = ws_mod.Workspace(tempfile.mkdtemp())
    for name, body in (files or {"main.py": SRC}).items():
        w.write(name, body)
    w.git_commit("Start")
    events = []
    counts = {"ask": 0, "chat": 0, "sandbox": 0}

    async def send(t, text="", **extra):
        events.append({"type": t, "text": text, **extra})

    async def ask_fn(_p):
        counts["ask"] += 1
        return "```python\nprint('einmalwurf')\n```"

    steps = iter(script)
    async def chat_fn(_m, _s):
        counts["chat"] += 1
        return next(steps, reply("nichts mehr"))

    async def run_sandbox(_files, _entry="main.py"):
        counts["sandbox"] += 1
        return (0, "ok")

    outcome = asyncio.run(main.dispatch(send, intent, ask_fn=ask_fn, chat_fn=chat_fn,
                                        run_sandbox=run_sandbox, workspace=w, mode=mode))
    return outcome, events, counts, w

outcome, events, counts, w = drive_dispatch(
    "benenne calculate_total in sum_items um", [], "auto")
check("mechanischer Weg schlägt alles", outcome == "mechanical", outcome)
check("mechanisch ohne Modellaufruf", counts["chat"] == 0 and counts["ask"] == 0, counts)

outcome, events, counts, w = drive_dispatch("baue etwas Neues", [
    reply(calls=[("write_file", {"path": "neu.py", "content": "print('x')\n"})]),
    reply(calls=[("run_python", {"path": "neu.py"})]),
    reply(calls=[("finish", {"summary": "gebaut"})]),
], "auto")
check("kreativer Auftrag geht in die Werkzeugschleife", outcome == "ok", outcome)
check("dabei kein Einmalwurf", counts["ask"] == 0, counts)
check("Werkzeugschleife hat geschrieben", w.exists("neu.py"))

outcome, events, counts, w = drive_dispatch(
    "baue etwas Neues", [reply("nur Text")], "auto")
check("ohne Werkzeuge fällt auto auf den Einmalwurf zurück",
      outcome == "oneshot", outcome)
check("Einmalwurf nutzt ask_fn", counts["ask"] >= 2, counts)
check("Wechsel wird gemeldet",
      any("einfachen Weg" in e["text"] for e in events if e["type"] == "status"))
check("genau ein done trotz Wegwechsel",
      [e["type"] for e in events].count("done") == 1,
      [e["type"] for e in events])

outcome, events, counts, w = drive_dispatch(
    "baue etwas Neues", [reply("nur Text")], "tools")
check("Modus 'tools' wechselt NICHT", outcome == "failed", outcome)
check("Modus 'tools' erklärt den Ausweg",
      any("BRAUNY_AGENT=auto" in e["text"] for e in events if e["type"] == "done"))

outcome, events, counts, w = drive_dispatch(
    "baue etwas Neues", [reply(calls=[("finish", {"summary": "x"})])], "oneshot")
check("Modus 'oneshot' ruft die Werkzeugschleife nicht auf",
      outcome == "oneshot" and counts["chat"] == 0, (outcome, counts))

print("\n[25] Skills")
import skills as skills_mod  # noqa: E402

SKILL_TEXT = """---
name: beispiel
beschreibung: Ein Beispiel
ausloeser: alpha, beta gamma, delta
werkzeuge: read_file, finish
---
Erst lesen, dann melden.
"""

s = skills_mod.parse(SKILL_TEXT, "beispiel.md")
check("Kopf wird gelesen", s.name == "beispiel" and s.beschreibung == "Ein Beispiel", s)
check("Auslöser als Liste", s.ausloeser == ["alpha", "beta gamma", "delta"], s.ausloeser)
check("Werkzeuge als Liste", s.werkzeuge == ["read_file", "finish"], s.werkzeuge)
check("Anleitung ist der Körper", s.anleitung == "Erst lesen, dann melden.")
check("prompt enthält Anleitung und Name",
      "Erst lesen" in s.prompt() and "beispiel" in s.prompt())
check("Name fällt auf den Dateinamen zurück",
      skills_mod.parse("---\nbeschreibung: x\n---\nText", "ersatz.md").name == "ersatz")
check("ohne Kopf kein Skill", skills_mod.parse("Nur Text") is None)
check("ohne Körper kein Skill", skills_mod.parse("---\nname: x\n---\n\n") is None)
check("leerer Text kein Skill", skills_mod.parse("") is None)

echte = skills_mod.load(main.BASE_DIR.parent / "skills")
namen = {sk.name for sk in echte}
check("mitgelieferte Skills geladen", len(echte) == 5, sorted(namen))
check("Skills heißen wie erwartet",
      namen == {"tests", "bugfix", "umbau", "review", "doku"}, sorted(namen))
check("jeder Skill hat Auslöser", all(sk.ausloeser for sk in echte))
check("jeder Skill hat eine Anleitung", all(len(sk.anleitung) > 100 for sk in echte))
check("fehlendes Verzeichnis -> leere Liste",
      skills_mod.load("/gibt/es/nicht") == [])

sdir = tempfile.mkdtemp()
def schreibe(ordner, name, inhalt):
    """Mit Kontextmanager: sonst haengt der Puffer am Garbage Collector."""
    with open(os.path.join(ordner, name), "w") as fh:
        fh.write(inhalt)

schreibe(sdir, "zu_gross.md",
         "---\nname: gross\n---\n" + "x" * (skills_mod.MAX_SKILL_BYTES + 10))
schreibe(sdir, "gut.md", "---\nname: gut\n---\nText hier.")
schreibe(sdir, "keine.txt", "---\nname: nein\n---\nText.")
geladen = skills_mod.load(sdir)
check("zu große Datei wird übersprungen", [sk.name for sk in geladen] == ["gut"],
      [sk.name for sk in geladen])

check("Auslöser trifft wortweise", skills_mod.score("mach alpha fertig", s) == 1)
check("Auslöser trifft NICHT als Teilstring",
      skills_mod.score("alphabet lesen", s) == 0, skills_mod.score("alphabet lesen", s))
check("Name zählt doppelt", skills_mod.score("beispiel bauen", s) == 2)
check("leere Aufgabe ergibt 0", skills_mod.score("", s) == 0)

check("match wählt den passenden Skill",
      skills_mod.match("schreibe tests für die Funktion", echte).name == "tests")
check("match erkennt Fehlersuche",
      skills_mod.match("da ist ein bug im traceback", echte).name == "bugfix")
check("match erkennt Durchsicht",
      skills_mod.match("prüfe den code auf probleme", echte).name == "review")
check("match ohne Treffer gibt None",
      skills_mod.match("mach irgendwas völlig anderes", echte) is None,
      skills_mod.match("mach irgendwas völlig anderes", echte))
check("match ohne Skills gibt None", skills_mod.match("tests", []) is None)

review = next(sk for sk in echte if sk.name == "review")
check("review-Skill erlaubt kein Schreiben",
      "write_file" not in review.werkzeuge, review.werkzeuge)
check("review-Skill erlaubt Lesen und finish",
      "read_file" in review.werkzeuge and "finish" in review.werkzeuge)
check("overview nennt Name und Beschreibung",
      any("tests:" in z for z in skills_mod.overview(echte)),
      skills_mod.overview(echte))

# Die Werkzeugliste eines Skills ist eine harte Sperre. Kommt ein neues
# Werkzeug dazu und wird hier nicht nachgetragen, ist es unter jedem Skill
# unbenutzbar - ohne dass irgendwo ein Fehler auftaucht.
for sk in echte:
    unbekannt = [w for w in sk.werkzeuge if w not in tools.NAMES]
    check(f"Skill '{sk.name}' nennt nur existierende Werkzeuge",
          not unbekannt, unbekannt)
    check(f"Skill '{sk.name}' erlaubt finish",
          "finish" in sk.werkzeuge, sk.werkzeuge)

schreibende = {"edit_file", "write_file"}
for sk in echte:
    if sk.name == "review":
        check("review darf nichts Schreibendes",
              not (schreibende & set(sk.werkzeuge)), sk.werkzeuge)
    else:
        check(f"Skill '{sk.name}' kennt edit_file",
              "edit_file" in sk.werkzeuge, sk.werkzeuge)

print("\n[26] Konnektoren")
import connectors  # noqa: E402

def blocked(url, ips=None):
    """True, wenn check_url die Adresse abweist."""
    resolver = (lambda _h: set(ips)) if ips else connectors._resolve
    try:
        connectors.check_url(url, resolver)
        return False
    except connectors.ConnectorError:
        return True

# Der wichtigste Fall: der Metadaten-Dienst der Cloud. Wer den lesen kann,
# bekommt die Zugangsdaten der Instanz.
check("Cloud-Metadaten 169.254.169.254 blockiert",
      blocked("http://169.254.169.254/opc/v2/instance/"))
check("Loopback blockiert", blocked("http://127.0.0.1:80/"))
check("localhost blockiert", blocked("http://localhost/", ["127.0.0.1"]))
check("privates 10er-Netz blockiert", blocked("http://10.0.0.5/", ["10.0.0.5"]))
check("privates 192.168er-Netz blockiert", blocked("http://192.168.1.1/"))
check("privates 172.16er-Netz blockiert", blocked("http://172.16.0.1/"))
check("IPv6-Loopback blockiert", blocked("http://[::1]/"))
check("IPv6 unique-local blockiert", blocked("http://[fc00::1]/"))
check("0.0.0.0 blockiert", blocked("http://0.0.0.0/"))
check("file:// blockiert", blocked("file:///etc/passwd"))
check("gopher:// blockiert", blocked("gopher://example.com/"))
check("ohne Hostnamen blockiert", blocked("http:///pfad"))
check("ungewöhnlicher Port blockiert",
      blocked("http://93.184.216.34:22/", ["93.184.216.34"]))
check("öffentliche Adresse erlaubt",
      not blocked("https://93.184.216.34/", ["93.184.216.34"]))
check("Port 443 erlaubt",
      not blocked("https://93.184.216.34:443/", ["93.184.216.34"]))
# Ein Name kann auf mehrere Adressen zeigen - eine private reicht zum Sperren.
check("gemischte Auflösung wird gesperrt",
      blocked("http://beispiel.test/", ["93.184.216.34", "169.254.169.254"]))
# Klassischer Trick: alles vor dem @ ist Benutzerinfo, nicht der Host.
check("Benutzerinfo-Trick greift nicht",
      blocked("http://echte-seite.de@169.254.169.254/"))

check("to_text entfernt Tags",
      connectors.to_text("<p>Hallo <b>Welt</b></p>").replace("  ", " ").strip()
      == "Hallo Welt")
check("to_text wirft script-Blöcke weg",
      "geheim" not in connectors.to_text("<script>var geheim=1</script><p>ok</p>"))
check("to_text löst Entities auf", "&" in connectors.to_text("<p>a &amp; b</p>"))

check("safe_branch säubert", connectors.safe_branch("Mein Fix / Bug #42!")
      == "mein-fix-bug-42", connectors.safe_branch("Mein Fix / Bug #42!"))
check("safe_branch nie leer", connectors.safe_branch("###") == "arbeit")
check("safe_branch kürzt", len(connectors.safe_branch("x" * 200)) <= 60)
check("safe_branch entfernt '..' (git lehnt das ab)",
      ".." not in connectors.safe_branch("a..b"), connectors.safe_branch("a..b"))
check("safe_branch entfernt '.lock' am Ende",
      not connectors.safe_branch("fix.lock").endswith(".lock"),
      connectors.safe_branch("fix.lock"))

def with_token(fn):
    orig = connectors.GIT_TOKEN
    connectors.GIT_TOKEN = "ghp-streng-geheim-4711"
    try:
        return fn()
    finally:
        connectors.GIT_TOKEN = orig

check("scrub entfernt den Token",
      with_token(lambda: connectors.scrub("push nach https://ghp-streng-geheim-4711@x"))
      == "push nach https://***@x")
check("scrub verträgt leeren Text", connectors.scrub("") == "")

def with_remote(fn):
    orig_r, orig_t = connectors.GIT_REMOTE, connectors.GIT_TOKEN
    connectors.GIT_REMOTE = "https://github.com/nutzer/repo.git"
    connectors.GIT_TOKEN = "ghp-streng-geheim-4711"
    try:
        return fn()
    finally:
        connectors.GIT_REMOTE, connectors.GIT_TOKEN = orig_r, orig_t

beschreibung = with_remote(lambda: str(connectors.describe()))
check("describe nennt nur den Host",
      "github.com" in beschreibung and "nutzer/repo" not in beschreibung, beschreibung)
check("describe verrät den Token nicht",
      "ghp-streng-geheim" not in beschreibung, beschreibung)

check("ohne Konfiguration keine Konnektoren", connectors.available() == [],
      connectors.available())

# Echter Push gegen ein lokales bare-Repo. Kein Netz noetig, beweist aber,
# dass der Weg wirklich funktioniert - inklusive zweitem Push auf denselben
# Branch, wo --force-with-lease ohne Remote-Tracking-Ref haette scheitern
# koennen.
bare = os.path.join(tempfile.mkdtemp(), "remote.git")
subprocess.run(["git", "init", "--bare", "-q", bare], check=True)

def with_local_remote(fn):
    orig_r, orig_t = connectors.GIT_REMOTE, connectors.GIT_TOKEN
    connectors.GIT_REMOTE, connectors.GIT_TOKEN = bare, "dummy"
    try:
        return fn()
    finally:
        connectors.GIT_REMOTE, connectors.GIT_TOKEN = orig_r, orig_t

pws = ws_mod.Workspace(tempfile.mkdtemp())
pws.write("main.py", "print(1)\n")
erste = with_local_remote(lambda: connectors.git_push(pws, "Mein Fix / Bug #42",
                                                      "Erster Stand"))
check("erster Push geht durch", "brauny/mein-fix-bug-42" in erste, erste)
zweig = subprocess.run(["git", f"--git-dir={bare}", "branch", "--list"],
                       capture_output=True, text=True).stdout
check("Branch liegt wirklich im Remote", "brauny/mein-fix-bug-42" in zweig, zweig)

pws.write("main.py", "print(2)\n")
zweiter = with_local_remote(lambda: connectors.git_push(pws, "Mein Fix / Bug #42",
                                                        "Zweiter Stand"))
check("zweiter Push auf denselben Branch geht auch",
      "brauny/mein-fix-bug-42" in zweiter, zweiter)

leer_ws = ws_mod.Workspace(tempfile.mkdtemp())
try:
    with_local_remote(lambda: connectors.git_push(leer_ws, "x", ""))
    check("Push ohne Commit wird abgelehnt", False, "kein Fehler")
except connectors.ConnectorError as exc:
    check("Push ohne Commit wird abgelehnt", "keinen Commit" in str(exc), str(exc))

def ohne_remote(fn):
    """Erzwingt den unkonfigurierten Zustand.

    Ohne das wuerde dieser Test auf einer Maschine mit gesetztem
    BRAUNY_GIT_REMOTE einen ECHTEN Push auf ein fremdes Repository ausloesen.
    """
    orig_r, orig_t = connectors.GIT_REMOTE, connectors.GIT_TOKEN
    connectors.GIT_REMOTE, connectors.GIT_TOKEN = "", ""
    try:
        return fn()
    finally:
        connectors.GIT_REMOTE, connectors.GIT_TOKEN = orig_r, orig_t

try:
    ohne_remote(lambda: connectors.git_push(pws, "x", "y"))
    check("Push ohne Konfiguration wirft", False, "kein Fehler")
except connectors.ConnectorError as exc:
    check("Push ohne Konfiguration wirft", "BRAUNY_GIT_REMOTE" in str(exc), str(exc))
check("fetch abgeschaltet meldet das deutlich",
      blocked("https://93.184.216.34/") is False and
      not connectors.FETCH_ENABLED)
try:
    connectors.fetch("https://93.184.216.34/", lambda _h: {"93.184.216.34"})
    check("fetch ohne Freischaltung wirft", False, "kein Fehler")
except connectors.ConnectorError as exc:
    check("fetch ohne Freischaltung wirft", "BRAUNY_FETCH" in str(exc), str(exc))

print("\n[27] Werkzeugfreigabe und Skill-Beschränkung")

fws = ws_mod.Workspace(tempfile.mkdtemp())
fws.write("main.py", "print(1)\n")

leer = tools.Toolbox(fws)
check("ohne Konnektoren nur Basiswerkzeuge",
      {t["function"]["name"] for t in leer.schema()} == tools.BASE_NAMES,
      sorted({t["function"]["name"] for t in leer.schema()} ^ tools.BASE_NAMES))
check("schema() ohne Argument kennt keine Konnektoren",
      {t["function"]["name"] for t in tools.schema()} == tools.BASE_NAMES)
check("fetch_url nicht aufrufbar",
      asyncio.run(leer.call("fetch_url", {"url": "https://x.de"}))
      .startswith("FEHLER"))

mit = tools.Toolbox(fws, enabled=["fetch_url"])
check("freigeschalteter Konnektor erscheint",
      len(mit.schema()) == len(tools.BASE_NAMES) + 1, len(mit.schema()))
check("Konnektor steht im Schema",
      "fetch_url" in {t["function"]["name"] for t in mit.schema()})
check("nicht freigeschalteter Konnektor fehlt",
      "git_push" not in {t["function"]["name"] for t in mit.schema()})
check("unbekannter Name wird nicht freigeschaltet",
      tools.Toolbox(fws, enabled=["rm_rf"]).enabled == [])

eng = tools.Toolbox(fws)
eng.restrict(["read_file", "list_files"])
namen_eng = {t["function"]["name"] for t in eng.schema()}
check("restrict schrumpft das Schema",
      namen_eng == {"read_file", "list_files", "finish"}, namen_eng)
check("finish bleibt trotz restrict erlaubt", "finish" in eng.allowed)
check("restrict blockiert das Schreiben",
      asyncio.run(eng.call("write_file", {"path": "a.py", "content": "x"}))
      .startswith("FEHLER"))
check("Fehlermeldung erklärt den Grund",
      "nicht freigegeben" in
      asyncio.run(eng.call("write_file", {"path": "a.py", "content": "x"})))
check("erlaubtes Werkzeug geht weiter",
      not asyncio.run(eng.call("list_files", {})).startswith("FEHLER"))

weit = tools.Toolbox(fws)
weit.restrict(["fetch_url", "git_push", "read_file"])
check("restrict kann keinen Konnektor freischalten",
      "fetch_url" not in weit.allowed and "git_push" not in weit.allowed,
      sorted(weit.allowed))
check("leeres restrict ändert nichts",
      len(tools.Toolbox(fws).schema()) == len(tools.BASE_NAMES))

# Skill im Agentenlauf
def drive_skill(script, skill, files=None):
    w = ws_mod.Workspace(tempfile.mkdtemp())
    for name, body in (files or {"main.py": "print('alt')\n"}).items():
        w.write(name, body)
    w.git_commit("Start")
    events, gesehen = [], []

    async def send(t, text="", **extra):
        events.append({"type": t, "text": text, **extra})

    async def run_sandbox(_files, _entry="main.py"):
        return (0, "ok")

    steps = iter(script)
    async def chat_fn(messages, schema):
        gesehen.append({"messages": list(messages), "schema": list(schema)})
        return next(steps, reply("nichts mehr"))

    box = tools.Toolbox(w, run_sandbox=run_sandbox,
                        index_builder=codeindex.CodeIndex.build)
    outcome = asyncio.run(agentloop.run_tool_agent(
        send, "aufgabe", chat_fn=chat_fn, toolbox=box, skill=skill))
    return outcome, events, w, gesehen

outcome, events, w, gesehen = drive_skill(
    [reply(calls=[("finish", {"summary": "fertig"})])], review)
system = gesehen[0]["messages"][0]["content"]
check("Skill-Anleitung steht im Systemprompt",
      "Korrektheit" in system, system[-200:])
check("Grundanweisung bleibt erhalten", "BraunyCode" in system)
angeboten = {t["function"]["name"] for t in gesehen[0]["schema"]}
check("Skill beschränkt das angebotene Schema",
      "write_file" not in angeboten, sorted(angeboten))
check("Skill lässt Lesen zu", "read_file" in angeboten)

outcome, events, w, gesehen = drive_skill([
    reply(calls=[("write_file", {"path": "main.py", "content": "print('neu')\n"})]),
    reply(calls=[("finish", {"summary": "trotzdem"})]),
], review)
check("Schreibversuch unter review wird abgewiesen",
      w.read("main.py") == "print('alt')\n", w.read("main.py"))
check("Abweisung wird gemeldet",
      any("nicht freigegeben" in e["text"] for e in events if e["type"] == "error"),
      [e["text"] for e in events if e["type"] == "error"])
check("Lauf geht danach normal weiter", outcome == "ok", outcome)

outcome, events, counts, w = drive_dispatch("prüfe den code auf probleme", [
    reply(calls=[("finish", {"summary": "geprüft"})]),
], "auto")
check("dispatch wählt und meldet den Skill",
      any("Skill" in e["text"] and "review" in e["text"]
          for e in events if e["type"] == "status"),
      [e["text"] for e in events if e["type"] == "status"])

print("\n[28] Unbeaufsichtigte Einrichtung (cloud-init)")

CI_PATH = main.BASE_DIR.parent / "deploy" / "hetzner-cloud-init.yaml"
check("cloud-init-Datei vorhanden", CI_PATH.exists(), CI_PATH)
ci_text = CI_PATH.read_text() if CI_PATH.exists() else ""
check("beginnt mit #cloud-config (sonst ignoriert cloud-init sie)",
      ci_text.startswith("#cloud-config"), ci_text[:30])

try:
    import yaml  # noqa: E402
except ImportError:
    yaml = None
    # Kein check(): eine fehlende optionale Abhaengigkeit ist kein Defekt im
    # Code und darf die Fehlerzahl nicht erhoehen.
    print("  ÜBERSPR.  PyYAML fehlt — cloud-init-YAML wird nicht geprüft.")

if yaml and ci_text:
    ci = yaml.safe_load(ci_text)
    check("YAML ist gültig", isinstance(ci, dict), type(ci))
    dateien = {f["path"]: f["content"] for f in ci.get("write_files", [])}
    check("Konfigurationsdatei wird angelegt", "/etc/braunycode.setup" in dateien)
    check("Einrichtungsskript wird angelegt",
          "/usr/local/bin/braunycode-setup.sh" in dateien)

    conf = dateien.get("/etc/braunycode.setup", "")
    setup = dateien.get("/usr/local/bin/braunycode-setup.sh", "")

    # Der Platzhalter muss in BEIDEN Dateien wortgleich stehen. Driften sie
    # auseinander, liefe die Installation mit dem Standard-Passwort durch -
    # ein oeffentlich erreichbarer Dienst mit bekanntem Token.
    import re as _re
    platzhalter = _re.search(r"BRAUNY_TOKEN=(\S+)", conf)
    check("Platzhalter-Passwort steht in der Konfiguration", bool(platzhalter), conf[:200])
    if platzhalter:
        check("Abbruch prüft genau diesen Platzhalter",
              platzhalter.group(1) in setup, platzhalter.group(1))
    check("Abbruch prüft auch die Mindestlänge",
          "-lt 12" in setup, setup[:400])

    # Der Deadlock, der die Einrichtung sonst haengen laesst.
    check("cloud-init-Warteschleife wird abgeschaltet",
          "BRAUNY_SKIP_CLOUDINIT_WAIT=1" in setup, setup[:600])
    check("Installer kennt den Schalter",
          "BRAUNY_SKIP_CLOUDINIT_WAIT" in (main.BASE_DIR.parent / "install.sh").read_text())

    # Werte duerfen nicht in eine Kommandozeile eingesetzt werden - ein
    # Passwort mit Anfuehrungszeichen wuerde die Zeile zerlegen.
    check("Werte werden über env übergeben, nicht interpoliert",
          "runuser -u brauny -- env" in setup, setup[-500:])
    check("HOME wird gesetzt (runuser tut das nicht)", "HOME=/home/brauny" in setup)

    benutzer = ci.get("users", [])
    gruppen = benutzer[0].get("groups", []) if benutzer else []
    # docker existiert beim Anlegen des Benutzers noch nicht.
    check("Benutzer wird NICHT in die docker-Gruppe gelegt",
          "docker" not in gruppen, gruppen)
    check("Benutzer bekommt sudo", "sudo" in gruppen, gruppen)

print("\n[29] HTTPS-Einrichtung")

HTTPS_PATH = main.BASE_DIR.parent / "deploy" / "enable-https.sh"
check("enable-https.sh vorhanden", HTTPS_PATH.exists(), HTTPS_PATH)
https_text = HTTPS_PATH.read_text() if HTTPS_PATH.exists() else ""

check("enable-https.sh ist syntaktisch gültig",
      subprocess.run(["bash", "-n", str(HTTPS_PATH)],
                     capture_output=True).returncode == 0)

# sslip.io/nip.io stehen NICHT auf der Public Suffix List. Let's Encrypt
# zaehlt die ganze Domain als eine einzige mit 50 Zertifikaten pro Woche,
# geteilt mit allen Nutzern weltweit - die Ausstellung wuerde fast immer
# scheitern. Wer das hier spaeter "vereinfachen" will, faellt genau darauf
# herein.
check("baut NICHT auf sslip.io/nip.io",
      "sslip.io" not in https_text.split("# WARUM")[-1].split("set -euo")[0]
      or "NICHT auf der Public Suffix List" in https_text, "Begründung fehlt")
check("Begründung gegen sslip.io steht im Skript",
      "Public Suffix List" in https_text)
check("reverse_proxy im Caddyfile (WebSocket läuft darüber)",
      "reverse_proxy" in https_text)
check("Caddyfile wird vor dem Neustart geprüft",
      "caddy validate" in https_text)
check("Domain wird gegen die Server-IP geprüft",
      "api.ipify.org" in https_text and "getent hosts" in https_text)
check("Aufruf ohne Domain wird abgelehnt",
      "Aufruf: sudo bash enable-https.sh" in https_text)
check("http:// im Argument wird abgefangen", "http://*|https://*)" in https_text)

if yaml and ci_text:
    check("cloud-init kennt BRAUNY_DOMAIN", "BRAUNY_DOMAIN=" in conf, conf[-300:])
    check("BRAUNY_DOMAIN ist standardmäßig leer (nur HTTP)",
          _re.search(r"^BRAUNY_DOMAIN=\s*$", conf, _re.M) is not None, conf[-200:])
    check("cloud-init ruft enable-https.sh auf", "enable-https.sh" in setup)
    # Ein Fehlschlag beim Zertifikat darf die ganze Einrichtung nicht kippen.
    check("HTTPS-Fehlschlag ist nicht tödlich",
          "|| echo \"HTTPS fehlgeschlagen" in setup, setup[-800:])
    # Bei leerer Domain waere ein '[ -n ... ] && echo' der letzte Befehl und
    # das Skript endete mit Status 1 - ein Fehlalarm.
    check("Einrichtungsskript endet ausdrücklich mit exit 0",
          setup.rstrip().endswith("exit 0"), setup[-120:])

installer = (main.BASE_DIR.parent / "install.sh").read_text()
check("Installer übernimmt ein vorgegebenes Token",
      'if [ -n "${BRAUNY_TOKEN:-}" ]' in installer)
check("Installer lehnt zu kurze Token ab",
      '"${#TOKEN}" -ge 12' in installer, )
# Die Zusicherung lautet nicht mehr "bricht als root ab", sondern "der Agent
# landet nie als root". Contabo und Hetzner geben ueberhaupt nur root heraus -
# ein Abbruch haette dort jede Installation unmoeglich gemacht. Geprueft wird
# jetzt der Ausweg: Benutzer anlegen und sich als dieser neu starten.
check("Installer erkennt einen Start als root",
      '[ "$(id -u)" -eq 0 ]' in installer)
check("und startet sich als unprivilegierter Benutzer neu",
      'exec sudo -u "$BRAUNY_USER"' in installer)
check("der Agent selbst laeuft also nie als root",
      'useradd -m -s /bin/bash "$BRAUNY_USER"' in installer)
# Ohne diese Sperre startet BRAUNY_USER=root sich selbst endlos neu.
check("BRAUNY_USER=root wird abgelehnt",
      '[ "$BRAUNY_USER" != "root" ]' in installer)
# Eine kaputte sudoers-Datei sperrt den Benutzer dauerhaft aus - deshalb wird
# sie geprueft, bevor sie zaehlt.
check("die sudoers-Datei wird vor dem Scharfschalten geprüft",
      'visudo -cf' in installer)
# sudo raeumt die Umgebung ab; ohne Weitergabe liefe der zweite Durchgang mit
# anderen Vorgaben als der erste.
check("gesetzte BRAUNY_*-Variablen überleben den Neustart",
      'FORWARD+=("$v=${!v}")' in installer
      and 'BRAUNY_MODEL BRAUNY_HOME' in installer)

# Das Modell darf nicht fest verdrahtet sein: dieselbe Datei laeuft auf 8 GB
# und auf 24 GB, und ein zu grosses Modell wird beim ersten Aufruf beendet.
check("das Modell wird am vorhandenen RAM gewählt",
      "/proc/meminfo" in installer and "qwen3-coder:30b" in installer)
check("die kleineren Modelle bleiben als Rückfall erhalten",
      "qwen2.5-coder:14b" in installer and "qwen2.5-coder:7b" in installer)
check("eine ausdrückliche Vorgabe schlägt die Erkennung",
      'if [ -z "${BRAUNY_MODEL:-}" ]' in installer)
# 19 GB Modell auf 24 GB Maschine: ohne Puffer beendet der OOM-Killer ollama
# mitten in einer Antwort.
check("es wird eine Auslagerungsdatei angelegt",
      "mkswap" in installer and "swapon" in installer)
check("die Auslagerungsdatei überlebt den Neustart",
      "/etc/fstab" in installer)
check("vorhandener Swap wird nicht verdoppelt",
      "SwapTotal" in installer)

print("\n[30] Mehrdateiige Projekte in der Sandbox")

# Der Kern: frueher ging nur EINE Datei in den Container und es lief immer
# fest /app/main.py. Ein Projekt aus mehreren Modulen war damit nicht
# ausfuehrbar - obwohl genau das das Versprechen der Werkzeugschleife ist.

check("safe_relpath behält Unterverzeichnisse",
      sandbox.safe_relpath("pkg/mod.py") == os.path.join("pkg", "mod.py"),
      sandbox.safe_relpath("pkg/mod.py"))
check("safe_relpath wirft '..' weg",
      sandbox.safe_relpath("../../evil.py") == "evil.py")
# Ein absoluter Pfad faellt bewusst auf den blossen Dateinamen zurueck,
# statt eine etc/-Struktur im Projekt nachzubauen.
check("absoluter Pfad wird auf den Dateinamen reduziert",
      sandbox.safe_relpath("/etc/passwd") == "passwd",
      sandbox.safe_relpath("/etc/passwd"))
check("safe_relpath verträgt Backslashes",
      sandbox.safe_relpath("pkg\\mod.py") == os.path.join("pkg", "mod.py"))
check("safe_relpath nie leer", sandbox.safe_relpath("") == "datei.py")

md = sandbox.make_project_dir({
    "main.py": "from pkg.helfer import wert\nprint(wert)\n",
    "pkg/helfer.py": "wert = 7\n",
    "pkg/__init__.py": "",
})
check("Unterverzeichnis wird angelegt",
      os.path.isfile(os.path.join(md, "pkg", "helfer.py")))
check("Hauptdatei liegt richtig", os.path.isfile(os.path.join(md, "main.py")))
check("Unterverzeichnis ist für den Sandbox-User betretbar",
      os.stat(os.path.join(md, "pkg")).st_mode & 0o001 != 0,
      oct(os.stat(os.path.join(md, "pkg")).st_mode))
shutil.rmtree(md, ignore_errors=True)

check("entry_command startet die gewünschte Datei",
      sandbox.entry_command("pkg/start.py")[-1] == "/app/pkg/start.py",
      sandbox.entry_command("pkg/start.py"))
check("entry_command ohne Angabe nimmt main.py",
      sandbox.entry_command()[-1] == "/app/main.py")
check("entry_command lässt sich nicht aus /app herauslocken",
      sandbox.entry_command("../../etc/passwd")[-1] == "/app/passwd",
      sandbox.entry_command("../../etc/passwd"))

# Und jetzt der eigentliche Regressionstest: ein Projekt, dessen Hauptdatei
# ein zweites Modul importiert.
mws = ws_mod.Workspace(tempfile.mkdtemp())
mws.write("main.py", "from helfer import wert\nprint(wert)\n")
mws.write("helfer.py", "wert = 7\n")
mws.write("notizen.txt", "kein Code\n")

uebergeben = {}
async def merk_sandbox(files, entry="main.py", *, command=None):
    uebergeben["files"] = files
    uebergeben["entry"] = entry
    uebergeben["command"] = command
    return (0, "7")

mbox = tools.Toolbox(mws, run_sandbox=merk_sandbox)
ergebnis = asyncio.run(mbox.call("run_python", {"path": "main.py"}))
check("run_python meldet Erfolg", ergebnis.startswith("Lauf erfolgreich"), ergebnis)
check("importiertes Modul geht mit in den Container",
      "helfer.py" in uebergeben["files"], sorted(uebergeben.get("files", {})))
check("Hauptdatei geht mit", "main.py" in uebergeben["files"])
check("Einstiegspunkt ist die angeforderte Datei",
      uebergeben["entry"] == "main.py", uebergeben.get("entry"))
check("Ergebnis nennt die Dateianzahl",
      "Datei(en) im Container" in ergebnis, ergebnis)

# Eine andere Datei starten als main.py - frueher unmoeglich.
asyncio.run(mbox.call("run_python", {"path": "helfer.py"}))
check("beliebige Datei als Einstiegspunkt",
      uebergeben["entry"] == "helfer.py", uebergeben.get("entry"))

check("fehlende Datei meldet Fehler statt zu laufen",
      asyncio.run(mbox.call("run_python", {"path": "gibtsnicht.py"}))
      .startswith("FEHLER"))

# Deckel: ein riesiges Projekt darf den Container-Start nicht sprengen.
gws = ws_mod.Workspace(tempfile.mkdtemp())
for i in range(tools.MAX_SANDBOX_FILES + 20):
    gws.write(f"m{i}.py", f"x = {i}\n")
gbox = tools.Toolbox(gws, run_sandbox=merk_sandbox)
asyncio.run(gbox.call("run_python", {"path": "m0.py"}))
check("Dateizahl ist gedeckelt",
      len(uebergeben["files"]) <= tools.MAX_SANDBOX_FILES + 1,
      len(uebergeben["files"]))
check("Einstiegsdatei ist trotz Deckel dabei",
      "m0.py" in uebergeben["files"])

# Der Einmalwurf schickt weiterhin genau eine Datei.
einzel = {}
async def einzel_sandbox(files, entry="main.py"):
    einzel.update({"files": files, "entry": entry})
    return (0, "ok")

async def einzel_ask(_p):
    return "```python\nprint('x')\n```"

async def stumm(_t, _text="", **_kw):
    return None

asyncio.run(main.run_agent(stumm, "aufgabe", ask_fn=einzel_ask,
                           run_sandbox=einzel_sandbox, max_attempts=1))
check("Einmalwurf schickt genau eine Datei",
      list(einzel["files"]) == ["main.py"], einzel.get("files"))
check("Einmalwurf startet main.py", einzel["entry"] == "main.py")

print("\n[31] Bearbeitungswerkzeuge")

ews = ws_mod.Workspace(tempfile.mkdtemp())
ews.write("main.py", "def gruss(name):\n    return f'hallo {name}'\n\n"
                     "print(gruss('welt'))\n")
ews.write("hilfe.py", "from main import gruss\n\ndef zweimal(n):\n"
                      "    return gruss(n) + gruss(n)\n")
ews.write("notiz.md", "# Notizen\nnichts\n")
ews.git_commit("Start")

ebox = tools.Toolbox(ews, run_sandbox=merk_sandbox,
                     index_builder=codeindex.CodeIndex.build)

def ecall(_werkzeug, **args):
    """Erster Parameter unterstrichen: sonst kollidiert er mit dem
    Argument 'name' von symbol_info."""
    return asyncio.run(ebox.call(_werkzeug, args))

# ---- edit_file: der wichtigste Neuzugang
r = ecall("edit_file", path="main.py", old_text="hallo", new_text="moin")
check("edit_file ersetzt", "1 Stelle ersetzt" in r, r)
check("Datei wirklich geändert", "moin" in ews.read("main.py"))
check("Rest der Datei bleibt", "def gruss(name)" in ews.read("main.py"))

r = ecall("edit_file", path="main.py", old_text="gibtsnichtimtext", new_text="x")
check("fehlender Text meldet Fehler", r.startswith("FEHLER"), r)
check("Fehler erklärt, was zu tun ist", "read_file" in r, r)

r = ecall("edit_file", path="hilfe.py", old_text="gruss(n)", new_text="gruesse(n)")
check("mehrdeutige Stelle wird abgelehnt", r.startswith("FEHLER") and "2-mal" in r, r)
check("Datei bei Mehrdeutigkeit unverändert", "gruesse" not in ews.read("hilfe.py"))

r = ecall("edit_file", path="hilfe.py", old_text="gruss(n)",
          new_text="gruesse(n)", replace_all=True)
check("replace_all ersetzt alle", "2 Stellen ersetzt" in r, r)
check("beide Vorkommen ersetzt", ews.read("hilfe.py").count("gruesse(n)") == 2)

check("identischer Text wird abgelehnt",
      ecall("edit_file", path="main.py", old_text="a", new_text="a")
      .startswith("FEHLER"))
check("leeres old_text wird abgelehnt",
      ecall("edit_file", path="main.py", old_text="", new_text="x")
      .startswith("FEHLER"))
check("edit_file kommt nicht aus dem Projekt heraus",
      ecall("edit_file", path="../fremd.py", old_text="a", new_text="b")
      .startswith("FEHLER"))

# ---- read_file abschnittsweise
ews.write("lang.py", "\n".join(f"zeile{i}" for i in range(1, 101)) + "\n")
r = ecall("read_file", path="lang.py", offset=10, limit=5)
check("read_file liest Ausschnitt", "zeile10" in r and "zeile14" in r, r[:80])
check("Ausschnitt endet wo er soll", "zeile15" not in r)
check("Ausschnitt nennt den Gesamtumfang", "von 100" in r, r[-60:])
check("read_file ohne Angaben liest alles",
      "zeile100" in ecall("read_file", path="lang.py"))

# ---- glob
check("glob findet nach Endung",
      set(ecall("glob", pattern="*.py").split()) ==
      {"hilfe.py", "lang.py", "main.py"}, ecall("glob", pattern="*.py"))
check("glob ohne Treffer meldet das",
      "Keine Datei" in ecall("glob", pattern="*.rs"))
check("glob ohne Muster meldet Fehler", ecall("glob").startswith("FEHLER"))

# ---- search: regex, glob-Filter, Kontext
check("search mit regulärem Ausdruck",
      "main.py" in ecall("search", query=r"def \w+\(", regex=True),
      ecall("search", query=r"def \w+\(", regex=True))
check("kaputter Ausdruck meldet Fehler",
      ecall("search", query="[unklosed", regex=True).startswith("FEHLER"))
check("search filtert über glob",
      "notiz.md" not in ecall("search", query="Notizen", glob="*.py"))
check("search findet ohne Filter",
      "notiz.md" in ecall("search", query="Notizen"))
check("search mit Kontext liefert Nachbarzeilen",
      ecall("search", query="return f", context=1).count("main.py:") >= 2,
      ecall("search", query="return f", context=1))

# ---- symbol_info
r = ecall("symbol_info", name="gruss")
check("symbol_info nennt die Signatur", "gruss(name)" in r, r)
check("symbol_info nennt die Fundstelle", "main.py:1" in r, r)
check("symbol_info nennt Aufrufer", "Aufrufer:" in r, r)
check("unbekanntes Symbol meldet das",
      "Kein Symbol" in ecall("symbol_info", name="gibtsnicht"))
check("symbol_info ohne Namen meldet Fehler",
      ecall("symbol_info").startswith("FEHLER"))

# ---- check_syntax
check("check_syntax bestätigt gültigen Code",
      "in Ordnung" in ecall("check_syntax", path="main.py"),
      ecall("check_syntax", path="main.py"))
ews.write("kaputt.py", "def f(:\n    pass\n")
r = ecall("check_syntax", path="kaputt.py")
check("check_syntax findet den Fehler", "SyntaxError" in r, r)
check("check_syntax nennt die Zeile", "Zeile 1" in r, r)

# ---- delete_file / move_file
check("move_file benennt um",
      "notiz.md → doku/notiz.md" in ecall("move_file", source="notiz.md",
                                          destination="doku/notiz.md"))
check("Datei liegt am neuen Ort", ews.exists("doku/notiz.md"))
check("alter Ort ist leer", not ews.exists("notiz.md"))
check("move auf bestehendes Ziel wird abgelehnt",
      ecall("move_file", source="doku/notiz.md", destination="main.py")
      .startswith("FEHLER"))
check("move einer fehlenden Datei meldet Fehler",
      ecall("move_file", source="weg.md", destination="x.md").startswith("FEHLER"))

check("delete_file löscht", "Gelöscht" in ecall("delete_file", path="doku/notiz.md"))
check("Datei ist weg", not ews.exists("doku/notiz.md"))
check("leer gewordenes Verzeichnis wird aufgeräumt",
      not (ews.root / "doku").exists())
check("delete einer fehlenden Datei meldet Fehler",
      ecall("delete_file", path="weg.md").startswith("FEHLER"))

# ---- rename_symbol: über den Syntaxbaum, nicht per Textersetzung
rws = ws_mod.Workspace(tempfile.mkdtemp())
rws.write("main.py", 'def berechne(x):\n    """Doku."""\n    return x\n\n'
                     'wert = berechne(1)\n'
                     'text = "berechne"\n'
                     'obj.berechne()\n')
rws.write("andere.py", "from main import berechne\n\ndef nutzer():\n"
                       "    return berechne(2)\n")
rws.git_commit("Start")
rbox = tools.Toolbox(rws, index_builder=codeindex.CodeIndex.build)
r = asyncio.run(rbox.call("rename_symbol",
                          {"path": "main.py", "old_name": "berechne",
                           "new_name": "rechne"}))
neu = rws.read("main.py")
check("rename_symbol benennt Definition um", "def rechne(x)" in neu, neu)
check("rename_symbol benennt Aufruf um", "rechne(1)" in neu)
check("Zeichenkette bleibt unangetastet", '"berechne"' in neu, neu)
check("fremdes Attribut bleibt unangetastet", "obj.berechne()" in neu, neu)
check("Kommentar/Docstring unversehrt", '"""Doku."""' in neu)
check("warnt vor Aufrufern in anderen Dateien",
      "ACHTUNG" in r and "andere.py" in r, r)
check("ungültiger Bezeichner wird abgelehnt",
      asyncio.run(rbox.call("rename_symbol",
                            {"path": "main.py", "old_name": "rechne",
                             "new_name": "2ungueltig"})).startswith("FEHLER"))

# ---- undo: der Rückwärtsgang
uws = ws_mod.Workspace(tempfile.mkdtemp())
uws.write("main.py", "print('original')\n")
uws.git_commit("Start")
ubox = tools.Toolbox(uws)
asyncio.run(ubox.call("write_file", {"path": "main.py", "content": "kaputt(\n"}))
asyncio.run(ubox.call("write_file", {"path": "neu.py", "content": "x=1\n"}))
check("Änderung ist erst mal da", "kaputt" in uws.read("main.py"))
r = asyncio.run(ubox.call("undo", {}))
check("undo meldet den Commit", "zurückgesetzt" in r, r)
check("geänderte Datei ist wiederhergestellt",
      uws.read("main.py") == "print('original')\n", uws.read("main.py"))
check("neu angelegte Datei ist weg", not uws.exists("neu.py"))
check("undo leert die Liste der Änderungen", ubox.written == [], ubox.written)

leerws = ws_mod.Workspace(tempfile.mkdtemp())
check("undo ohne Historie meldet Fehler",
      asyncio.run(tools.Toolbox(leerws).call("undo", {})).startswith("FEHLER"))

# ---- run_command
cbox = tools.Toolbox(ews, run_sandbox=merk_sandbox)
r = asyncio.run(cbox.call("run_command", {"command": "python -m unittest -v"}))
check("run_command läuft über die Sandbox",
      r.startswith("Befehl erfolgreich"), r)
check("run_command ohne Befehl meldet Fehler",
      asyncio.run(cbox.call("run_command", {"command": ""})).startswith("FEHLER"))
check("run_command ohne Sandbox meldet Fehler",
      asyncio.run(tools.Toolbox(ews).call("run_command", {"command": "ls"}))
      .startswith("FEHLER"))
check("unlesbarer Befehl meldet Fehler",
      asyncio.run(cbox.call("run_command", {"command": 'python -c "unbalanced'}))
      .startswith("FEHLER"))

# Kein Shell-Aufruf: '&&' ist ein gewoehnliches Argument, keine Verkettung.
befehle = {}
async def merk_command(files, entry=None, *, command=None):
    befehle["command"] = command
    return (0, "ok")
sbox = tools.Toolbox(ews, run_sandbox=merk_command)
asyncio.run(sbox.call("run_command", {"command": "ls && rm -rf /"}))
check("Befehl wird in Argumente zerlegt, nicht an eine Shell gegeben",
      befehle["command"] == ["ls", "&&", "rm", "-rf", "/"], befehle["command"])

# ---- Hilfsfunktionen
check("_passt vergleicht ohne '/' den Dateinamen", tools._passt("a/b/c.py", "*.py"))
check("_passt vergleicht mit '/' den ganzen Pfad",
      tools._passt("src/x.py", "src/*.py") and not tools._passt("a/x.py", "src/*.py"))
check("_als_zahl verträgt Text von Modellen", tools._als_zahl("12", 0) == 12)
check("_als_zahl fällt bei Unsinn zurück", tools._als_zahl("viele", 7) == 7)

print("\n[32] Befunde aus der Durchsicht")

# 1. sys.path in der Sandbox. 'python /app/pkg/start.py' setzt sys.path[0] auf
#    /app/pkg, nicht auf /app - ein Import aus dem Projektstamm scheitert dann.
#    Der Mehrdatei-Lauf ging nur zufaellig, solange die Startdatei oben lag.
import inspect  # noqa: E402
quelle_start = inspect.getsource(sandbox.start)
check("Sandbox setzt PYTHONPATH=/app",
      '"PYTHONPATH": "/app"' in quelle_start, quelle_start[-300:])

# Und der Nachweis, dass es ohne PYTHONPATH wirklich bricht:
tiefes = tempfile.mkdtemp()
os.makedirs(os.path.join(tiefes, "pkg"))
schreibe(tiefes, "helfer.py", "wert = 7\n")
schreibe(os.path.join(tiefes, "pkg"), "start.py",
         "import helfer\nprint(helfer.wert)\n")
ohne = subprocess.run([sys.executable, os.path.join(tiefes, "pkg", "start.py")],
                      capture_output=True, text=True)
mit = subprocess.run([sys.executable, os.path.join(tiefes, "pkg", "start.py")],
                     capture_output=True, text=True,
                     env={**os.environ, "PYTHONPATH": tiefes})
check("ohne PYTHONPATH scheitert der Import wirklich",
      "ModuleNotFoundError" in ohne.stderr, ohne.stderr[-120:])
check("mit PYTHONPATH klappt er", mit.stdout.strip() == "7", mit.stdout)

# 2. Ein Skill in Latin-1 darf den Dienst nicht am Start hindern.
kaputt_dir = tempfile.mkdtemp()
with open(os.path.join(kaputt_dir, "latin.md"), "wb") as fh:
    fh.write("---\nname: latin\nbeschreibung: gr\xfc\xdfe\n---\nText.\n"
             .encode("latin-1"))
schreibe(kaputt_dir, "gut.md", "---\nname: gut\nausloeser: x\n---\nText hier.\n")
try:
    geladen = skills_mod.load(kaputt_dir)
    check("Latin-1-Skill legt den Start nicht lahm", True)
    check("die anderen Skills werden trotzdem geladen",
          any(s.name == "gut" for s in geladen), [s.name for s in geladen])
except Exception as exc:
    check("Latin-1-Skill legt den Start nicht lahm", False, f"{type(exc).__name__}: {exc}")

# 3. restrict darf konfigurierte Konnektoren nicht wegnehmen.
kbox = tools.Toolbox(fws, enabled=["git_push", "fetch_url"])
kbox.restrict(["read_file", "write_file", "finish"])   # nennt keinen Konnektor
check("Skill ohne Konnektor-Angabe behält die konfigurierten",
      {"git_push", "fetch_url"} <= kbox.allowed, sorted(kbox.allowed))
check("Skill schränkt die Basiswerkzeuge trotzdem ein",
      "delete_file" not in kbox.allowed, sorted(kbox.allowed))

kbox2 = tools.Toolbox(fws, enabled=["git_push", "fetch_url"])
kbox2.restrict(["read_file", "git_push", "finish"])    # nennt einen Konnektor
check("nennt der Skill einen Konnektor, gilt genau seine Liste",
      "git_push" in kbox2.allowed and "fetch_url" not in kbox2.allowed,
      sorted(kbox2.allowed))

# 4. Ein Tippfehler im Skill darf den Agenten nicht handlungsunfähig machen.
tbox = tools.Toolbox(fws)
verworfen = tbox.restrict(["raed_file", "wrtie_file"])   # beides Tippfehler
check("unbekannte Werkzeuge werden gemeldet",
      verworfen == ["raed_file", "wrtie_file"], verworfen)
check("bei nur Tippfehlern wird gar nicht eingeschränkt",
      len(tbox.schema()) == len(tools.BASE_NAMES), len(tbox.schema()))
check("der Agent bleibt handlungsfähig", "read_file" in tbox.allowed)

tbox2 = tools.Toolbox(fws)
uebrig = tbox2.restrict(["read_file", "gibtsnicht"])
check("gültige Namen greifen trotz Tippfehler daneben",
      tbox2.allowed == {"read_file", "finish"}, sorted(tbox2.allowed))
check("der Tippfehler wird dabei gemeldet", uebrig == ["gibtsnicht"], uebrig)

# 6. edit_file darf eine Nicht-UTF-8-Datei nicht stillschweigend zerstören.
bws = ws_mod.Workspace(tempfile.mkdtemp())
with open(bws.root / "latin.txt", "wb") as fh:
    fh.write("caf\xe9 und stra\xdfe\n".encode("latin-1"))
vorher = (bws.root / "latin.txt").read_bytes()
bbox = tools.Toolbox(bws)
r = asyncio.run(bbox.call("edit_file", {"path": "latin.txt",
                                        "old_text": "und", "new_text": "oder"}))
check("edit_file lehnt Nicht-UTF-8 ab", r.startswith("FEHLER") and "UTF-8" in r, r)
check("die Datei bleibt dabei unangetastet",
      (bws.root / "latin.txt").read_bytes() == vorher)

# 7. read_file hinter dem Dateiende.
bws.write("kurz.py", "eins\nzwei\n")
r = asyncio.run(bbox.call("read_file", {"path": "kurz.py", "offset": 20}))
check("Offset hinter Dateiende wird verständlich gemeldet",
      "nur 2 Zeile" in r, r)
check("kein umgedrehter Bereich mehr", "20–19" not in r, r)

# 5. DNS-Rebinding: die Vorabprüfung allein reicht nicht, weil httpx danach
#    selbst noch einmal auflöst. Die Antwort wird deshalb erst freigegeben,
#    wenn auch die tatsächliche Gegenstelle öffentlich ist.
class FakeStream:
    def __init__(self, adresse): self._a = adresse
    def get_extra_info(self, name):
        return (self._a, 443) if name == "server_addr" else None

class FakeAntwort:
    def __init__(self, adresse):
        self.extensions = {"network_stream": FakeStream(adresse)}

def ohne_proxy(fn):
    """Die Peer-Pruefung entfaellt hinter einem Proxy - fuer den Test weg."""
    gesichert = {n: os.environ.pop(n, None) for n in connectors.PROXY_VARS}
    try:
        return fn()
    finally:
        for n, v in gesichert.items():
            if v is not None:
                os.environ[n] = v

check("öffentliche Gegenstelle wird durchgelassen",
      ohne_proxy(lambda: connectors.peer_pruefen(FakeAntwort("93.184.216.34")))
      is None)
for boese in ("169.254.169.254", "127.0.0.1", "10.1.2.3", "::1"):
    try:
        ohne_proxy(lambda: connectors.peer_pruefen(FakeAntwort(boese)))
        check(f"Gegenstelle {boese} wird verworfen", False, "durchgelassen")
    except connectors.ConnectorError as exc:
        check(f"Gegenstelle {boese} wird verworfen", "verworfen" in str(exc))

# Proxy-Variablen werden standardmäßig ignoriert (trust_env=0). Erst wer sie
# ausdrücklich einschaltet, bekommt den Proxy-Weg — und damit die schwächere
# Zusicherung, weil dann die Gegenstelle der Proxy ist.
check("Proxy-Variablen werden standardmäßig ignoriert",
      connectors.FETCH_TRUST_ENV is False)
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:8080"
try:
    check("Proxy-Variable allein reicht nicht", not connectors.proxy_aktiv())
    connectors.FETCH_TRUST_ENV = True
    check("erst mit trust_env gilt der Proxy", connectors.proxy_aktiv())
    check("dann wird die Gegenstelle nicht geprüft",
          connectors.peer_pruefen(FakeAntwort("127.0.0.1")) is None)
finally:
    connectors.FETCH_TRUST_ENV = False
    del os.environ["HTTPS_PROXY"]
check("ohne Proxy-Variable meldet proxy_aktiv False",
      ohne_proxy(connectors.proxy_aktiv) is False)

check("ohne ermittelbare Gegenstelle wird nicht blockiert",
      connectors.peer_pruefen(FakeAntwort(None)) is None)
class OhneStream:
    extensions: dict = {}
check("fehlender Stream blockiert nicht",
      connectors.peer_pruefen(OhneStream()) is None)

# 8. HTTPS braucht offene Ports 80/443.
# https_text stammt von weiter oben und ist "" wenn die Datei fehlt - dann
# meldet der Check einen Fehlschlag, statt die ganze Suite abzubrechen.
check("enable-https.sh öffnet Port 80 und 443",
      "for port in 80 443" in https_text, "fehlt")
check("Begründung steht dabei", "HTTP-01" in https_text)

print("\n[33] Befunde aus der externen Durchsicht")

# Ungueltige Portangabe: parsed.port wirft ValueError, der frueher roh aus dem
# Konnektor herausfiel statt als verstaendliche Meldung anzukommen.
for kaputt in ("https://beispiel.de:abc", "http://beispiel.de:abc/pfad"):
    try:
        connectors.check_url(kaputt, lambda _h: {"93.184.216.34"})
        check(f"ungültiger Port in {kaputt} wird abgewiesen", False, "durchgelassen")
    except connectors.ConnectorError as exc:
        check(f"ungültiger Port in {kaputt} wird abgewiesen", "Port" in str(exc), str(exc))
    except ValueError as exc:
        check(f"ungültiger Port in {kaputt} wird abgewiesen", False,
              f"roher ValueError: {exc}")

# Zugangsdaten im Remote landen woertlich in .git/config - scrub() kennt sie
# nicht, weil sie nicht BRAUNY_GIT_TOKEN sind.
def mit_remote(url, fn):
    orig_r, orig_t = connectors.GIT_REMOTE, connectors.GIT_TOKEN
    connectors.GIT_REMOTE, connectors.GIT_TOKEN = url, "dummy"
    try:
        return fn()
    finally:
        connectors.GIT_REMOTE, connectors.GIT_TOKEN = orig_r, orig_t

try:
    mit_remote("https://ghp_fremdes_token@github.com/n/r.git",
               lambda: connectors.git_push(pws, "x", "y"))
    check("Remote mit eingebettetem Token wird abgelehnt", False, "durchgelassen")
except connectors.ConnectorError as exc:
    check("Remote mit eingebettetem Token wird abgelehnt",
          "Zugangsdaten" in str(exc), str(exc))
check("Remote ohne Zugangsdaten bleibt erlaubt",
      "brauny/" in mit_remote(bare, lambda: connectors.git_push(pws, "sauber", "Test")))

# Sandbox-Rechte: nicht mehr world-writable.
rechte_dir = sandbox.make_project_dir({"main.py": "x=1\n", "pkg/m.py": "y=2\n"})
for pfad, art in [(rechte_dir, "Projektverzeichnis"),
                  (os.path.join(rechte_dir, "pkg"), "Unterverzeichnis")]:
    modus = os.stat(pfad).st_mode & 0o777
    check(f"{art} ist nicht world-writable", not modus & 0o002, oct(modus))
    check(f"{art} ist für den Sandbox-User betretbar", modus & 0o001, oct(modus))
for datei in ["main.py", os.path.join("pkg", "m.py")]:
    modus = os.stat(os.path.join(rechte_dir, datei)).st_mode & 0o777
    check(f"{datei} ist nicht world-writable", not modus & 0o002, oct(modus))
    check(f"{datei} ist für den Sandbox-User lesbar", modus & 0o004, oct(modus))
shutil.rmtree(rechte_dir, ignore_errors=True)
check("Projekt wird nur-lesend eingehängt",
      '"mode": "ro"' in inspect.getsource(sandbox.start))

# Mehrwortige Ausloeser haetten nie gezuendet.
mehrwort = skills_mod.parse(
    "---\nname: mw\nausloeser: leerer test, alpha\n---\nAnleitung hier.\n", "mw.md")
check("mehrwortiger Auslöser zündet",
      skills_mod.score("mach einen leerer test daraus", mehrwort) >= 1,
      skills_mod.score("mach einen leerer test daraus", mehrwort))
check("einzelnes Wort daraus zündet NICHT allein",
      skills_mod.score("nur leerer text", mehrwort) == 0,
      skills_mod.score("nur leerer text", mehrwort))
check("einwortiger Auslöser bleibt wortweise",
      skills_mod.score("alphabet lesen", mehrwort) == 0)

# read_file auf einer leeren Datei.
bws.write("leer.py", "")
r = asyncio.run(bbox.call("read_file", {"path": "leer.py"}))
check("leere Datei wird als leer gemeldet", "ist leer" in r, r)
check("keine irreführende Zeilenmeldung", "gibt es nicht" not in r, r)

# cloud-init: Passwort darf nicht als Bash ausgewertet werden.
if yaml and ci_text:
    check("Konfiguration wird NICHT mit '.' eingebunden",
          ". /etc/braunycode.setup" not in setup, setup[:400])
    check("Konfiguration wird zeilenweise gelesen",
          "while IFS= read -r zeile" in setup, setup[:600])
    check("runcmd verschluckt den Fehlerstatus nicht",
          "|| echo 'Einrichtung fehlgeschlagen" not in str(ci["runcmd"]),
          str(ci["runcmd"]))

# Und der Nachweis, dass ein Passwort mit '$' bei 'source' verfälscht würde.
konf = os.path.join(tempfile.mkdtemp(), "setup")
with open(konf, "w") as fh:
    fh.write("BRAUNY_TOKEN=geheim$HOME-passwort\n")
mit_source = subprocess.run(
    ["bash", "-c", f'set -a; . "{konf}"; set +a; printf %s "$BRAUNY_TOKEN"'],
    capture_output=True, text=True).stdout
zeilenweise = subprocess.run(
    ["bash", "-c",
     f'while IFS= read -r z || [ -n "$z" ]; do case "$z" in BRAUNY_*=*) : ;; *) continue ;; esac; '
     f'k=${{z%%=*}}; w=${{z#*=}}; export "$k=$w"; done < "{konf}"; printf %s "$BRAUNY_TOKEN"'],
    capture_output=True, text=True).stdout
check("'source' würde das Passwort verfälschen",
      mit_source != "geheim$HOME-passwort", mit_source)
check("zeilenweises Lesen erhält es wörtlich",
      zeilenweise == "geheim$HOME-passwort", zeilenweise)

# ---------------------------------------------------------------- Diagnostik
print("\n[34] Diagnostik: Rohausgabe zu Befunden")

import diagnostics  # noqa: E402

# Echte Python-Ausgabe erzeugen statt eine erfundene nachzubauen: sonst testet
# man nur, dass das eigene Beispiel zum eigenen Muster passt.
def _stderr_von(code):
    ordner = tempfile.mkdtemp()
    ziel = os.path.join(ordner, "prog.py")
    with open(ziel, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(code))
    lauf = subprocess.run([sys.executable, ziel], capture_output=True, text=True)
    return lauf.stderr.replace(ordner, "/app")

b = diagnostics.parse(_stderr_von("""
    def teile(a, b):
        return a / b
    def start():
        return teile(1, 0)
    start()
"""))
check("Traceback ergibt genau einen Befund", len(b) == 1, b)
check("Kategorie aus dem Ausnahmetyp", b and b[0].kategorie == diagnostics.RUNTIME, b)
check("letzter Rahmen, nicht der erste", b and b[0].zeile == 3, b)
check("Symbol des letzten Rahmens", b and b[0].symbol == "teile", b)
check("Containerpfad wird projektrelativ", b and b[0].datei == "prog.py", b)

# Der haeufigste Fehler direkt nach einer Aenderung - und er hat KEINEN
# Traceback-Kopf. Genau daran ist die erste Fassung gescheitert.
b = diagnostics.parse(_stderr_von("def f(:\n    pass\n"))
check("SyntaxError ohne Traceback-Kopf wird erkannt", len(b) == 1, b)
check("und als SYNTAX eingeordnet", b and b[0].kategorie == diagnostics.SYNTAX, b)

b = diagnostics.parse(_stderr_von("def f():\npass\n"))
check("IndentationError zählt als SYNTAX",
      len(b) == 1 and b[0].kategorie == diagnostics.SYNTAX, b)

# Kaputtes Modul beim Import: der SyntaxError steht INNERHALB eines Tracebacks.
ordner = tempfile.mkdtemp()
os.makedirs(os.path.join(ordner, "pkg"))
open(os.path.join(ordner, "pkg", "__init__.py"), "w").close()
with open(os.path.join(ordner, "pkg", "kaputt.py"), "w") as fh:
    fh.write("def g(:\n")
with open(os.path.join(ordner, "prog.py"), "w") as fh:
    fh.write("import pkg.kaputt\n")
roh = subprocess.run([sys.executable, os.path.join(ordner, "prog.py")],
                     capture_output=True, text=True).stderr.replace(ordner, "/app")
b = diagnostics.parse(roh)
check("SyntaxError im Traceback zählt nicht doppelt", len(b) == 1, b)
check("er zeigt auf die kaputte Datei, nicht den Importeur",
      b and b[0].datei == "pkg/kaputt.py", b)

b = diagnostics.parse(
    "SyntaxError in app/x.py, Zeile 12: invalid syntax", "check_syntax")
check("deutsche check_syntax-Meldung wird verstanden",
      len(b) == 1 and b[0].zeile == 12 and b[0].datei == "app/x.py", b)

b = diagnostics.parse("FEHLER: 'pattern' fehlt.", "tools")
check("Werkzeugfehler ohne Ausnahmetyp wird TOOL",
      len(b) == 1 and b[0].kategorie == diagnostics.TOOL, b)

b = diagnostics.parse("FEHLER: ModuleNotFoundError: No module named 'x'", "tools")
check("Werkzeugfehler mit Ausnahmetyp behält die Kategorie",
      len(b) == 1 and b[0].kategorie == diagnostics.IMPORT, b)

check("leere Ausgabe ergibt keine Befunde", diagnostics.parse("") == [])
check("erfolgreiche Ausgabe ergibt keine Befunde",
      diagnostics.parse("Lauf erfolgreich.\nAusgabe:\nfertig") == [])
# Freier Text, der wie eine Ausnahme aussieht, darf keinen Befund erfinden.
check("Fließtext erzeugt keinen Befund",
      diagnostics.parse("Hinweis: ValueError kann hier auftreten") == [])

f = diagnostics.Finding(kategorie=diagnostics.NAME, schwere=diagnostics.FEHLER,
                        nachricht="name 'x' is not defined", typ="NameError",
                        datei="a.py", zeile=9, symbol="f")
check("Fingerabdruck ohne Zeilennummer", "9" not in f.fingerprint(), f.fingerprint())
check("Fingerabdruck ohne freien Text",
      "not defined" not in f.fingerprint(), f.fingerprint())
check("Fingerabdruck trägt Datei und Symbol",
      "a.py" in f.fingerprint() and "|f" in f.fingerprint(), f.fingerprint())

# Verdrahtung: der Befund muss beim Modell ankommen, nicht nur im Protokoll.
async def _rot(_dateien, _entry=None, command=None):
    return 1, ('Traceback (most recent call last):\n'
               '  File "/app/main.py", line 3, in teile\n    return a / b\n'
               'ZeroDivisionError: division by zero')

_w = ws_mod.Workspace(tempfile.mkdtemp())
_tb = tools.Toolbox(_w, run_sandbox=_rot)
asyncio.run(_tb.call("write_file", {"path": "main.py", "content": "x = 1\n"}))
_erg = asyncio.run(_tb.call("run_python", {"path": "main.py"}))
check("Befund hängt am Werkzeugergebnis", "Befund:" in _erg, _erg[-120:])
check("Befund nennt Datei und Zeile", "main.py:3" in _erg, _erg[-120:])
check("Befund steht im Protokoll",
      any(e["befunde"] for e in _tb.protokoll), _tb.protokoll)
check("roter Lauf bleibt trotz Befund ungeprüft",
      _tb.unverified == {"main.py"}, _tb.unverified)

_erg = asyncio.run(_tb.call("search", {}))
check("bei reinem Werkzeugfehler kein Befund-Anhang",
      "Befund:" not in _erg, _erg)


# ------------------------------------------------------------- Testauswahl
print("\n[35] Testauswahl über den Importgraphen")

import testimpact  # noqa: E402

_projekt = {
    "pkg/__init__.py":   "",
    "pkg/kern.py":       "def rechne(x):\n    return x * 2\n",
    "pkg/mittel.py":     "from .kern import rechne\ndef doppelt(x):\n    return rechne(x)\n",
    "app.py":            "import pkg.mittel\n",
    "einsam.py":         "import json\n",
    "test_kern.py":      "from pkg.kern import rechne\n",
    "test_mittel.py":    "from pkg.mittel import doppelt\n",
    "tests/test_app.py": "import app\n",
    "test_einsam.py":    "import einsam\n",
}

check("Modulname aus Pfad", testimpact.modulname("pkg/mod.py") == "pkg.mod")
check("__init__ wird zum Paket", testimpact.modulname("pkg/__init__.py") == "pkg")
check("Testdatei an Präfix erkannt", testimpact.ist_test("test_x.py"))
check("Testdatei an Suffix erkannt", testimpact.ist_test("x_test.py"))
check("Testdatei am Ordner erkannt", testimpact.ist_test("tests/irgendwas.py"))
check("normale Datei ist kein Test", not testimpact.ist_test("pkg/kern.py"))

_g = testimpact.graph(_projekt)
check("relativer Import wird aufgelöst",
      "pkg/kern.py" in _g["pkg/mittel.py"], _g["pkg/mittel.py"])
check("punktierter Import wird aufgelöst",
      "pkg/mittel.py" in _g["app.py"], _g["app.py"])
check("Fremdimport erzeugt keine Kante", _g["einsam.py"] == set(), _g["einsam.py"])

_t = testimpact.betroffene_tests(_projekt, ["pkg/kern.py"])
check("Auswahl reicht über zwei Stufen",
      _t == ["test_kern.py", "test_mittel.py", "tests/test_app.py"], _t)
_t = testimpact.betroffene_tests(_projekt, ["einsam.py"])
check("unabhängige Datei zieht nur ihren Test",
      _t == ["test_einsam.py"], _t)
check("das ist echte Einsparung, nicht die ganze Suite",
      len(_t) < len([r for r in _projekt if testimpact.ist_test(r)]), _t)

# Ein Importzyklus darf die Rueckwaertssuche nicht endlos drehen lassen.
_zyklus = {"a.py": "import b\n", "b.py": "import a\n", "test_a.py": "import a\n"}
check("Importzyklus hängt nicht",
      testimpact.betroffene_tests(_zyklus, ["a.py"]) == ["test_a.py"])

# Eine kaputte Datei verliert ihre Kanten - die Auswahl wird dadurch ZU KURZ.
# Genau das muss dastehen, sonst wirkt eine unvollstaendige Liste wie ein
# Ergebnis.
_kaputt = dict(_projekt, **{"pkg/mittel.py": "from .kern import (\n"})
_bericht = testimpact.bericht(_kaputt, ["pkg/kern.py"])
check("unlesbare Datei wird beim Namen genannt",
      "pkg/mittel.py" in _bericht and "ACHTUNG" in _bericht, _bericht)
check("und die Auswahl wird als unvollständig bezeichnet",
      "unvollständig" in _bericht, _bericht)
check("der Graph bricht dabei nicht ab",
      "test_kern.py" in _bericht, _bericht)

check("Bericht nennt die Grenze der Methode",
      "importlib" in testimpact.bericht(_projekt, ["pkg/kern.py"]))

_ohne = {"a.py": "x = 1\n"}
check("Projekt ohne Tests wird als solches gemeldet",
      "keine erkennbaren Testdateien" in testimpact.bericht(_ohne, ["a.py"]),
      testimpact.bericht(_ohne, ["a.py"]))

_waise = {"a.py": "x = 1\n", "test_b.py": "import json\n"}
check("Code ohne erreichenden Test wird benannt",
      "Kein Test erreicht" in testimpact.bericht(_waise, ["a.py"]),
      testimpact.bericht(_waise, ["a.py"]))

# Verdrahtung als Werkzeug
_w = ws_mod.Workspace(tempfile.mkdtemp())
for _rel, _inhalt in _projekt.items():
    _w.write(_rel, _inhalt)
_tb = tools.Toolbox(_w)
check("ohne Änderung sagt das Werkzeug das",
      "Noch nichts geändert" in asyncio.run(_tb.call("affected_tests", {})))
asyncio.run(_tb.call("edit_file", {"path": "pkg/kern.py",
                                   "old_text": "x * 2", "new_text": "x * 3"}))
_erg = asyncio.run(_tb.call("affected_tests", {}))
check("Werkzeug nimmt von selbst die geänderten Dateien",
      "pkg/kern.py" in _erg and "test_mittel.py" in _erg, _erg)
_erg = asyncio.run(_tb.call("affected_tests", {"paths": "einsam.py"}))
check("gezielte Abfrage grenzt richtig ein",
      "test_einsam.py" in _erg and "test_kern.py" not in _erg, _erg)
check("affected_tests belegt nichts",
      _tb.unverified == {"pkg/kern.py"}, _tb.unverified)


# ------------------------------------------------- Skills gegen neue Werkzeuge
print("\n[36] Ausgelieferte Skills kennen die Werkzeuge")

# Zweimal ist in diesem Projekt schon ein neues Werkzeug hinzugekommen, ohne in
# die Allowlists der Skills einzuziehen - und war damit ueberall still
# gesperrt, wo ein Skill griff. Kein Fehler, keine Meldung, nur ein Agent, der
# das Werkzeug nie benutzt. Diese Zusicherung faengt den dritten Fall.
_skills = skills_mod.load(os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills"))
check("Skills werden geladen", len(_skills) >= 5, len(_skills))

for _s in _skills:
    _erlaubt = set(_s.werkzeuge)
    check(f"{_s.name}: nur existierende Werkzeuge",
          _erlaubt <= tools.NAMES, sorted(_erlaubt - tools.NAMES))
    check(f"{_s.name}: finish ist drin", "finish" in _erlaubt, sorted(_erlaubt))
    if _erlaubt & tools.MODIFYING:
        # Wer Code aendern darf, muss auch pruefen duerfen - sonst laeuft er
        # in die Abschluss-Sperre und kommt nicht wieder heraus.
        check(f"{_s.name}: ändert Code, also auch check_syntax",
              "check_syntax" in _erlaubt, sorted(_erlaubt))
    if _erlaubt & tools.MODIFYING and _erlaubt & {"run_python", "run_command"}:
        # Nur wer auch ausfuehren darf. 'doku' aendert Docstrings, kann aber
        # bewusst nichts starten - eine Testliste waere dort Information, mit
        # der es nichts anfangen kann. Die erste Fassung dieser Zusicherung war
        # zu grob und hat genau das angemahnt.
        check(f"{_s.name}: ändert und führt aus, also auch affected_tests",
              "affected_tests" in _erlaubt, sorted(_erlaubt))


# ------------------------------------------------------ Bereitschaftspruefung
print("\n[37] Bereitschaft: nicht raten, welche Datei gemeint war")

import readiness  # noqa: E402

_d = ["app/main.py", "app/tools.py", "app/workspace.py", "test_smoke.py"]
_sym = ["berechne_summen", "berechne_saldo", "Workspace", "Toolbox"]

def _stand(text):
    return readiness.pruefen(text, _d, _sym).stand

check("bekannte Datei ist bereit",
      _stand("Behebe den Fehler in app/tools.py") == readiness.BEREIT)
check("Tippfehler wird zur benannten Annahme",
      _stand("Behebe den Fehler in app/tool.py") == readiness.BEREIT_MIT_ANNAHME)
check("unbekannte Datei löst Rückfrage aus",
      _stand("Behebe den Fehler in app/kern.py") == readiness.RUECKFRAGE)
check("Symbol-Tippfehler wird zur Annahme",
      _stand("Benenne berechne_summe in summiere um") == readiness.BEREIT_MIT_ANNAHME)

# Die Rueckfrage muss konkret sein, nicht "bitte praezisieren".
_u = readiness.pruefen("Behebe den Fehler in app/tool.py", _d, _sym)
check("die Annahme nennt beide Namen",
      "app/tool.py" in _u.text() and "app/tools.py" in _u.text(), _u.text())

# Keine Rueckfrage aus Prinzip: vage Auftraege sind kein Mangel, sie nennen
# eben nichts Konkretes. Wer hier fragt, macht den Agenten unbrauchbar.
for _vage in ("Mach die Anwendung schneller",
              "Die Datenbank ist zu langsam, optimiere die Abfragen",
              "Räum den Code auf"):
    check(f"vage bleibt bereit: {_vage[:28]}", _stand(_vage) == readiness.BEREIT)

# Anlege-Absicht: dass die Datei fehlt, IST der Auftrag.
for _neu in ("Schreibe eine neue Datei helfer.py",
             "Erstelle die Datei berichte.py",
             "Lege ein neues Modul cache.py an",
             "Create a new file cache.py"):
    check(f"Neuanlage fragt nicht: {_neu[:30]}", _stand(_neu) == readiness.BEREIT)

# ... aber "neu" auf einer Datei, die es schon gibt, ist ein Widerspruch.
check("Neuanlage einer vorhandenen Datei wird hinterfragt",
      _stand("Lege eine neue Datei tools.py an") == readiness.RUECKFRAGE)
# ... und "neue Funktion IN einer Datei" ist keine Dateianlage.
check("'neue Funktion in X' ist keine Neuanlage",
      _stand("Füge eine neue Funktion in app/kern.py hinzu") == readiness.RUECKFRAGE)

check("leeres Projekt fragt nie",
      readiness.pruefen("Baue etwas in main.py", [], []).stand == readiness.BEREIT)

# Verdrahtung: die Sperre muss VOR der ersten Änderung greifen.
_w = ws_mod.Workspace(tempfile.mkdtemp())
_w.write("app/tools.py", "x = 1\n")
_ereignisse = []
async def _send(typ, text="", **rest):
    _ereignisse.append({"type": typ, "text": text, **rest})
async def _chat(_messages, _schema):
    raise AssertionError("Das Modell darf bei einer Rückfrage gar nicht laufen")

_tb = tools.Toolbox(_w)
_ausgang = asyncio.run(agentloop.run_tool_agent(
    _send, "Behebe den Fehler in app/kern.py",
    chat_fn=_chat, toolbox=_tb, symbole=[]))
check("Lauf endet als clarify", _ausgang == "clarify", _ausgang)
check("kein Modellaufruf bei Rückfrage", True)   # _chat haette sonst geworfen
check("nichts wurde geschrieben", _tb.written == [], _tb.written)
check("die Frage nennt die Datei",
      any("app/kern.py" in e["text"] for e in _ereignisse if e["type"] == "error"),
      _ereignisse)
check("done meldet ehrlich ok=False",
      any(e["type"] == "done" and e["ok"] is False for e in _ereignisse),
      _ereignisse)

# Annahme blockiert nicht, steht aber im Auftrag, den das Modell sieht.
_gesehen = []
async def _chat2(messages, _schema):
    _gesehen.append(messages)
    return reply(calls=[("finish", {"summary": "fertig"})])
_tb2 = tools.Toolbox(ws_mod.Workspace(tempfile.mkdtemp()))
_tb2.ws.write("app/tools.py", "x = 1\n")
_ausgang = asyncio.run(agentloop.run_tool_agent(
    _send, "Behebe den Fehler in app/tool.py",
    chat_fn=_chat2, toolbox=_tb2, symbole=[]))
check("Annahme blockiert den Lauf nicht", _ausgang == "ok", _ausgang)
check("die Annahme steht im Auftrag des Modells",
      any("app/tools.py" in m.get("content", "")
          for m in _gesehen[0] if m.get("role") == "user"), _gesehen[0])


# --------------------------------------------------------- Abtastverhalten
print("\n[38] Abtastverhalten: deterministisch, wo es genau sein muss")

# Bis hierher setzte KEIN Pfad eine Temperatur - der Lauf uebernahm die
# Vorgabe des Anbieters, meist um 0.8. Ein Agent, der Werkzeuge mit exakten
# Argumenten aufruft, bekam damit bei gleicher Aufgabe verschiedene Aufrufe
# und ein nicht nachstellbares Fehlerbild.

def _ollama_kwargs(policy=provider.DETERMINISTISCH):
    """Faengt die Nutzlast ab, mit der Ollama tatsaechlich gerufen wird."""
    gesehen = {}
    orig = httpx.post
    def _post(url, json=None, timeout=None, **rest):
        gesehen.update(json or {})
        return OllamaAntwort({"message": {"content": "ok"}})
    httpx.post = _post
    try:
        provider._chat_ollama([{"role": "user", "content": "x"}], None, policy)
    finally:
        httpx.post = orig
    return gesehen

_kw = _ollama_kwargs()
check("Ollama bekommt Optionen", "options" in _kw, _kw.keys())
check("deterministisch heißt Temperatur 0",
      _kw["options"]["temperature"] == 0.0, _kw.get("options"))
check("ohne BRAUNY_SEED kein seed im Aufruf",
      "seed" not in _kw["options"], _kw.get("options"))

_kw = _ollama_kwargs(provider.VIELFALT)
check("Vielfalt heißt Temperatur über 0",
      _kw["options"]["temperature"] > 0, _kw.get("options"))

def _openai_payload(policy=provider.DETERMINISTISCH):
    """Faengt die Nutzlast ab, ohne eine Anfrage zu senden."""
    import httpx
    gesehen = {}

    class _Antwort:
        status_code = 200
        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "ok"}}]}

    class _Client:
        def __init__(self, **_): pass
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def post(self, _url, json=None, headers=None):
            gesehen.update(json or {})
            return _Antwort()

    orig_client, orig_base, orig_provider = (
        httpx.Client, provider.API_BASE, provider.PROVIDER)
    httpx.Client = _Client
    provider.API_BASE, provider.PROVIDER = "https://example.invalid/v1", "openai"
    try:
        provider._chat_openai([{"role": "user", "content": "x"}], None, policy)
    finally:
        httpx.Client = orig_client
        provider.API_BASE, provider.PROVIDER = orig_base, orig_provider
    return gesehen

_p = _openai_payload()
check("API-Nutzlast trägt eine Temperatur", "temperature" in _p, sorted(_p))
check("und sie ist 0", _p["temperature"] == 0.0, _p.get("temperature"))
check("ohne BRAUNY_SEED kein seed in der Nutzlast", "seed" not in _p, sorted(_p))
check("Vielfalt wirkt auch über die API",
      _openai_payload(provider.VIELFALT)["temperature"] > 0)

# Ein gesetzter Startwert muss ankommen ...
_orig_seed = provider.SEED
try:
    provider.SEED = 4711
    check("gesetzter Startwert erreicht Ollama",
          _ollama_kwargs()["options"].get("seed") == 4711)
    check("gesetzter Startwert erreicht die API",
          _openai_payload().get("seed") == 4711)
finally:
    provider.SEED = _orig_seed

# ... und ein Tippfehler darin darf NICHT jeden Modellaufruf sprengen.
_alt = os.environ.get("BRAUNY_SEED")
try:
    os.environ["BRAUNY_SEED"] = "vier-sieben-eins-eins"
    check("unbrauchbarer Startwert wird ignoriert statt zu werfen",
          provider._seed_lesen() is None)
    os.environ["BRAUNY_SEED"] = "  17 "
    check("Leerzeichen um den Startwert stören nicht",
          provider._seed_lesen() == 17)
finally:
    if _alt is None:
        os.environ.pop("BRAUNY_SEED", None)
    else:
        os.environ["BRAUNY_SEED"] = _alt

check("Standard ist deterministisch",
      provider._temperatur("unbekannte-betriebsart") == 0.0)


# ------------------------------------------------------------ Sandbox-Image
print("\n[39] Sandbox-Image")

_WURZEL = os.path.dirname(os.path.abspath(__file__))
_DOCKERFILE = os.path.join(_WURZEL, "deploy", "sandbox.Dockerfile")
check("Dockerfile liegt vor", os.path.exists(_DOCKERFILE), _DOCKERFILE)

if os.path.exists(_DOCKERFILE):
    with open(_DOCKERFILE, encoding="utf-8") as _fh:
        _df = _fh.read()
    check("baut auf dem bisherigen Basisimage auf",
          "FROM python:3.11-slim" in _df)
    check("bringt einen Testläufer mit", "pytest" in _df)
    check("bringt Hypothesis mit", "hypothesis" in _df)
    # Bewusste Entscheidung, kein Versehen: mutmut zieht einen Terminal-UI-
    # Stapel mit, der in einem Container ohne Netz und ohne Terminal nichts
    # verloren hat. Wer ihn spaeter doch will, soll diese Zusicherung sehen.
    check("mutmut bleibt bewusst draußen", "mutmut" not in _df.split("RUN pip")[-1])
    check("Versionen sind nach oben begrenzt",
          "<10" in _df and "<7" in _df, _df)
    # Die Haertung steht in app/sandbox.py. Ein USER hier wuerde dieselbe
    # Entscheidung an einer zweiten Stelle treffen - genau so laufen zwei
    # Wahrheiten auseinander.
    check("kein zweiter Ort für die Benutzer-Entscheidung",
          not any(z.strip().startswith("USER ") for z in _df.splitlines()))
    check("prüft sich beim Bauen selbst",
          "import pytest, hypothesis" in _df)

with open(os.path.join(_WURZEL, "install.sh"), encoding="utf-8") as _fh:
    _inst = _fh.read()
check("install.sh baut das Image", "sandbox.Dockerfile" in _inst)
check("und hat einen Rückfall", "fehlgeschlagen" in _inst and "docker pull" in _inst)

# Den Zweig ausfuehren statt ihn zu lesen: docker und sudo werden ersetzt,
# beide Ausgaenge einmal gefahren.
_start = _inst.index("# Der Sandbox-Container laeuft ohne Netzwerk")
_ende = _inst.index("# ------------------------------------------------"
                    "---------------- 3. Ollama")
_block = _inst[_start:_ende]

_RAHMEN = ("set -euo pipefail\n"
           "step() { :; }\n"
           "warn() { echo \"WARN $*\"; }\n"
           "SRC_DIR=\"@SRC@\"\n"
           "SANDBOX_IMAGE=\"python:3.11-slim\"\n"
           "sudo() { \"$@\"; }\n"
           "docker() { case \"$1\" in build) return @CODE@ ;; "
           "pull) return 0 ;; esac; }\n"
           "@BLOCK@\n"
           "echo \"ERGEBNIS: $SANDBOX_IMAGE\"\n")

_src = tempfile.mkdtemp()
os.makedirs(os.path.join(_src, "deploy"))
with open(os.path.join(_src, "deploy", "sandbox.Dockerfile"), "w") as _fh:
    _fh.write("FROM x\n")

def _fahre_zweig(code):
    _skript = (_RAHMEN.replace("@SRC@", _src).replace("@CODE@", str(code))
                      .replace("@BLOCK@", _block))
    return subprocess.run(["bash", "-c", _skript], capture_output=True, text=True)

_r = _fahre_zweig(0)
check("gelungener Bau setzt das eigene Image",
      "ERGEBNIS: braunycode-sandbox:1" in _r.stdout, _r.stdout + _r.stderr)
_r = _fahre_zweig(1)
check("gescheiterter Bau fällt auf das Basisimage zurück",
      "ERGEBNIS: python:3.11-slim" in _r.stdout, _r.stdout + _r.stderr)
check("und der Verlust wird benannt, nicht verschwiegen",
      "belegt Aenderungen nur" in _r.stdout, _r.stdout)
check("ein gescheiterter Bau kippt die Einrichtung nicht",
      _r.returncode == 0, _r.returncode)


# --------------------------------------------- Gegenbeispiele aus Testlaeufen
print("\n[40] Gegenbeispiele und pytest-Ausgabe")

# Die folgenden Ausgaben stammen aus ECHTEN Laeufen (pytest 9.1.1,
# hypothesis 6.165.2) und sind hier festgehalten, weil hypothesis in der
# Testumgebung nicht installiert sein muss. Weiter unten wird zusaetzlich
# frisch erzeugt, falls es doch verfügbar ist.

_HYP_PYTEST = """guthaben = 0, betrag = 1

    @given(st.integers(min_value=0, max_value=1000),
           st.integers(min_value=0, max_value=1000))
    def test_kontostand_nie_negativ(guthaben, betrag):
>       assert abheben(guthaben, betrag) >= 0
E       assert -1 >= 0
E        +  where -1 = abheben(0, 1)
E       Failing test case: test_kontostand_nie_negativ(
E           guthaben=0,
E           betrag=1,
E       )

test_konto.py:9: AssertionError
=========================== short test summary info ============================
FAILED test_konto.py::test_kontostand_nie_negativ - assert -1 >= 0
1 failed in 0.89s"""

_b = diagnostics.parse(_HYP_PYTEST, "run_command")
check("pytest-Fehlschlag ergibt einen Befund", len(_b) == 1, _b)
check("widerlegte Eigenschaft wird PROPERTY",
      _b and _b[0].kategorie == diagnostics.PROPERTY, _b)
check("Datei aus der pytest-Zusammenfassung",
      _b and _b[0].datei == "test_konto.py", _b)
check("Zeilennummer nachgereicht", _b and _b[0].zeile == 9, _b)
check("Testname als Symbol",
      _b and _b[0].symbol == "test_kontostand_nie_negativ", _b)
check("Gegenbeispiel extrahiert",
      _b and _b[0].gegenbeispiel == "guthaben=0, betrag=1", _b and _b[0].gegenbeispiel)
check("Gegenbeispiel steht im Einzeiler",
      _b and "guthaben=0" in _b[0].einzeiler(), _b and _b[0].einzeiler())
# Der Fingerabdruck darf den konkreten Fall NICHT enthalten - sonst zeigt jede
# neue Eingabe auf einen anderen Eintrag im Fehlergedaechtnis.
check("Fingerabdruck ohne den konkreten Fall",
      _b and "guthaben=0" not in _b[0].fingerprint(), _b and _b[0].fingerprint())

_HYP_DIREKT = """Traceback (most recent call last):
  File "/app/direkt.py", line 7, in <module>
    test_nie_negativ()
  File "/app/direkt.py", line 5, in test_nie_negativ
    assert abheben(g, b) >= 0
AssertionError
Failing test case: test_nie_negativ(
    g=0,
    b=1,
)"""
_b = diagnostics.parse(_HYP_DIREKT, "run_python")
check("auch ohne pytest erkannt", len(_b) == 1, _b)
check("und ebenfalls PROPERTY",
      _b and _b[0].kategorie == diagnostics.PROPERTY, _b)
check("Gegenbeispiel ohne pytest",
      _b and _b[0].gegenbeispiel == "g=0, b=1", _b and _b[0].gegenbeispiel)

# Ältere Hypothesis-Fassungen schreiben 'Falsifying example'.
check("die ältere Schreibweise wird auch verstanden",
      diagnostics.parse(_HYP_DIREKT.replace("Failing test case",
                                            "Falsifying example"))[0]
      .gegenbeispiel == "g=0, b=1")

_PYTEST_SCHLICHT = """=================================== FAILURES ===================================
__________________________________ test_summe __________________________________

    def test_summe():
>       assert summe(2, 3) == 5
E       assert -1 == 5

test_schlicht.py:3: AssertionError
=========================== short test summary info ============================
FAILED test_schlicht.py::test_summe - assert -1 == 5
1 failed in 0.17s"""
_b = diagnostics.parse(_PYTEST_SCHLICHT, "run_command")
check("gewöhnlicher pytest-Fehlschlag wird erkannt", len(_b) == 1, _b)
# Ohne Gegenbeispiel bleibt es eine Zusicherung. Alles zu PROPERTY zu erklären
# waere eine Behauptung ueber eine ganze Eingabeklasse, die hier niemand belegt.
check("ohne Gegenbeispiel bleibt es ASSERTION",
      _b and _b[0].kategorie == diagnostics.ASSERTION, _b)
check("kein Gegenbeispiel erfunden", _b and _b[0].gegenbeispiel is None, _b)

_MEHRERE = _PYTEST_SCHLICHT + "\nFAILED test_a.py::test_x - assert 1 == 2"
check("mehrere FAILED-Zeilen ergeben mehrere Befunde",
      len(diagnostics.parse(_MEHRERE)) == 2, diagnostics.parse(_MEHRERE))

check("bestandener Lauf ergibt keinen Befund",
      diagnostics.parse("2 passed in 0.10s") == [])

# Wenn hypothesis zur Hand ist: frisch erzeugen statt nur die Aufzeichnung.
try:
    import hypothesis  # noqa: F401
except ImportError:
    print("  ÜBERSPRUNGEN  Livelauf gegen hypothesis (nicht installiert)")
else:
    _ordner = tempfile.mkdtemp()
    with open(os.path.join(_ordner, "p.py"), "w", encoding="utf-8") as _fh:
        _fh.write(textwrap.dedent("""
            from hypothesis import given, strategies as st
            def abheben(g, b): return g - b
            @given(st.integers(min_value=0, max_value=100),
                   st.integers(min_value=0, max_value=100))
            def test_nie_negativ(g, b):
                assert abheben(g, b) >= 0
            test_nie_negativ()
        """))
    _roh = subprocess.run([sys.executable, os.path.join(_ordner, "p.py")],
                          capture_output=True, text=True).stderr
    _b = diagnostics.parse(_roh.replace(_ordner, "/app"))
    check("Livelauf ergibt ein Gegenbeispiel",
          _b and _b[0].gegenbeispiel is not None, _roh[-300:])
    check("Livelauf wird als PROPERTY eingeordnet",
          _b and _b[0].kategorie == diagnostics.PROPERTY, _b)


# ---------------------------------------------------------- Fehlergedaechtnis
print("\n[41] Fehlergedächtnis")

import memory as memory_mod  # noqa: E402

def _befund(datei="konto.py", symbol="abheben", typ="AssertionError", text="negativ"):
    return diagnostics.Finding(
        kategorie=diagnostics.kategorie_fuer(typ), schwere=diagnostics.FEHLER,
        typ=typ, nachricht=text, datei=datei, symbol=symbol, schritt=1)

_pfad = os.path.join(tempfile.mkdtemp(), "tiefer", "g.sqlite")
_g = memory_mod.Gedaechtnis(_pfad)
check("Datei wird samt Ordner angelegt", os.path.exists(_pfad), _pfad)
check("frisches Gedächtnis ist leer", _g.anzahl() == 0)
check("ohne Vorwissen kein Hinweis", _g.hinweis(_befund()) == "")

check("leere Lösung wird abgewiesen", _g.merken(_befund(), "   ") is False)
check("und landet nicht in der Ablage", _g.anzahl() == 0)

check("echte Lösung wird gemerkt",
      _g.merken(_befund(), "edit_file(konto.py)") is True)
_h = _g.hinweis(_befund())
check("Hinweis nennt die Lösung", "edit_file(konto.py)" in _h, _h)
# Als Hinweis, nicht als Anweisung: was damals half, muss heute nicht stimmen.
check("Hinweis ist als ungeprüft gekennzeichnet",
      "nicht ungeprüft übernehmen" in _h, _h)

# Ein anderer Fehler an derselben Stelle darf NICHT treffen - sonst bekommt
# das Modell eine Lösung für ein anderes Problem vorgelegt.
check("anderer Fehlertyp trifft nicht",
      _g.hinweis(_befund(typ="NameError")) == "")
check("anderes Symbol trifft nicht",
      _g.hinweis(_befund(symbol="einzahlen")) == "")
check("andere Datei trifft nicht",
      _g.hinweis(_befund(datei="andere.py")) == "")

# Wechselnder Text im selben Fehler muss weiterhin treffen - genau dafür ist
# der Fingerabdruck ohne den freien Text gebaut.
check("wechselnde Meldung trifft trotzdem",
      _g.hinweis(_befund(text="ganz anderer Wortlaut")) != "")

for _i in range(5):
    _g.merken(_befund(), f"lösung-{_i}")
check("Anzahl der gezeigten Treffer ist gedeckelt",
      _g.hinweis(_befund()).count("  - ") <= memory_mod.MAX_TREFFER,
      _g.hinweis(_befund()))

# Alterung: ein Fix von vor einem Jahr kann sich auf Code beziehen, den es
# nicht mehr gibt.
import sqlite3 as _sq  # noqa: E402
with _sq.connect(_pfad) as _c:
    _c.execute("UPDATE fehler SET zeitpunkt = ?", (time.time() - 400 * 86400,))
check("veraltete Einträge werden nicht mehr gezeigt", _g.hinweis(_befund()) == "")
check("und lassen sich entfernen", _g.aufraeumen() > 0)
check("danach ist die Ablage leer", _g.anzahl() == 0)

# Kein Quelltext in der Ablage - ein Gedächtnis, das Dateiinhalte mitschreibt,
# waere ein Datenleck mit Zusatznutzen.
_g.merken(_befund(), "edit_file(konto.py)")
with _sq.connect(_pfad) as _c:
    _inhalt = " ".join(str(z) for z in _c.execute("SELECT * FROM fehler"))
check("kein Quelltext in der Ablage",
      "def " not in _inhalt and "return" not in _inhalt, _inhalt)

# Lösung aus dem Protokoll ableiten
_prot = [
    {"nr": 1, "werkzeug": "read_file", "ok": True, "dateien": []},
    {"nr": 2, "werkzeug": "run_python", "ok": True, "dateien": []},
    {"nr": 3, "werkzeug": "edit_file", "ok": True, "dateien": [{"pfad": "konto.py"}]},
    {"nr": 4, "werkzeug": "edit_file", "ok": True, "dateien": [{"pfad": "konto.py"}]},
    {"nr": 5, "werkzeug": "write_file", "ok": False, "dateien": [{"pfad": "x.py"}]},
]
_l = memory_mod.loesung_beschreiben(_prot, ab_schritt=2)
check("nur Schritte NACH dem Fehler", "read_file" not in _l, _l)
check("Wiederholungen werden zusammengefasst",
      _l.count("edit_file") == 1, _l)
check("gescheiterte Schritte zählen nicht", "write_file" not in _l, _l)
check("Werkzeug und Pfad, kein Inhalt", _l == "edit_file(konto.py)", _l)

# Verdrahtung: Hinweis erscheint im Werkzeugergebnis
async def _rot(_d, _e=None, command=None):
    return 1, ('Traceback (most recent call last):\n'
               '  File "/app/konto.py", line 2, in abheben\n    return g - b\n'
               'AssertionError: negativ')

_w = ws_mod.Workspace(tempfile.mkdtemp())
_w.write("konto.py", "def abheben(g, b):\n    return g - b\n")
_g2 = memory_mod.Gedaechtnis(os.path.join(tempfile.mkdtemp(), "g2.sqlite"))

_tb = tools.Toolbox(_w, run_sandbox=_rot, gedaechtnis=_g2)
_erg = asyncio.run(_tb.call("run_python", {"path": "konto.py"}))
check("beim ersten Mal kein Vorwissen", "kam hier schon vor" not in _erg, _erg[-200:])
check("der Befund trägt seinen Schritt",
      _tb.befunde and _tb.befunde[0].schritt is not None, _tb.befunde)

_g2.merken(_tb.befunde[0], "edit_file(konto.py)")
_tb2 = tools.Toolbox(_w, run_sandbox=_rot, gedaechtnis=_g2)
_erg = asyncio.run(_tb2.call("run_python", {"path": "konto.py"}))
check("beim zweiten Mal steht das Vorwissen dabei",
      "kam hier schon vor" in _erg and "edit_file(konto.py)" in _erg, _erg[-300:])

# Ohne Gedächtnis muss alles unverändert funktionieren.
_tb3 = tools.Toolbox(_w, run_sandbox=_rot)
_erg = asyncio.run(_tb3.call("run_python", {"path": "konto.py"}))
check("ohne Gedächtnis läuft es normal weiter",
      "Befund:" in _erg and "kam hier schon vor" not in _erg, _erg[-200:])

# Ein unbelegter Lauf darf NICHTS merken - sonst wird eine Vermutung als
# Erfahrung weitergereicht.
_g3 = memory_mod.Gedaechtnis(os.path.join(tempfile.mkdtemp(), "g3.sqlite"))
class _Attrappe:
    gedaechtnis = _g3
    befunde = [_befund()]
    protokoll = [{"nr": 2, "werkzeug": "edit_file", "ok": True,
                  "dateien": [{"pfad": "konto.py"}]}]
agentloop._merken(_Attrappe())
check("belegter Lauf merkt sich etwas", _g3.anzahl() == 1, _g3.anzahl())


# ------------------------------------------------- Reichweite eines Belegs
print("\n[42] Ein grüner Lauf belegt nur, was er erreicht hat")

# Der Fehler, den diese Zusicherungen verhindern: 'pytest test_a.py' laeuft
# durch, und die Sperre erklaert damit AUCH eine gleichzeitig geaenderte b.py
# fuer belegt, die kein Test anfasst. Der Beleg waere echt - er belegte nur
# das Falsche. Genau dagegen ist die ganze Schicht gebaut.

def _reichweite_projekt():
    w = ws_mod.Workspace(tempfile.mkdtemp())
    w.write("a.py", "def f(): return 1\n")
    w.write("b.py", "def g(): return 2\n")
    w.write("hilf.py", "X = 3\n")
    w.write("test_a.py", "import a\nimport hilf\ndef test_f(): assert a.f() == 1\n")
    return w

async def _gruen(_d, _e=None, command=None):
    return 0, "1 passed"

def _nach(aenderungen, pruefung):
    tb = tools.Toolbox(_reichweite_projekt(), run_sandbox=_gruen)
    for pfad in aenderungen:
        asyncio.run(tb.call("edit_file", {"path": pfad, "old_text": "3"
                                          if pfad == "hilf.py" else "return",
                                          "new_text": "4" if pfad == "hilf.py"
                                          else "return "}))
    asyncio.run(tb.call(*pruefung))
    return tb

_tb = _nach(["a.py", "b.py"], ("run_command", {"command": "python -m pytest test_a.py"}))
check("gezielter Testlauf belegt nur seinen Ast",
      _tb.unverified == {"b.py"}, sorted(_tb.unverified))
check("und finish wird deshalb abgewiesen",
      asyncio.run(_tb.call("finish", {"summary": "x"})).startswith("FEHLER"))

_tb = _nach(["hilf.py"], ("run_command", {"command": "python -m pytest test_a.py"}))
check("transitiv importierte Datei gilt als belegt",
      _tb.unverified == set(), sorted(_tb.unverified))

_tb = _nach(["a.py", "b.py"], ("run_command", {"command": "python -m pytest"}))
check("ein Lauf ohne Dateiangabe belegt alles",
      _tb.unverified == set(), sorted(_tb.unverified))

_tb = _nach(["a.py", "b.py"], ("run_python", {"path": "a.py"}))
check("run_python belegt nur seinen eigenen Ast",
      _tb.unverified == {"b.py"}, sorted(_tb.unverified))

_tb = _nach(["a.py", "b.py"], ("run_command", {"command": "python -m pytest test_a.py"}))
asyncio.run(_tb.call("run_python", {"path": "b.py"}))
check("beide Äste belegt lässt finish durch",
      not asyncio.run(_tb.call("finish", {"summary": "x"})).startswith("FEHLER"))
check("und der Lauf gilt als verifiziert", _tb.verified is True)

for _befehl, _erwartet in (
        ('python -m pytest test_a.py', ["test_a.py"]),
        ('python -m pytest "test_a.py"', ["test_a.py"]),
        ("python -m pytest 'test_a.py'::test_f", ["test_a.py"]),
        ("python -m pytest test_a.py::test_f", ["test_a.py"]),
        ("python -m pytest", [])):
    check(f"Befehlszerlegung: {_befehl[17:] or '(ohne Datei)'}",
          testimpact.dateien_aus_befehl(_befehl, {"test_a.py"}) == _erwartet,
          testimpact.dateien_aus_befehl(_befehl, {"test_a.py"}))

# Nicht-Python-Dateien: sie koennen von keinem Lauf belegt werden, duerfen den
# Abschluss also nicht blockieren - aber verschwiegen werden sie auch nicht.
_w = ws_mod.Workspace(tempfile.mkdtemp())
_w.write("main.py", "print(1)\n")
_tb = tools.Toolbox(_w, run_sandbox=_gruen)
asyncio.run(_tb.call("write_file", {"path": "notiz.txt", "content": "x"}))
check("Textdatei blockiert den Abschluss nicht", _tb.unverified == set(), _tb.unverified)
check("sie wird aber getrennt vermerkt",
      _tb.ungeprueft_sonstige == {"notiz.txt"}, _tb.ungeprueft_sonstige)
check("und steht im Bericht",
      _tb.bericht()["ohne_pruefmoeglichkeit"] == ["notiz.txt"], _tb.bericht())
check("finish geht deshalb durch",
      not asyncio.run(_tb.call("finish", {"summary": "x"})).startswith("FEHLER"))


# --------------------------------------------------------- Linter-Ausgabe
print("\n[43] ruff und mypy")

# Aufzeichnungen aus echten Laeufen (ruff 0.16.2, mypy 2.3.0). ruff steckt im
# Sandbox-Image, mypy nicht - dessen Parser ist trotzdem da, falls ein Projekt
# sein eigenes mitbringt.

_RUFF = """modul.py:1:1: I001 [*] Import block is un-sorted or un-formatted
modul.py:1:8: F401 [*] `os` imported but unused
modul.py:13:5: F841 Local variable `x` is assigned to but never used
modul.py:13:9: F821 Undefined name `ergebnis`
Found 4 errors."""

_b = diagnostics.parse(_RUFF, "run_command")
check("ruff: alle vier Zeilen erkannt", len(_b) == 4, _b)
_nach_typ = {f.typ: f for f in _b}
# Ein nicht aufgeloester Name ist ein Fehler, eine unsortierte Importliste
# nicht. Beides gleich zu melden brächte das Modell dazu, Stil zu reparieren,
# während der echte Fehler stehen bleibt.
check("undefinierter Name ist ein Fehler",
      _nach_typ["F821"].schwere == diagnostics.FEHLER
      and _nach_typ["F821"].kategorie == diagnostics.NAME, _nach_typ["F821"])
check("unsortierte Importe sind nur Stil",
      _nach_typ["I001"].schwere == diagnostics.WARNUNG
      and _nach_typ["I001"].kategorie == diagnostics.STIL, _nach_typ["I001"])
check("ungenutzter Import ist nur Stil",
      _nach_typ["F401"].schwere == diagnostics.WARNUNG, _nach_typ["F401"])
check("ruff: Datei und Zeile stimmen",
      _nach_typ["F821"].datei == "modul.py" and _nach_typ["F821"].zeile == 13,
      _nach_typ["F821"])
check("die Zusammenfassungszeile erzeugt keinen Befund",
      all("Found 4 errors" not in f.nachricht for f in _b), _b)

# Der Sonderfall, an dem die erste Fassung gescheitert ist: bei kaputter
# Syntax schreibt ruff 'invalid-syntax:' MIT Doppelpunkt, bei Regelcodes steht
# keiner. Ausgerechnet der wichtigste Befund fiel damit durch.
_b = diagnostics.parse(
    "kaputt.py:1:7: invalid-syntax: Expected a parameter or the end of the "
    "parameter list\nFound 1 error.", "run_command")
check("ruff: Syntaxfehler wird erkannt", len(_b) == 1, _b)
check("und als SYNTAX/Fehler eingeordnet",
      _b and _b[0].kategorie == diagnostics.SYNTAX
      and _b[0].schwere == diagnostics.FEHLER, _b)

_MYPY = ('modul.py:9: error: Incompatible return value type (got "int", '
         'expected "str")  [return-value]\n'
         "Found 1 error in 1 file (checked 1 source file)")
_b = diagnostics.parse(_MYPY, "run_command")
check("mypy: Befund erkannt", len(_b) == 1, _b)
check("mypy: Kategorie ist TYPE",
      _b and _b[0].kategorie == diagnostics.TYPE, _b)
check("mypy: der Regelcode wird zum Typ",
      _b and _b[0].typ == "return-value", _b)
check("mypy: Zeile stimmt", _b and _b[0].zeile == 9, _b)

# Mit --show-column-numbers steht eine Spalte dazwischen.
_b = diagnostics.parse(_MYPY.replace("modul.py:9:", "modul.py:9:12:"), "run_command")
check("mypy: Spaltenangabe stört nicht",
      len(_b) == 1 and _b[0].zeile == 9, _b)

# 'note:' sind Zusatzzeilen unter einem Fehler, keine eigenen Befunde.
_b = diagnostics.parse(
    'a.py:3: error: Argument 1 has incompatible type  [arg-type]\n'
    'a.py:3: note: "f" defined here', "run_command")
check("mypy: Notizzeilen zählen nicht als Befund", len(_b) == 1, _b)

# Gegenprobe: Erfolgsmeldungen dürfen nichts erfinden.
for _sauber in ("All checks passed!",
                "Success: no issues found in 1 source file",
                "Alles in Ordnung."):
    check(f"sauberer Lauf ergibt nichts: {_sauber[:24]}",
          diagnostics.parse(_sauber) == [])

# Und ruff gehört ins Image, mypy bewusst nicht.
if os.path.exists(_DOCKERFILE):
    with open(_DOCKERFILE, encoding="utf-8") as _fh:
        _df = _fh.read()
    _rezept = _df.split("RUN pip install")[1].split("&&")[0]
    check("ruff steckt im Sandbox-Image", "ruff" in _rezept, _rezept)
    check("mypy bleibt bewusst draußen", "mypy" not in _rezept, _rezept)
    check("das Image prüft ruff beim Bauen", "ruff --version" in _df)


print("\n[44] Laeufe leben auf dem Server, nicht in der Verbindung")

# 'runs' waere hier gefaehrlich: weiter oben wird der Name als gewoehnliche
# Variable wiederverwendet (events, runs = drive_agent(...)) und wuerde das
# Modul ueberschreiben.
import runs as runs_mod  # noqa: E402
import threading  # noqa: E402

async def _lauf_grundlagen():
    lauf = runs_mod.Lauf(id="t1", prompt="x", gestartet=time.time())
    lauf.anhaengen("status", "eins")
    lauf.anhaengen("tool", "list_files()")
    lauf.abschliessen(ok=True, text="Fertig.")

    alle = [e async for _, e in lauf.folgen(0)]
    ab_eins = [(i, e) async for i, e in lauf.folgen(1)]
    return lauf, alle, ab_eins

_lauf, _alle, _ab_eins = asyncio.run(_lauf_grundlagen())
check("Ereignisse werden der Reihe nach aufbewahrt",
      [e["type"] for e in _alle] == ["status", "tool", "done"], _alle)
check("ein Abschluss markiert den Lauf als fertig", _lauf.fertig and _lauf.ok is True)
check("folgen(1) fängt beim zweiten Ereignis an",
      _ab_eins[0][0] == 1 and _ab_eins[0][1]["type"] == "tool", _ab_eins[0])
check("folgen(1) wiederholt das erste Ereignis nicht",
      all(e["text"] != "eins" for _, e in _ab_eins), _ab_eins)

# Nach dem Abschluss darf nichts mehr dazukommen - sonst haengt ein
# Zuschauer, der schon 'done' gesehen hat, an einem Lauf ohne Ende.
_lauf.anhaengen("status", "zu spät")
check("nach dem Abschluss wird nichts mehr angenommen",
      [e["type"] for e in _lauf.ereignisse] == ["status", "tool", "done"],
      [e["type"] for e in _lauf.ereignisse])

# Speichergrenze: nicht still abschneiden, sondern sagen, dass gekuerzt wurde.
_voll = runs_mod.Lauf(id="t2", prompt="x", gestartet=time.time())
for _n in range(runs_mod.MAX_EREIGNISSE + 50):
    _voll.anhaengen("sandbox", f"Zeile {_n}")
check("die Ereignisgrenze greift", len(_voll.ereignisse) <= runs_mod.MAX_EREIGNISSE + 2,
      len(_voll.ereignisse))
check("und wird benannt statt verschwiegen",
      any(e["type"] == "error" and "Zu viele" in e["text"] for e in _voll.ereignisse))
check("der Lauf endet dann auch wirklich", _voll.fertig and _voll.ok is False)

_lang = runs_mod.Lauf(id="t3", prompt="x", gestartet=time.time())
_lang.anhaengen("sandbox", "y" * (runs_mod.MAX_TEXT + 500))
check("überlange Texte werden gekappt",
      len(_lang.ereignisse[0]["text"]) == runs_mod.MAX_TEXT,
      len(_lang.ereignisse[0]["text"]))

_reg = runs_mod.Register()
_a = _reg.starten("erster")
_b = _reg.starten("zweiter")
check("Läufe bekommen verschiedene Kennungen", _a.id != _b.id)
check("der offene Lauf ist der jüngste", _reg.offen().id == _b.id)
_b.abschliessen(ok=True)
check("ein fertiger Lauf gilt nicht mehr als offen", _reg.offen().id == _a.id)
_a.abschliessen(ok=True)
check("ohne laufenden Auftrag gibt es keinen offenen", _reg.offen() is None)
check("aber abrufbar bleiben sie", _reg.holen(_a.id) is not None)

# Ein langlaufender Auftrag darf niemals weggeraeumt werden - er ist genau
# dann am wertvollsten, wenn er lange dauert.
_reg2 = runs_mod.Register()
_alt = _reg2.starten("laeuft seit Stunden")
_alt.gestartet = time.time() - 10 * runs_mod.AUFBEWAHRUNG
for _n in range(runs_mod.MAX_LAEUFE + 5):
    _reg2.starten(f"fuellung {_n}").abschliessen(ok=True)
_reg2.starten("neu")
check("ein laufender Auftrag überlebt jedes Aufräumen",
      _reg2.holen(_alt.id) is not None)

# --- Der eigentliche Punkt: Verbindung weg, Lauf laeuft weiter ------------
#
# Genau hieran ist der erste echte Auftrag gescheitert. Der Server hatte
# sauber gearbeitet - nur hatte niemand mehr zugehoert, und mit dem Zuhoerer
# starb die Arbeit.

# Ab hier ein ECHTER Server statt des Testclients.
#
# Der Testclient von Starlette gibt jeder WebSocket-Sitzung ihren eigenen
# Ereignisloop und raeumt ihn beim Verlassen des Blocks ab - mitsamt allem,
# was darin gestartet wurde. Genau die Faehigkeit, die hier geprueft werden
# soll, kann er also gar nicht zeigen: jeder Lauf endete unter ihm als
# "Abgebrochen.", obwohl der Code richtig war.
#
# Das ist der Unterschied zwischen "der Test ist rot" und "der Code ist
# kaputt". Wer das verwechselt, baut die falsche Sache um.
import socket  # noqa: E402
import uvicorn  # noqa: E402
import websockets  # noqa: E402

_tor = threading.Event()

async def _langsamer_lauf(send, prompt, **kw):
    await send("status", "erster Schritt")
    # threading.Event statt asyncio.Event: gesetzt wird es aus dem Testfaden,
    # und asyncio.Event ist ueber Fadengrenzen hinweg nicht sicher.
    while not _tor.is_set():
        await asyncio.sleep(0.01)
    await send("status", "zweiter Schritt")
    await send("done", "Fertig.", ok=True, exit=0, attempts=1, seconds=0.2)

_frei = socket.socket()
_frei.bind(("127.0.0.1", 0))
_PORT = _frei.getsockname()[1]
_frei.close()

_echtes_dispatch = main.dispatch
main.dispatch = _langsamer_lauf
_server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=_PORT,
                                        log_level="error"))
threading.Thread(target=_server.run, daemon=True).start()
for _ in range(200):
    if getattr(_server, "started", False):
        break
    time.sleep(0.05)

_URL = f"ws://127.0.0.1:{_PORT}/ws/agent"

async def _reden(nutzlast, bis=2, timeout=10):
    """Verbindet, schickt eine Nachricht, liest Ereignisse.

    bis: Anzahl der Ereignisse, oder 'done' fuer 'bis zum Abschluss'.
    """
    gelesen = []
    async with websockets.connect(_URL) as w:
        await w.send(json.dumps(nutzlast))
        while True:
            try:
                ev = json.loads(await asyncio.wait_for(w.recv(), timeout))
            except Exception:
                break
            gelesen.append(ev)
            if bis == "done" and ev["type"] == "done":
                break
            if isinstance(bis, int) and len(gelesen) >= bis:
                break
            if ev["type"] == "error" and ev.get("code"):
                break
    return gelesen

try:
    check("der Testserver ist oben", getattr(_server, "started", False))

    # 1. Auftrag starten und mittendrin die Verbindung kappen.
    _erste = asyncio.run(_reden({"token": "geheim-test-token", "prompt": "dauert"}, bis=2))
    _lauf_id = _erste[0].get("lauf")
    _weiter_ab = _erste[-1]["i"] + 1

    check("jedes Ereignis trägt die Laufkennung",
          all(e.get("lauf") for e in _erste), _erste)
    check("jedes Ereignis trägt eine laufende Nummer",
          [e["i"] for e in _erste] == [0, 1], [e.get("i") for e in _erste])

    # 2. Der Server arbeitet weiter, obwohl niemand mehr zusieht.
    time.sleep(0.3)
    _l = main.runs.register.holen(_lauf_id)
    check("der Lauf lebt nach dem Verbindungsabbruch weiter",
          bool(_l) and not _l.fertig, _l and _l.ereignisse[-1:])

    _tor.set()
    for _ in range(300):
        _l = main.runs.register.holen(_lauf_id)
        if _l and _l.fertig:
            break
        time.sleep(0.01)
    check("und läuft ohne Zuschauer zu Ende", bool(_l and _l.fertig), _l)

    # 3. Wieder anhaengen - ab der Stelle, an der wir waren.
    _rest = asyncio.run(_reden({"token": "geheim-test-token",
                                "attach": _lauf_id, "from": _weiter_ab}, bis="done"))
    check("beim erneuten Anhängen kommt der Rest",
          [e["text"] for e in _rest][:1] == ["zweiter Schritt"], _rest)
    check("und das Ergebnis kommt an",
          _rest[-1]["type"] == "done" and _rest[-1]["ok"] is True, _rest[-1])
    check("nichts wird doppelt geschickt",
          all(e["i"] >= _weiter_ab for e in _rest), [e["i"] for e in _rest])

    # 4. Ein Lauf, den es nicht gibt, wird benannt statt verschwiegen.
    _ev = asyncio.run(_reden({"token": "geheim-test-token",
                              "attach": "gibtsnicht", "from": 0}, bis=1))[0]
    check("ein unbekannter Lauf wird als solcher gemeldet",
          _ev["type"] == "error" and _ev.get("code") == "unbekannt", _ev)

    # 5. Abbrechen muss wirklich abbrechen - der Lauf haengt ja nicht mehr an
    #    der Verbindung, ein Wegsehen beendet ihn also nicht mehr.
    _tor.clear()
    _start = asyncio.run(_reden({"token": "geheim-test-token", "prompt": "dauert"}, bis=2))
    _abbruch_id = _start[0]["lauf"]
    _ende = asyncio.run(_reden({"token": "geheim-test-token",
                                "cancel": _abbruch_id, "from": 0}, bis="done"))[-1]
    check("Abbrechen beendet den Lauf wirklich",
          _ende["type"] == "done" and _ende["ok"] is False, _ende)
    check("und sagt, dass abgebrochen wurde", "bgebrochen" in _ende["text"], _ende)
finally:
    main.dispatch = _echtes_dispatch
    _tor.set()
    _server.should_exit = True
    time.sleep(0.3)

# Die Oberflaeche muss das auch benutzen - sonst ist die Faehigkeit da und
# niemand ruft sie ab.
# Der Aktualisierungsbefehl. Anlass war ein echter Fehlschlag: die Anleitung
# begann mit 'cd' ins Quellverzeichnis, das ein frueherer Lauf geloescht
# hatte. Die Zeile brach sofort ab, alles dahinter passierte nie - und weil
# sofort wieder ein Prompt kam, sah es aus, als sei es gelaufen.
_upd_pfad = main.BASE_DIR.parent / "deploy" / "update.sh"
check("es gibt einen Aktualisierungsbefehl", _upd_pfad.exists(), str(_upd_pfad))
if _upd_pfad.exists():
    _upd = _upd_pfad.read_text()
    check("er setzt kein vorhandenes Quellverzeichnis voraus",
          'rm -rf "$SRC"' in _upd and "git clone" in _upd)
    check("er faengt ein fehlendes git ab", "command -v git" in _upd)
    check("er laeuft losgeloest vom Terminal weiter",
          "setsid" in _upd and "nohup" in _upd)
    check("er nennt den eingespielten Stand", "rev-parse --short HEAD" in _upd)
    check("er sagt, wo man nachsieht", "tail -f" in _upd)
    check("er besteht auf root statt halb zu laufen",
          '"$(id -u)" -ne 0' in _upd)
    _inst = (main.BASE_DIR.parent / "install.sh").read_text()
    check("der Installer legt ihn als Befehl ab",
          "/usr/local/bin/braunycode-update" in _inst)

check("die Oberfläche merkt sich den laufenden Auftrag", "brauny.run" in js)
check("sie hängt sich beim Zurückkommen wieder an",
      "visibilitychange" in js and "wiederanhaengen" in js)
check("sie zählt mit, wo sie war", "ev.i" in js and "attach" in js)
check("der Abbruch geht an den Server, statt nur wegzusehen", "cancel:" in js)


print(f"\n=== {ok} bestanden, {fail} fehlgeschlagen ===")
sys.exit(1 if fail else 0)
