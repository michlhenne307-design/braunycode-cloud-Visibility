"""BraunyCode Cloud - FastAPI-Backend fuer den iPhone-Agenten."""

import ast
import asyncio
import hmac
import json
import logging
import os
import re
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import agentloop
import codeindex
import memory
import connectors
import provider
import refactor
import runs
import sandbox
import skills as skills_mod
import tools
import workspace as ws_mod

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("brauny")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
ICON_DIR = STATIC_DIR / "icons"

MODEL = os.environ.get("BRAUNY_MODEL", "qwen2.5-coder:7b")
TOKEN = os.environ.get("BRAUNY_TOKEN", "")
MAX_PROMPT = int(os.environ.get("BRAUNY_MAX_PROMPT", "2000"))
# Anzahl Versuche inklusive erstem Wurf. 1 = altes Verhalten ohne Reparatur.
MAX_ATTEMPTS = max(1, int(os.environ.get("BRAUNY_MAX_ATTEMPTS", "3")))
# Wie viele Zeichen der Fehlerausgabe zurueck ans Modell gehen. Zu viel
# frisst das Kontextfenster eines kleinen Modells, zu wenig verschluckt den Fehler.
ERROR_TAIL = int(os.environ.get("BRAUNY_ERROR_TAIL", "1500"))

# Gleichzeitige Laeufe. Jeder Lauf haelt einen Container und beschaeftigt das
# Modell - ohne Deckel legen ein paar parallele Auftraege die Maschine lahm.
MAX_CONCURRENT = max(1, int(os.environ.get("BRAUNY_MAX_CONCURRENT", "2")))
# Obergrenze fuer einen einzelnen Modellaufruf. Ohne das haengt ein blockiertes
# Ollama die WebSocket-Verbindung endlos.
ASK_TIMEOUT = int(os.environ.get("BRAUNY_ASK_TIMEOUT", "300"))
# Hoechstzahl Ausgabezeilen eines Sandbox-Laufs. Mehr sieht sich niemand an,
# und im Modellkontext verdraengt eine ausser Rand und Band geratene Ausgabe
# alles, was zur Loesung noetig waere.
MAX_SANDBOX_ZEILEN = max(20, int(os.environ.get("BRAUNY_MAX_SANDBOX_ZEILEN", "200")))
# Schutz gegen Token-Raten: nach so vielen Fehlversuchen innerhalb des
# Zeitfensters wird die Adresse voruebergehend abgewiesen.
AUTH_MAX_FAILS = max(1, int(os.environ.get("BRAUNY_AUTH_MAX_FAILS", "5")))
AUTH_WINDOW = int(os.environ.get("BRAUNY_AUTH_WINDOW", "300"))
# Projektverzeichnis, in dem der Agent arbeitet und seine Historie fuehrt.
WORKSPACE_ROOT = os.environ.get("BRAUNY_WORKSPACE", str(BASE_DIR.parent / "workspace"))
# Arbeitsweise: "auto"    - Werkzeugschleife, faellt bei Modellen ohne
#                           Werkzeugunterstuetzung auf den einfachen Weg zurueck
#               "tools"   - nur Werkzeugschleife
#               "oneshot" - nur der alte Weg: planen, schreiben, ausfuehren
AGENT_MODE = os.environ.get("BRAUNY_AGENT", "auto").strip().lower()
# Verzeichnis mit Skill-Dateien (Verfahrenswissen als Markdown).
SKILL_DIR = os.environ.get("BRAUNY_SKILLS", str(BASE_DIR.parent / "skills"))

app = FastAPI(title="BraunyCode Cloud", version="1.7.0")

# Einmal beim Start lesen - Skills aendern sich nicht waehrend eines Laufs.
SKILLS = skills_mod.load(SKILL_DIR)

# Begrenzt die gleichzeitig laufenden Auftraege.
run_slots = asyncio.Semaphore(MAX_CONCURRENT)

# Projekt, an dem gearbeitet wird. Faellt auf None zurueck, wenn das
# Verzeichnis nicht angelegt werden kann - der Agent laeuft dann wie bisher
# ohne Historie weiter.
try:
    WORKSPACE = ws_mod.Workspace(WORKSPACE_ROOT)
except OSError as exc:
    logging.getLogger("brauny").warning("Kein Projektverzeichnis: %s", exc)
    WORKSPACE = None
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


async def model_chat(messages, schema=None, on_text=None):
    """Ein Modellaufruf mit Zeitlimit, egal ob lokal oder ueber eine API.

    wait_for beendet den Hintergrund-Thread nicht - der laeuft aus. Es loest
    aber die Verbindung, statt sie unbegrenzt haengen zu lassen.

    on_text bekommt Textstuecke, waehrend sie entstehen. Der Modellaufruf
    laeuft in einem Arbeitsfaden; von dort darf nicht in den Ereignisloop
    geschrieben werden.

    Die Bruecke ist bewusst EIN Ausgeber, der der Reihe nach leert - nicht je
    Stueck eine eigene Aufgabe. Aufgaben laufen in der Reihenfolge, in der der
    Loop sie drannimmt, und das letzte Stueck wird am Ende direkt abgewartet:
    damit koennte es vor einem frueheren erscheinen und der Text waere
    durcheinander. Ein Ausgeber, eine Warteschlange, keine Ueberholspur.
    """
    if on_text is None:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(provider.chat, messages, schema),
                timeout=ASK_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Das Modell hat nach {ASK_TIMEOUT}s nicht geantwortet."
            ) from None

    loop = asyncio.get_running_loop()
    puffer: list[str] = []
    wecker = asyncio.Event()
    fertig = False

    def bruecke(stueck: str):
        """Laeuft im Arbeitsfaden."""
        puffer.append(stueck)
        loop.call_soon_threadsafe(wecker.set)

    async def ausliefern():
        while True:
            await wecker.wait()
            wecker.clear()
            # Alles auf einmal nehmen, was inzwischen da ist: das buendelt von
            # selbst, ohne feste Groesse und ohne Wartezeit.
            if puffer:
                stueck, puffer[:] = "".join(puffer), []
                await on_text(stueck)
            if fertig and not puffer:
                return

    ausgeber = asyncio.create_task(ausliefern())
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(provider.chat, messages, schema,
                              provider.DETERMINISTISCH, bruecke),
            timeout=ASK_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise TimeoutError(
            f"Das Modell hat nach {ASK_TIMEOUT}s nicht geantwortet."
        ) from None
    finally:
        fertig = True
        wecker.set()
        try:
            await ausgeber
        except Exception:
            log.exception("Textausgabe fehlgeschlagen - der Lauf geht weiter.")


async def ask(prompt: str) -> str:
    """Einzelfrage ohne Werkzeuge - der einfache Weg."""
    reply = await model_chat([{"role": "user", "content": prompt}])
    return reply.text


# ------------------------------------------------------------------ Auth-Bremse

# Adresse -> Zeitpunkte der letzten Fehlversuche
_auth_fails: dict[str, list[float]] = {}


def _prune_fails(key: str, now: float) -> list[float]:
    recent = [t for t in _auth_fails.get(key, []) if now - t < AUTH_WINDOW]
    if recent:
        _auth_fails[key] = recent
    else:
        _auth_fails.pop(key, None)
    return recent


def auth_blocked(key: str, now=None) -> bool:
    """True, wenn diese Adresse gerade zu viele Fehlversuche hatte."""
    return len(_prune_fails(key, now or time.monotonic())) >= AUTH_MAX_FAILS


def record_auth_fail(key: str, now=None) -> None:
    now = now or time.monotonic()
    recent = _prune_fails(key, now)
    # Deckel, damit ein Dauerbeschuss den Speicher nicht vollschreibt
    _auth_fails[key] = (recent + [now])[-AUTH_MAX_FAILS:]


def clear_auth_fails(key: str) -> None:
    """Nach erfolgreicher Anmeldung die Zaehler dieser Adresse loeschen."""
    _auth_fails.pop(key, None)


# ------------------------------------------------------------------ Prompts

def plan_prompt(task: str, context: str = "") -> str:
    # Der Projektkontext kommt VOR die Aufgabe: kleine Modelle gewichten den
    # Anfang eines Prompts staerker.
    head = f"Bestehendes Projekt:\n{context}\n\n" if context else ""
    return (
        f"{head}Du bist ein Software-Architekt. Aufgabe: {task}\n"
        "Antworte kurz und ausschliesslich als JSON mit den Schluesseln "
        '"sprache", "dateien", "schritte".'
    )


def code_prompt(task: str, plan: str, context: str = "") -> str:
    head = f"Bestehendes Projekt:\n{context}\n\n" if context else ""
    return (
        f"{head}Plan:\n{plan}\n\nAufgabe: {task}\n\n"
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


def syntax_error(code: str):
    """Prueft den Code auf Syntaxfehler, bevor eine Sandbox startet.

    Syntaxfehler sind der haeufigste Grund, warum generierter Code nicht
    laeuft. Sie hier abzufangen spart den Start eines Containers und liefert
    dem Modell sofort eine praezise Meldung fuer die Reparatur. Gibt die
    Fehlermeldung als Text zurueck oder None, wenn der Code parst.
    """
    try:
        ast.parse(code)
        return None
    except SyntaxError as exc:
        return f"SyntaxError: {exc.msg} (Zeile {exc.lineno})"


# ------------------------------------------------------------------ Agentenlogik

def pick_target(ws) -> str | None:
    """Waehlt die Datei, auf die sich ein mechanischer Auftrag bezieht.

    Bewusst eng: nur bei main.py oder genau einer Python-Datei. Bei mehreren
    Kandidaten ist die Zuordnung nicht eindeutig - dann lieber nichts anfassen.
    """
    files = [f for f in ws.list_files() if f.endswith(".py")]
    if "main.py" in files:
        return "main.py"
    return files[0] if len(files) == 1 else None


async def try_mechanical(send, task, ws) -> bool:
    """Deterministischer Schnellweg. True, wenn der Auftrag erledigt wurde.

    Mechanische Aenderungen - umbenennen, Docstrings, tote Importe - laufen
    ueber den Syntaxbaum statt ueber das Modell: in Millisekunden, ohne
    Rechenlast, mit reproduzierbarem Ergebnis.
    """
    if ws is None:
        return False
    mech = refactor.classify(task)
    if mech is None:
        return False

    target = pick_target(ws)
    if target is None:
        await send("status", f"Mechanischer Auftrag erkannt ({mech.label}), aber "
                             "keine eindeutige Zieldatei — gehe den normalen Weg.")
        return False

    await send("status", f"Mechanischer Auftrag erkannt: {mech.label}. "
                         "Kein Modell nötig.")
    start = time.monotonic()
    try:
        before = ws.read(target)
    except Exception as exc:
        await send("status", f"Konnte {target} nicht lesen ({exc}) — normaler Weg.")
        return False

    result = await asyncio.to_thread(refactor.apply, mech, before)
    if not result.changed:
        await send("status", f"{result.summary} Nichts zu ändern.")
        await send("done", result.summary, ok=True, exit=0, attempts=0,
                   seconds=round(time.monotonic() - start, 1), mechanical=True)
        return True

    ws.write(target, result.code)

    # Umbenannt wird nur in der Zieldatei. Verweist eine andere Datei noch auf
    # den alten Namen, bricht das Projekt - das muss gemeldet werden, statt es
    # als sauberen Erfolg zu verkaufen.
    warning = ""
    if mech.kind == "rename":
        index = await asyncio.to_thread(codeindex.CodeIndex.build, ws)
        stale = [s for s in index.callers(mech.params["old"]) if s.path != target]
        if stale:
            places = ", ".join(sorted({f"{s.path}:{s.line}" for s in stale})[:5])
            warning = (f" ACHTUNG: '{mech.params['old']}' wird noch verwendet in "
                       f"{places} — dort nicht mit umbenannt.")
            await send("error", warning.strip())

    sha = await asyncio.to_thread(ws.git_commit, f"{mech.label} ({target})")
    await send("code", result.code, lang="python", attempt=1, path=target)
    await send("sandbox", result.summary + warning)
    note = f" Commit {sha}." if sha else ""
    await send("done", f"{result.summary}{note}", ok=True, exit=0, attempts=0,
               seconds=round(time.monotonic() - start, 1), mechanical=True)
    return True


async def run_agent(send, task, *, ask_fn, run_sandbox, workspace=None,
                    max_attempts=MAX_ATTEMPTS, skip_mechanical=False):
    """Plant, generiert und repariert Code, bis er laeuft oder die Versuche aus sind.

    ask_fn und run_sandbox sind hineingereicht, damit die Schleife ohne echtes
    Ollama oder Docker getestet werden kann. run_sandbox(dateien, start) fuehrt
    das Projekt aus, streamt die Ausgabe selbst und liefert
    (exit_code, gesammelte_ausgabe).
    """
    start = time.monotonic()

    def elapsed():
        return round(time.monotonic() - start, 1)

    # Erst der deterministische Weg - er kostet nichts und ist exakt.
    # dispatch() hat ihn ggf. schon probiert und setzt dann skip_mechanical,
    # damit die Meldungen nicht doppelt erscheinen.
    if not skip_mechanical and await try_mechanical(send, task, workspace):
        return

    # Was das Modell nicht wissen kann: dieses Projekt. Nur die relevanten
    # Stellen, nicht Dokumentation, die es ohnehin kennt.
    context = ""
    if workspace is not None:
        index = await asyncio.to_thread(codeindex.CodeIndex.build, workspace)
        context = index.context_for(task)
        if context:
            await send("status", f"Projektkontext: {len(index.symbols)} Symbol(e) "
                                 f"aus {len(index.files)} Datei(en) berücksichtigt.")

    await send("status", "Plane Architektur …")
    plan = await ask_fn(plan_prompt(task, context))
    await send("plan", plan)

    await send("status", "Schreibe Code …")
    code = extract_code(await ask_fn(code_prompt(task, plan, context)))
    if not code:
        await send("error", "Das Modell hat keinen Code geliefert.")
        await send("done", "Abgebrochen.", ok=False, exit=-1,
                   attempts=0, seconds=elapsed())
        return

    last_exit = -1
    for attempt in range(1, max_attempts + 1):
        await send("code", code, lang="python", attempt=attempt)

        # Syntax zuerst pruefen: ist der Code gar nicht lauffaehig, sparen wir
        # den teuren Container-Start und reparieren sofort.
        syntax = syntax_error(code)
        if syntax:
            await send("status", "Syntaxfehler erkannt — überspringe Sandbox, "
                                 "repariere direkt.")
            await send("sandbox", syntax)
            exit_code, output = 1, syntax
        else:
            if attempt == 1:
                await send("status", "Starte isolierte Sandbox "
                                     "(kein Netzwerk, 512 MB, 1 CPU) …")
            else:
                await send("status", f"Versuch {attempt}/{max_attempts}: "
                                     "starte korrigierte Fassung …")
            # Der Einmalwurf erzeugt bewusst genau eine Datei.
            exit_code, output = await run_sandbox({"main.py": code}, "main.py")

        last_exit = exit_code
        failed = exit_code != 0 or looks_failed(output)

        if not failed:
            note = "" if attempt == 1 else f" (nach {attempt} Versuchen)"
            commit = ""
            if workspace is not None:
                # Erst wenn der Code nachweislich laeuft, landet er im Projekt.
                workspace.write("main.py", code)
                sha = await asyncio.to_thread(
                    workspace.git_commit, f"Agent: {task[:60]}")
                if sha:
                    commit = f" Commit {sha}."
            await send("done", f"Sauber beendet in {elapsed()}s{note}.{commit}",
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


def _gedaechtnis():
    """Das Fehlergedaechtnis, oder None wenn es sich nicht anlegen laesst.

    Bewusst nicht toedlich: eine nicht beschreibbare Datei darf keinen Lauf
    verhindern. Der Agent arbeitet dann ohne Vorwissen weiter - schlechter,
    aber vollstaendig.
    """
    pfad = os.environ.get("BRAUNY_MEMORY")
    if not pfad:
        basis = os.environ.get("BRAUNY_WORKSPACE") or os.getcwd()
        pfad = os.path.join(os.path.dirname(os.path.abspath(basis)),
                            "gedaechtnis.sqlite")
    try:
        return memory.Gedaechtnis(pfad)
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "Fehlergedächtnis nicht verfügbar (%s) — Lauf ohne Vorwissen.", exc)
        return None


async def dispatch(send, task, *, ask_fn, chat_fn, run_sandbox, workspace=None,
                   mode=None, skills=None, enabled_connectors=None):
    """Waehlt den Weg, der zur Aufgabe und zum Modell passt.

    Drei Wege, vom billigsten zum teuersten:

    1. Mechanisch  - Umbenennen, Docstrings, tote Importe. Ueber den
       Syntaxbaum, in Millisekunden, ohne Modell.
    2. Werkzeuge   - das Modell arbeitet Schritt fuer Schritt am Projekt.
       So arbeiten die grossen Agenten, und nur so werden mehrere Dateien
       und mehrere Runden ueberhaupt moeglich.
    3. Einmalwurf  - planen, Code schreiben, ausfuehren, reparieren. Der
       Rueckfallweg fuer Modelle, die keine Werkzeuge koennen.
    """
    mode = (mode or AGENT_MODE)

    if await try_mechanical(send, task, workspace):
        return "mechanical"

    if mode in ("auto", "tools") and workspace is not None:
        # Nur die Uebersicht als Startpunkt. Den Rest holt sich der Agent
        # selbst mit read_file - das ist genauer als vorab zu raten, was er
        # braucht, und haelt den ersten Prompt klein.
        index = await asyncio.to_thread(codeindex.CodeIndex.build, workspace)
        context = index.overview()

        freigegeben = (connectors.available() if enabled_connectors is None
                       else enabled_connectors)
        toolbox = tools.Toolbox(workspace, run_sandbox=run_sandbox,
                                index_builder=codeindex.CodeIndex.build,
                                enabled=freigegeben,
                                gedaechtnis=_gedaechtnis())

        # Passendes Verfahrenswissen dazustellen, falls eines passt.
        skill = skills_mod.match(task, SKILLS if skills is None else skills)
        if skill is not None:
            await send("status", f"Skill „{skill.name}“: {skill.beschreibung}")
            # Erst einschraenken, DANN melden. Andersherum kuendigte die
            # Meldung Konnektoren an, die der Skill gleich darauf wegnahm.
            toolbox.restrict(skill.werkzeuge)

        offen = sorted(set(freigegeben) & toolbox.allowed)
        hinweis = f" Konnektoren: {', '.join(offen)}." if offen else ""
        await send("status", f"Werkzeugmodus: bis zu {agentloop.MAX_STEPS} "
                             f"Schritte am Projekt, {len(toolbox.schema())} "
                             f"Werkzeuge.{hinweis}")
        outcome = await agentloop.run_tool_agent(
            send, task, chat_fn=chat_fn, toolbox=toolbox, context=context,
            skill=skill, symbole={s.name for s in index.symbols})
        if outcome != "no-tools":
            return outcome
        if mode == "tools":
            # Ausdruecklich nur Werkzeuge gewuenscht - dann kein stiller
            # Wechsel, sondern eine klare Meldung.
            await send("done", "Das Modell unterstützt keine Werkzeugaufrufe. "
                               "BRAUNY_AGENT=auto setzen für den Rückfallweg.",
                       ok=False, exit=-1, attempts=1, seconds=0)
            return "failed"
        await send("status", "Modell ohne Werkzeugunterstützung — "
                             "wechsle auf den einfachen Weg.")

    await run_agent(send, task, ask_fn=ask_fn, run_sandbox=run_sandbox,
                    workspace=workspace, skip_mechanical=True)
    return "oneshot"


# ------------------------------------------------------------------ Start

@app.on_event("startup")
async def on_startup():
    """Raeumt Container auf, die ein frueherer Absturz liegen gelassen hat."""
    log.info("%d Skill(s) geladen aus %s: %s", len(SKILLS), SKILL_DIR,
             ", ".join(s.name for s in SKILLS) or "keine")
    offen = connectors.available()
    log.info("Konnektoren: %s", ", ".join(offen) or "keine (alles abgeschottet)")
    try:
        removed = await asyncio.to_thread(sandbox.reap_orphans)
        if removed:
            log.info("%d verwaiste Sandbox-Container entfernt.", removed)
    except Exception as exc:
        # Docker nicht erreichbar ist beim Start kein Grund, den Dienst
        # nicht zu starten - /healthz meldet das ohnehin.
        log.warning("Aufraeumen verwaister Container fehlgeschlagen: %s", exc)


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
    status = {"model": MODEL, "auth": bool(TOKEN), "version": app.version,
              "modus": AGENT_MODE, "provider": provider.describe(),
              "skills": skills_mod.overview(SKILLS),
              "konnektoren": connectors.describe()}
    if WORKSPACE is not None:
        status["workspace"] = {"pfad": str(WORKSPACE.root),
                               "dateien": len(WORKSPACE.list_files()),
                               "historie": WORKSPACE.git_log(3)}
    model_ok, model_note = await asyncio.to_thread(provider.health)
    status["modell_backend"] = model_note
    try:
        await asyncio.to_thread(sandbox.client().ping)
        status["docker"] = "ok"
    except Exception as exc:
        status["docker"] = f"fehler: {exc}"
    healthy = model_ok and status.get("docker") == "ok"
    return JSONResponse(status, status_code=200 if healthy else 503)


# ------------------------------------------------------------------ Agent

async def sandbox_runner(send, files: dict, entry: str = "main.py", *, command=None):
    """Fuehrt einen Projektstand in einer frischen Sandbox aus.

    Bekommt ALLE Dateien, nicht nur eine: ein Projekt aus mehreren Modulen
    liesse sich sonst nicht ausfuehren, weil der Import ins Leere geht.
    entry bestimmt, welche davon gestartet wird. Alternativ setzt command
    einen beliebigen Befehl - dann wird entry nicht benutzt.

    Streamt die Ausgabe als 'sandbox'-Ereignisse und liefert
    (exit_code, gesamte_ausgabe) zurueck. Jeder Aufruf bekommt eigenen
    Container und eigenes Verzeichnis und raeumt beides selbst wieder ab -
    so kann die Reparatur-Schleife den Code beliebig oft neu ausfuehren.
    """
    project_dir = None
    container = None
    lines: list[str] = []
    try:
        project_dir = await asyncio.to_thread(sandbox.make_project_dir, files)
        container = await asyncio.to_thread(
            sandbox.start, project_dir,
            list(command) if command else sandbox.entry_command(entry))
        try:
            # Deckel auf die Ausgabe. Im Betrieb passiert: das erzeugte
            # Programm hatte ein input()-Menue, in der Sandbox kommt keine
            # Eingabe, die Schleife drehte endlos und schrieb tausende Zeilen
            # in Sekunden. Jede davon wurde ein Ereignis - das Budget des
            # ganzen Laufs war aufgebraucht, und der Auftrag brach ab.
            #
            # Der Deckel muss HIER sitzen, an der Quelle. Er schuetzt drei
            # Dinge auf einmal: das Ereignisprotokoll, den Arbeitsspeicher und
            # vor allem den Modellkontext - die gesammelten Zeilen gehen als
            # Werkzeugergebnis zurueck ans Modell, und zehntausend Zeilen
            # "Unbekannte Auswahl" verdraengen dort alles Nuetzliche.
            gekuerzt = False
            async for line in sandbox.stream_logs(container):
                if len(lines) >= MAX_SANDBOX_ZEILEN:
                    gekuerzt = True
                    break
                lines.append(line)
                await send("sandbox", line)
            if gekuerzt:
                hinweis = (f"[Gekürzt] Mehr als {MAX_SANDBOX_ZEILEN} Ausgabezeilen. "
                           "Läuft das Programm in einer Endlosschleife oder "
                           "wartet es auf eine Eingabe, die es nicht gibt?")
                lines.append(hinweis)
                await send("sandbox", hinweis)
                # Weiterlaufen lassen waere sinnlos: die Ausgabe ist ohnehin
                # abgeschnitten. Der Container wird im finally aufgeraeumt -
                # dieselbe Stelle, die auch der Zeitablauf benutzt.
                return -1, "\n".join(lines)
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


async def _arbeiten(lauf: runs.Lauf) -> None:
    """Fuehrt einen Auftrag aus - unabhaengig davon, ob jemand zusieht.

    Diese Aufgabe haengt an keiner Verbindung. Das ist der ganze Punkt: auf
    einem Telefon verliert man die Verbindung staendig, und ein Auftrag, der
    daran stirbt, ist auf einem Telefon nicht benutzbar.
    """
    async def send(event_type: str, text: str = "", **extra):
        lauf.anhaengen(event_type, text, **extra)

    # Platz im Lauf-Kontingent holen. Ist alles belegt, sofort und
    # verstaendlich abweisen statt die Maschine zu ueberladen.
    try:
        await asyncio.wait_for(run_slots.acquire(), timeout=0.5)
    except asyncio.TimeoutError:
        lauf.anhaengen("error", f"Server ausgelastet ({MAX_CONCURRENT} Aufträge "
                                "laufen bereits). Bitte kurz warten.", code="busy")
        lauf.abschliessen(ok=False, text="Abgewiesen.")
        return

    try:
        await send("status", f"Modell {MODEL} über {provider.PROVIDER} — "
                             "Auftrag angenommen.")

        async def run_sandbox(files, entry="main.py", *, command=None):
            return await sandbox_runner(send, files, entry, command=command)

        await dispatch(send, lauf.prompt, ask_fn=ask, chat_fn=model_chat,
                       run_sandbox=run_sandbox, workspace=WORKSPACE)
    except asyncio.CancelledError:
        lauf.abschliessen(ok=False, text="Abgebrochen.")
        raise
    except Exception as exc:
        log.exception("Agentenlauf fehlgeschlagen")
        lauf.anhaengen("error", f"{type(exc).__name__}: {exc}")
        lauf.abschliessen(ok=False, text="Fehlgeschlagen.")
    finally:
        run_slots.release()
        # Falls dispatch ohne 'done' zurueckkam, darf der Lauf nicht ewig als
        # laufend gelten - sonst wartet die Oberflaeche auf ein Ende, das
        # niemand mehr schickt. abschliessen() ist wirkungslos, wenn schon
        # ein Abschluss da ist.
        lauf.abschliessen(ok=False, text="Ohne Abschluss beendet.")


@app.websocket("/ws/agent")
async def agent(ws: WebSocket):
    await ws.accept()

    async def send_roh(event_type: str, text: str = "", **extra):
        await ws.send_text(json.dumps({"type": event_type, "text": text, **extra}))

    peer = ws.client.host if ws.client else "unbekannt"

    try:
        request = json.loads(await ws.receive_text())
        prompt = str(request.get("prompt", "")).strip()[:MAX_PROMPT]
        supplied = str(request.get("token", ""))
        anhaengen_an = str(request.get("attach", "")).strip()
        abbrechen = str(request.get("cancel", "")).strip()
        ab_index = max(0, int(request.get("from", 0) or 0))
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
        await send_roh("error", "Ungueltige Anfrage - JSON mit token und prompt erwartet.")
        await ws.close(code=1003)
        return

    if TOKEN:
        if auth_blocked(peer):
            log.warning("Zu viele Fehlversuche von %s - abgewiesen.", peer)
            await send_roh("error", "Zu viele Fehlversuche. Bitte etwas warten.",
                           code="auth")
            await ws.close(code=1008)
            return
        # compare_digest statt == : keine Rueckschluesse ueber die Laufzeit
        if not hmac.compare_digest(supplied, TOKEN):
            record_auth_fail(peer)
            log.warning("Token abgelehnt von %s.", peer)
            await send_roh("error", "Token abgelehnt.", code="auth")
            await ws.close(code=1008)
            return
        clear_auth_fails(peer)

    if anhaengen_an or abbrechen:
        lauf = runs.register.holen(anhaengen_an or abbrechen)
        if lauf is not None and abbrechen:
            # Abbrechen heisst jetzt wirklich abbrechen. Frueher genuegte es,
            # die Verbindung zu schliessen - das beendet den Lauf nicht mehr,
            # und ein Knopf, der nichts tut, waere schlimmer als keiner.
            log.info("Abbruch von %s fuer Lauf %s.", peer, lauf.id)
            if lauf.aufgabe is not None and not lauf.aufgabe.done():
                lauf.aufgabe.cancel()
            else:
                lauf.abschliessen(ok=False, text="Abgebrochen.")
        if lauf is None:
            # Ehrlich benennen statt so zu tun, als waere nie etwas gewesen:
            # die Oberflaeche soll den Verlauf verwerfen, nicht ewig warten.
            await send_roh("error", "Dieser Lauf ist nicht mehr vorhanden.",
                           code="unbekannt")
            await ws.close()
            return
        log.info("Zuschauer von %s haengt sich an Lauf %s ab %d an.",
                 peer, lauf.id, ab_index)
    else:
        if not prompt:
            await send_roh("error", "Leerer Auftrag.")
            return
        lauf = runs.register.starten(prompt)
        log.info("Auftrag von %s (Lauf %s): %s", peer, lauf.id, prompt[:120])
        lauf.aufgabe = asyncio.create_task(_arbeiten(lauf))
        ab_index = 0

    # Jedes Ereignis traegt Laufkennung und laufende Nummer. Damit weiss die
    # Oberflaeche jederzeit, woran sie haengt und wo sie war - ohne dass es
    # dafuer eine gesonderte erste Nachricht braucht, die jeder Aufrufer
    # kennen muesste.
    try:
        async for i, ereignis in lauf.folgen(ab_index):
            await ws.send_text(json.dumps({**ereignis, "i": i, "lauf": lauf.id}))
    except WebSocketDisconnect:
        log.info("Zuschauer weg - Lauf %s laeuft weiter.", lauf.id)
    except Exception:
        log.exception("Senden an den Zuschauer fehlgeschlagen - Lauf %s laeuft weiter.",
                      lauf.id)
