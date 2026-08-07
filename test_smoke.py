"""Smoke-Tests ohne Docker/Ollama. Aufruf: python test_smoke.py"""
import asyncio
import os
import sys

os.environ["BRAUNY_TOKEN"] = "geheim-test-token"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

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
check("GET / hat Token-Feld", 'id="token"' in r.text)
r = client.get("/healthz")
check("healthz meldet 503 ohne Backends", r.status_code == 503, r.status_code)
check("healthz nennt Modell", r.json().get("model") == "llama3.1:8b")

print("\n[6] WebSocket-Auth")
with client.websocket_connect("/ws/agent") as ws:
    ws.send_text("falsches-token")
    msg = ws.receive_text()
check("weist falsches Token ab", "[FEHLER] Falsches Token." == msg, msg)

with client.websocket_connect("/ws/agent") as ws:
    ws.send_text("geheim-test-token")
    ws.send_text("")
    msg = ws.receive_text()
check("lehnt leeren Auftrag ab", "[FEHLER] Leerer Auftrag." == msg, msg)

with client.websocket_connect("/ws/agent") as ws:
    ws.send_text("geheim-test-token")
    ws.send_text("baue etwas")
    msg = ws.receive_text()
check("korrektes Token passiert das Gate", msg.startswith("[SYSTEM]"), msg)

print(f"\n=== {ok} bestanden, {fail} fehlgeschlagen ===")
sys.exit(1 if fail else 0)
