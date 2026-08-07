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
check("healthz nennt Modell", r.json().get("model") == "llama3.1:8b")

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
check("erste Meldung nennt das Modell", "llama3.1:8b" in ev["text"], ev)

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

print(f"\n=== {ok} bestanden, {fail} fehlgeschlagen ===")
sys.exit(1 if fail else 0)
