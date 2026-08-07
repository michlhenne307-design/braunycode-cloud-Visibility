"""BraunyCode Cloud - FastAPI-Backend fuer den iPhone-Agenten."""

import asyncio
import hmac
import json
import logging
import os
import re
import time
from pathlib import Path

import ollama
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import sandbox

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("brauny")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
ICON_DIR = STATIC_DIR / "icons"

MODEL = os.environ.get("BRAUNY_MODEL", "llama3.1:8b")
TOKEN = os.environ.get("BRAUNY_TOKEN", "")
MAX_PROMPT = int(os.environ.get("BRAUNY_MAX_PROMPT", "2000"))

app = FastAPI(title="BraunyCode Cloud", version="1.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

CODE_FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.S)


def extract_code(text: str) -> str:
    """Holt den Code aus einer Markdown-Antwort.

    Ohne das landen die ```-Zaeune im File und die Sandbox stirbt sofort
    mit einem SyntaxError.
    """
    matches = CODE_FENCE.findall(text)
    if matches:
        return max(matches, key=len).strip()
    return text.strip()


def message_content(response) -> str:
    """Liest den Text aus einer ollama-Antwort, egal welche Client-Version."""
    try:
        return response["message"]["content"]
    except (TypeError, KeyError):
        return response.message.content


async def ask(prompt: str) -> str:
    response = await asyncio.to_thread(
        ollama.chat,
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
    )
    return message_content(response)


# ------------------------------------------------------------------ Seiten

@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/manifest.json")
async def manifest():
    return FileResponse(STATIC_DIR / "manifest.json", media_type="application/manifest+json")


@app.get("/sw.js")
async def service_worker():
    # Scope "/" erfordert die Auslieferung von der Wurzel, nicht aus /static.
    # no-store, damit ein Update nicht hinter einem alten Worker haengen bleibt.
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="text/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-store"},
    )


@app.get("/apple-touch-icon.png")
async def apple_icon():
    return FileResponse(ICON_DIR / "apple-touch-icon.png", media_type="image/png")


@app.get("/icons/{name}")
async def icon(name: str):
    # Feste Liste statt Pfadverkettung - sonst waere /icons/../../etc/passwd offen.
    allowed = {"icon-192.png", "icon-512.png", "icon-maskable-512.png", "apple-touch-icon.png"}
    if name not in allowed:
        raise HTTPException(status_code=404)
    return FileResponse(ICON_DIR / name, media_type="image/png")


@app.get("/healthz")
async def healthz():
    status = {"model": MODEL, "auth": bool(TOKEN), "version": app.version}
    try:
        await asyncio.to_thread(ollama.list)
        status["ollama"] = "ok"
    except Exception as exc:
        status["ollama"] = f"fehler: {exc}"
    try:
        await asyncio.to_thread(sandbox.client().ping)
        status["docker"] = "ok"
    except Exception as exc:
        status["docker"] = f"fehler: {exc}"
    healthy = status.get("ollama") == "ok" and status.get("docker") == "ok"
    return JSONResponse(status, status_code=200 if healthy else 503)


# ------------------------------------------------------------------ Agent

@app.websocket("/ws/agent")
async def agent(ws: WebSocket):
    await ws.accept()
    container = None
    project_dir = None
    started = time.monotonic()

    async def send(event_type: str, text: str = "", **extra):
        await ws.send_text(json.dumps({"type": event_type, "text": text, **extra}))

    try:
        try:
            request = json.loads(await ws.receive_text())
            prompt = str(request.get("prompt", "")).strip()[:MAX_PROMPT]
            supplied = str(request.get("token", ""))
        except (json.JSONDecodeError, AttributeError, TypeError):
            await send("error", "Ungueltige Anfrage - JSON mit token und prompt erwartet.")
            await ws.close(code=1003)
            return

        if TOKEN and not hmac.compare_digest(supplied, TOKEN):
            # compare_digest statt == : keine Rueckschluesse ueber die Laufzeit
            await send("error", "Token abgelehnt.", code="auth")
            await ws.close(code=1008)
            return

        if not prompt:
            await send("error", "Leerer Auftrag.")
            return

        log.info("Auftrag: %s", prompt[:120])
        await send("status", f"Modell {MODEL} — Auftrag angenommen.")

        await send("status", "Plane Architektur …")
        plan = await ask(
            f"Du bist ein Software-Architekt. Aufgabe: {prompt}\n"
            "Antworte kurz und ausschliesslich als JSON mit den Schluesseln "
            '"sprache", "dateien", "schritte".'
        )
        await send("plan", plan)

        await send("status", "Schreibe Code …")
        raw = await ask(
            f"Plan:\n{plan}\n\nSchreibe dazu eine einzelne, sofort lauffaehige "
            "Python-Datei main.py. Nur Standardbibliothek, keine externen Pakete, "
            "kein Netzwerkzugriff, keine Benutzereingabe. Das Programm muss von "
            "selbst terminieren und sein Ergebnis mit print ausgeben. "
            "Antworte nur mit dem Code in einem ```python-Block."
        )
        code = extract_code(raw)
        if not code:
            await send("error", "Das Modell hat keinen Code geliefert.")
            await send("done", "Abgebrochen.", ok=False, exit=-1,
                       seconds=round(time.monotonic() - started, 1))
            return

        await send("code", code, lang="python")

        project_dir = await asyncio.to_thread(sandbox.make_project_dir, {"main.py": code})
        await send("status", "Starte isolierte Sandbox (kein Netzwerk, 512 MB, 1 CPU) …")
        container = await asyncio.to_thread(sandbox.start, project_dir)

        try:
            async for line in sandbox.stream_logs(container):
                await send("sandbox", line)
        except asyncio.TimeoutError:
            await send("error", f"Timeout nach {sandbox.TIMEOUT}s — Container gestoppt.")
            await send("done", "Abgebrochen.", ok=False, exit=-1,
                       seconds=round(time.monotonic() - started, 1))
            return

        result = await asyncio.to_thread(container.wait, timeout=10)
        exit_code = result.get("StatusCode", -1) if isinstance(result, dict) else -1
        seconds = round(time.monotonic() - started, 1)
        if exit_code == 0:
            await send("done", f"Sauber beendet in {seconds}s.",
                       ok=True, exit=0, seconds=seconds)
        else:
            await send("done", f"Programm endete mit Exit-Code {exit_code}.",
                       ok=False, exit=exit_code, seconds=seconds)

    except WebSocketDisconnect:
        log.info("Client hat die Verbindung getrennt - raeume auf.")
    except Exception as exc:
        log.exception("Agentenlauf fehlgeschlagen")
        try:
            await send("error", f"{type(exc).__name__}: {exc}")
            await send("done", "Fehlgeschlagen.", ok=False, exit=-1,
                      seconds=round(time.monotonic() - started, 1))
        except Exception:
            pass
    finally:
        await asyncio.to_thread(sandbox.cleanup, container, project_dir)
