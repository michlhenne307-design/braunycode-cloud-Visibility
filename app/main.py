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
# Anzahl Versuche inklusive erstem Wurf. 1 = altes Verhalten ohne Reparatur.
MAX_ATTEMPTS = max(1, int(os.environ.get("BRAUNY_MAX_ATTEMPTS", "3")))
# Wie viele Zeichen der Fehlerausgabe zurueck ans Modell gehen. Zu viel
# frisst das Kontextfenster eines 8B-Modells, zu wenig verschluckt den Fehler.
ERROR_TAIL = int(os.environ.get("BRAUNY_ERROR_TAIL", "1500"))

app = FastAPI(title="BraunyCode Cloud", version="1.2.0")
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


# ------------------------------------------------------------------ Prompts

def plan_prompt(task: str) -> str:
    return (
        f"Du bist ein Software-Architekt. Aufgabe: {task}\n"
        "Antworte kurz und ausschliesslich als JSON mit den Schluesseln "
        '"sprache", "dateien", "schritte".'
    )


def code_prompt(task: str, plan: str) -> str:
    return (
        f"Plan:\n{plan}\n\nAufgabe: {task}\n\n"
        "Schreibe dazu eine einzelne, sofort lauffaehige Python-Datei main.py. "
        "Nur Standardbibliothek, keine externen Pakete, kein Netzwerkzugriff, "
        "keine Benutzereingabe (kein input()). Das Programm muss von selbst "
        "terminieren und sein Ergebnis mit print ausgeben. "
        "Antworte nur mit dem Code in einem ```python-Block."
    )


def repair_prompt(task: str, code: str, error: str) -> str:
    """Fuettert dem Modell den gescheiterten Code samt Fehler zurueck.

    Das ist der Kern der Selbstkorrektur: statt nur zu melden 'ging nicht',
    bekommt das Modell den echten Traceback und darf es nochmal versuchen.
    """
    return (
        f"Diese Python-Datei main.py sollte folgende Aufgabe loesen: {task}\n\n"
        f"```python\n{code}\n```\n\n"
        f"Beim Ausfuehren trat dieser Fehler auf:\n\n{error}\n\n"
        "Finde die Ursache und schreibe die komplette, korrigierte main.py neu. "
        "Weiterhin nur Standardbibliothek, kein Netzwerk, keine Eingabe, "
        "muss von selbst terminieren. Antworte nur mit dem Code in einem "
        "```python-Block."
    )


def looks_failed(output: str) -> bool:
    """Erkennt einen Fehler auch dann, wenn der Exit-Code 0 bleibt.

    Manche Programme fangen Exceptions ab und drucken sie nur - der Container
    endet dann sauber, obwohl inhaltlich etwas schiefging.
    """
    markers = ("Traceback (most recent call last)", "SyntaxError:", "Error:")
    return any(m in output for m in markers)


# ------------------------------------------------------------------ Agentenlogik

async def run_agent(send, task, *, ask_fn, run_sandbox, max_attempts=MAX_ATTEMPTS):
    """Plant, generiert und repariert Code, bis er laeuft oder die Versuche aus sind.

    ask_fn und run_sandbox sind hineingereicht, damit die Schleife ohne echtes
    Ollama oder Docker getestet werden kann. run_sandbox(code) fuehrt den Code
    aus, streamt die Ausgabe selbst und liefert (exit_code, gesammelte_ausgabe).
    """
    start = time.monotonic()

    def elapsed():
        return round(time.monotonic() - start, 1)

    await send("status", "Plane Architektur …")
    plan = await ask_fn(plan_prompt(task))
    await send("plan", plan)

    await send("status", "Schreibe Code …")
    code = extract_code(await ask_fn(code_prompt(task, plan)))
    if not code:
        await send("error", "Das Modell hat keinen Code geliefert.")
        await send("done", "Abgebrochen.", ok=False, exit=-1,
                   attempts=0, seconds=elapsed())
        return

    last_exit = -1
    for attempt in range(1, max_attempts + 1):
        await send("code", code, lang="python", attempt=attempt)
        if attempt == 1:
            await send("status", "Starte isolierte Sandbox "
                                 "(kein Netzwerk, 512 MB, 1 CPU) …")
        else:
            await send("status", f"Versuch {attempt}/{max_attempts}: "
                                 "starte korrigierte Fassung …")

        exit_code, output = await run_sandbox(code)
        last_exit = exit_code
        failed = exit_code != 0 or looks_failed(output)

        if not failed:
            note = "" if attempt == 1 else f" (nach {attempt} Versuchen)"
            await send("done", f"Sauber beendet in {elapsed()}s{note}.",
                       ok=True, exit=0, attempts=attempt, seconds=elapsed())
            return

        if attempt < max_attempts:
            await send("status", f"Versuch {attempt} fehlgeschlagen "
                                 f"(Exit {exit_code}). Analysiere Fehler …")
            error_tail = output[-ERROR_TAIL:] if output else f"Exit-Code {exit_code}"
            repaired = extract_code(await ask_fn(repair_prompt(task, code, error_tail)))
            if repaired:
                code = repaired

    await send("done", f"Nach {max_attempts} Versuchen nicht lauffaehig "
                       f"(Exit {last_exit}).",
               ok=False, exit=last_exit, attempts=max_attempts, seconds=elapsed())


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

async def sandbox_runner(send, code: str):
    """Fuehrt einen Codestand in einer frischen Sandbox aus.

    Streamt die Ausgabe als 'sandbox'-Ereignisse und liefert
    (exit_code, gesamte_ausgabe) zurueck. Jeder Aufruf bekommt eigenen
    Container und eigenes Verzeichnis und raeumt beides selbst wieder ab -
    so kann die Reparatur-Schleife den Code beliebig oft neu ausfuehren.
    """
    project_dir = None
    container = None
    lines: list[str] = []
    try:
        project_dir = await asyncio.to_thread(sandbox.make_project_dir, {"main.py": code})
        container = await asyncio.to_thread(sandbox.start, project_dir)
        try:
            async for line in sandbox.stream_logs(container):
                lines.append(line)
                await send("sandbox", line)
        except asyncio.TimeoutError:
            await send("error", f"Timeout nach {sandbox.TIMEOUT}s — Container gestoppt.")
            lines.append(f"[Abbruch] Timeout nach {sandbox.TIMEOUT}s "
                         "(vermutlich Endlosschleife).")
            return -1, "\n".join(lines)

        result = await asyncio.to_thread(container.wait, timeout=10)
        exit_code = result.get("StatusCode", -1) if isinstance(result, dict) else -1
        return exit_code, "\n".join(lines)
    finally:
        await asyncio.to_thread(sandbox.cleanup, container, project_dir)


@app.websocket("/ws/agent")
async def agent(ws: WebSocket):
    await ws.accept()

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
        await send("status", f"Modell {MODEL} — Auftrag angenommen "
                             f"(bis zu {MAX_ATTEMPTS} Versuche).")

        async def run_sandbox(code):
            return await sandbox_runner(send, code)

        await run_agent(send, prompt, ask_fn=ask, run_sandbox=run_sandbox)

    except WebSocketDisconnect:
        log.info("Client hat die Verbindung getrennt - raeume auf.")
    except Exception as exc:
        log.exception("Agentenlauf fehlgeschlagen")
        try:
            await send("error", f"{type(exc).__name__}: {exc}")
            await send("done", "Fehlgeschlagen.", ok=False, exit=-1, attempts=0, seconds=0)
        except Exception:
            pass
