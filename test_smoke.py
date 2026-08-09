"""Smoke-Tests ohne Docker/Ollama. Aufruf: python test_smoke.py"""
import asyncio
import json
import os
import struct
import subprocess
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

class FakeFunction:
    name = "read_file"
    arguments = '{"path": "main.py"}'
class FakeCall:
    function = FakeFunction()
class AttrResp:
    class message:
        content = "attr-stil"
        tool_calls = [FakeCall()]

def fake_ollama_chat(response):
    """Setzt ollama.chat voruebergehend auf eine feste Antwort."""
    import ollama
    orig = ollama.chat
    ollama.chat = lambda **kw: response
    try:
        return provider._chat_ollama([{"role": "user", "content": "x"}], None)
    finally:
        ollama.chat = orig

r = fake_ollama_chat({"message": {"content": "dict-stil"}})
check("dict-Zugriff", r.text == "dict-stil", r)
check("dict ohne tool_calls", r.tool_calls == [])

r = fake_ollama_chat(AttrResp())
check("attribut-Zugriff", r.text == "attr-stil", r)
check("Werkzeugaufruf erkannt", len(r.tool_calls) == 1 and
      r.tool_calls[0].name == "read_file", r.tool_calls)
check("JSON-Argumente geparst",
      r.tool_calls[0].arguments == {"path": "main.py"}, r.tool_calls[0].arguments)

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
check("GET / enthaelt UI", "BraunyCode Control" in r.text)
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
for element in ["panel-log", "panel-code", "panel-out", "btn-run", "gate", "token"]:
    check(f"app.js-Ziel #{element} existiert im HTML", f'id="{element}"' in html)

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

    async def run_sandbox(code):
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

    async def run_sandbox(code):
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
check("sieben Werkzeuge", len(sch) == 7, len(sch))
check("OpenAI-Format", all(t["type"] == "function" and "name" in t["function"]
                          and "parameters" in t["function"] for t in sch))
check("Pflichtfelder deklariert",
      {t["function"]["name"]: t["function"]["parameters"]["required"]
       for t in sch}["write_file"] == ["path", "content"])

tws = ws_mod.Workspace(tempfile.mkdtemp())
tws.write("main.py", "def gruss():\n    return 'hallo'\n")
tws.write("hilfe.py", "WERT = 42\n")

sandbox_calls = []
async def fake_sandbox(code):
    sandbox_calls.append(code)
    return (0, "hallo")

box = tools.Toolbox(tws, run_sandbox=fake_sandbox,
                    index_builder=codeindex.CodeIndex.build)

def call(name, **args):
    return asyncio.run(box.call(name, args))

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

check("finish setzt die Zusammenfassung",
      call("finish", summary="alles gut") == "alles gut" and box.finished == "alles gut")

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

    async def run_sandbox(_code):
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

# finish ohne Ausfuehrung: das muss ausdruecklich dazugesagt werden
outcome, events, w, _ = drive_loop([
    reply(calls=[("write_file", {"path": "main.py", "content": "print(1)\n"})]),
    reply(calls=[("finish", {"summary": "Fertig."})]),
])
done = next(e for e in events if e["type"] == "done")
check("ohne Ausführung nicht als geprüft markiert", done.get("verified") is False, done)
check("Hinweis auf fehlende Ausführung",
      any("nicht ausgeführt" in e["text"] for e in events if e["type"] == "error"),
      [e["text"] for e in events if e["type"] == "error"])

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

    async def run_sandbox(_c):
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
open(os.path.join(sdir, "zu_gross.md"), "w").write(
    "---\nname: gross\n---\n" + "x" * (skills_mod.MAX_SKILL_BYTES + 10))
open(os.path.join(sdir, "gut.md"), "w").write("---\nname: gut\n---\nText hier.")
open(os.path.join(sdir, "keine.txt"), "w").write("---\nname: nein\n---\nText.")
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

try:
    connectors.git_push(pws, "x", "y")
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
check("ohne Konnektoren nur Basiswerkzeuge", len(leer.schema()) == 7, len(leer.schema()))
check("schema() ohne Argument kennt keine Konnektoren",
      {t["function"]["name"] for t in tools.schema()} == tools.BASE_NAMES)
check("fetch_url nicht aufrufbar",
      asyncio.run(leer.call("fetch_url", {"url": "https://x.de"}))
      .startswith("FEHLER"))

mit = tools.Toolbox(fws, enabled=["fetch_url"])
check("freigeschalteter Konnektor erscheint", len(mit.schema()) == 8, len(mit.schema()))
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
      len(tools.Toolbox(fws).schema()) == 7)

# Skill im Agentenlauf
def drive_skill(script, skill, files=None):
    w = ws_mod.Workspace(tempfile.mkdtemp())
    for name, body in (files or {"main.py": "print('alt')\n"}).items():
        w.write(name, body)
    w.git_commit("Start")
    events, gesehen = [], []

    async def send(t, text="", **extra):
        events.append({"type": t, "text": text, **extra})

    async def run_sandbox(_c):
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
    check("PyYAML für die Prüfung vorhanden", False, "übersprungen")

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

installer = (main.BASE_DIR.parent / "install.sh").read_text()
check("Installer übernimmt ein vorgegebenes Token",
      'if [ -n "${BRAUNY_TOKEN:-}" ]' in installer)
check("Installer lehnt zu kurze Token ab",
      '"${#TOKEN}" -ge 12' in installer, )
check("Installer läuft weiterhin nicht als root",
      '[ "$(id -u)" -ne 0 ]' in installer)

print(f"\n=== {ok} bestanden, {fail} fehlgeschlagen ===")
sys.exit(1 if fail else 0)
