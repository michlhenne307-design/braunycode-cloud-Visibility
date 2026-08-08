"""Smoke-Tests ohne Docker/Ollama. Aufruf: python test_smoke.py"""
import asyncio
import json
import os
import struct
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

print("\n[2] message_content (beide ollama-Client-Formen)")
class AttrResp:
    class message:
        content = "attr-stil"
check("dict-Zugriff", main.message_content({"message": {"content": "dict-stil"}}) == "dict-stil")
check("attribut-Zugriff", main.message_content(AttrResp()) == "attr-stil")

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
check("healthz nennt Modell", r.json().get("model") == "qwen2.5-coder:7b")

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

print(f"\n=== {ok} bestanden, {fail} fehlgeschlagen ===")
sys.exit(1 if fail else 0)
