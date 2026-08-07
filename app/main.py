"""BraunyCode Cloud - FastAPI-Backend fuer den iPhone-Agenten."""

import asyncio
import hmac
import logging
import os
import re
from pathlib import Path

import ollama
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

import sandbox

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("brauny")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

MODEL = os.environ.get("BRAUNY_MODEL", "llama3.1:8b")
TOKEN = os.environ.get("BRAUNY_TOKEN", "")
MAX_PROMPT = int(os.environ.get("BRAUNY_MAX_PROMPT", "2000"))

app = FastAPI(title="BraunyCode Cloud", version="1.0.0")

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


@app.get("/")
async def index():
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/healthz")
async def healthz():
    status = {"model": MODEL, "auth": bool(TOKEN)}
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


@app.websocket("/ws/agent")
async def agent(ws: WebSocket):
    await ws.accept()
    container = None
    project_dir = None

    async def say(line: str):
        await ws.send_text(line)

    try:
        first = await ws.receive_text()

        if TOKEN:
            # hmac.compare_digest statt == : keine Rueckschluesse ueber Laufzeit
            if not hmac.compare_digest(first, TOKEN):
                await say("[FEHLER] Falsches Token.")
                await ws.close(code=1008)
                return
            prompt = await ws.receive_text()
        else:
            prompt = first

        prompt = prompt.strip()[:MAX_PROMPT]
        if not prompt:
            await say("[FEHLER] Leerer Auftrag.")
            return

        log.info("Auftrag: %s", prompt[:120])
        await say(f"[SYSTEM] Modell {MODEL}, Auftrag: {prompt}")

        await say("[AGENT] Plane Architektur ...")
        plan = await ask(
            f"Du bist ein Software-Architekt. Aufgabe: {prompt}\n"
            "Antworte kurz und ausschliesslich als JSON mit den Schluesseln "
            '"sprache", "dateien", "schritte".'
        )
        await say(f"[PLAN] {plan}")

        await say("[AGENT] Schreibe Code ...")
        raw = await ask(
            f"Plan:\n{plan}\n\nSchreibe dazu eine einzelne, sofort lauffaehige "
            "Python-Datei main.py. Nur Standardbibliothek, keine externen Pakete, "
            "kein Netzwerkzugriff, keine Benutzereingabe. Das Programm muss von "
            "selbst terminieren und sein Ergebnis mit print ausgeben. "
            "Antworte nur mit dem Code in einem ```python-Block."
        )
        code = extract_code(raw)
        if not code:
            await say("[FEHLER] Modell hat keinen Code geliefert.")
            return

        project_dir = await asyncio.to_thread(sandbox.make_project_dir, {"main.py": code})
        await say(f"[CODE] {len(code)} Zeichen nach main.py geschrieben.")
        for line in code.splitlines():
            await say(f"[CODE] {line}")

        await say("[DOCKER] Starte isolierte Sandbox (kein Netzwerk, 512 MB, 1 CPU) ...")
        container = await asyncio.to_thread(sandbox.start, project_dir)

        try:
            async for line in sandbox.stream_logs(container):
                await say(f"[SANDBOX] {line}")
        except asyncio.TimeoutError:
            await say(f"[ABBRUCH] Timeout nach {sandbox.TIMEOUT}s - Container gestoppt.")
            return

        result = await asyncio.to_thread(container.wait, timeout=10)
        code_exit = result.get("StatusCode", -1) if isinstance(result, dict) else -1
        if code_exit == 0:
            await say("[ERFOLG] Programm sauber beendet (Exit 0).")
        else:
            await say(f"[FEHLGESCHLAGEN] Exit-Code {code_exit}.")

    except WebSocketDisconnect:
        log.info("Client hat die Verbindung getrennt.")
    except Exception as exc:
        log.exception("Agentenlauf fehlgeschlagen")
        try:
            await ws.send_text(f"[FEHLER] {type(exc).__name__}: {exc}")
        except Exception:
            pass
    finally:
        await asyncio.to_thread(sandbox.cleanup, container, project_dir)
