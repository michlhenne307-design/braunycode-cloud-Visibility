"""Gehärtete Docker-Sandbox zur Ausführung von generiertem Code."""

import asyncio
import os
import shutil
import tempfile

import docker

LABEL_KEY = "brauny.sandbox"
LABEL_VALUE = "1"

IMAGE = os.environ.get("BRAUNY_SANDBOX_IMAGE", "python:3.11-slim")
MEM_LIMIT = os.environ.get("BRAUNY_SANDBOX_MEM", "512m")
CPUS = float(os.environ.get("BRAUNY_SANDBOX_CPUS", "1.0"))
TIMEOUT = int(os.environ.get("BRAUNY_SANDBOX_TIMEOUT", "60"))

_client = None


def client():
    """Docker-Client erst beim ersten Zugriff aufbauen.

    Wichtig: nicht auf Modulebene, sonst stirbt der ganze Webserver beim
    Start, wenn die Docker-Gruppenmitgliedschaft noch nicht aktiv ist.
    """
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def safe_relpath(name: str) -> str:
    """Macht aus einem Dateinamen einen Pfad, der im Projekt bleibt.

    Unterverzeichnisse muessen erhalten bleiben - ein Projekt aus mehreren
    Modulen laesst sich sonst nicht ausfuehren. Alles, was aus dem
    Verzeichnis herauszeigen koennte (absolut, '..', Laufwerksbuchstabe),
    faellt auf den blossen Dateinamen zurueck.
    """
    aufgeraeumt = (name or "").replace("\\", "/").strip("/")
    teile = [t for t in aufgeraeumt.split("/") if t not in ("", ".")]
    if not teile or any(t == ".." for t in teile) or os.path.isabs(name or ""):
        return os.path.basename(name or "") or "datei.py"
    return os.path.join(*teile)


# Der Sandbox-User (uid 65534) muss lesen und Verzeichnisse betreten koennen -
# mehr nicht. Frueher stand hier 0o777 bzw. 0o666: damit konnte JEDES lokale
# Konto den Code aendern, waehrend er ausgefuehrt wurde, oder den
# Projektinhalt mitlesen. Schreiben braucht die Sandbox nicht; fuer Ausgaben
# ist /tmp eingehaengt (tmpfs, beschreibbar).
DIR_MODE = 0o755
FILE_MODE = 0o644


def make_project_dir(files: dict[str, str]) -> str:
    """Legt ein temporäres Projektverzeichnis an, das der Sandbox-User lesen darf."""
    project_dir = tempfile.mkdtemp(prefix="brauny-")
    os.chmod(project_dir, DIR_MODE)
    for name, content in files.items():
        path = os.path.join(project_dir, safe_relpath(name))
        ordner = os.path.dirname(path)
        if ordner and ordner != project_dir:
            os.makedirs(ordner, exist_ok=True)
            # Der Sandbox-User ist ein anderer und muss die
            # Zwischenverzeichnisse betreten koennen.
            for teil in _pfad_kette(project_dir, ordner):
                os.chmod(teil, DIR_MODE)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(path, FILE_MODE)
    return project_dir


def _pfad_kette(wurzel: str, ziel: str):
    """Alle Verzeichnisse zwischen Wurzel und Ziel, einschliesslich Ziel."""
    rest = os.path.relpath(ziel, wurzel)
    aktuell = wurzel
    for teil in rest.split(os.sep):
        if teil in ("", "."):
            continue
        aktuell = os.path.join(aktuell, teil)
        yield aktuell


def entry_command(entry: str = "main.py") -> list[str]:
    """Startbefehl fuer die auszufuehrende Datei.

    Frueher stand hier fest /app/main.py. Damit war jedes Projekt aus
    mehreren Modulen unausfuehrbar - genau das, was der Agent bauen koennen
    soll. Der Pfad wird durch dieselbe Pruefung geschickt wie beim Anlegen,
    sonst zeigte er womoeglich aus dem Container-Verzeichnis heraus.
    """
    return ["python", "-u", "/app/" + safe_relpath(entry or "main.py")]


def start(project_dir: str, command=None):
    """Startet den Container detached und gibt das Container-Objekt zurück.

    detach=True ist entscheidend: mit detach=False liefert docker-py die Logs
    direkt als bytes zurueck und es gibt kein Objekt, auf dem .logs() existiert.
    """
    return client().containers.run(
        IMAGE,
        command or entry_command(),
        working_dir="/app",
        # Nur-lesend einhaengen: der Container soll den Projektstand
        # ausfuehren, nicht veraendern. Fuer Ausgaben ist /tmp da.
        volumes={project_dir: {"bind": "/app", "mode": "ro"}},
        detach=True,
        network_disabled=True,
        mem_limit=MEM_LIMIT,
        memswap_limit=MEM_LIMIT,
        nano_cpus=int(CPUS * 1_000_000_000),
        pids_limit=128,
        user="65534:65534",
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        tmpfs={"/tmp": "rw,size=64m"},
        # PYTHONPATH=/app ist entscheidend fuer mehrdateiige Projekte:
        # 'python /app/pkg/start.py' setzt sys.path[0] auf /app/pkg, nicht auf
        # /app. Ein 'import helfer' aus dem Projektstamm scheitert dann mit
        # ModuleNotFoundError - der Mehrdatei-Lauf waere nur zufaellig
        # gegangen, solange die Startdatei ganz oben liegt.
        environment={"HOME": "/tmp", "PYTHONDONTWRITEBYTECODE": "1",
                     "PYTHONPATH": "/app"},
        # Label, damit verwaiste Container nach einem Absturz wiederfindbar sind
        labels={LABEL_KEY: LABEL_VALUE},
    )


def reap_orphans() -> int:
    """Entfernt Container aus frueheren Laeufen.

    Stirbt der Dienst mitten in einem Lauf, bleibt sein Container liegen und
    haelt Speicher belegt. Beim Start wird deshalb alles mit unserem Label
    weggeraeumt. Gibt die Anzahl entfernter Container zurueck.
    """
    removed = 0
    for container in client().containers.list(
        all=True, filters={"label": f"{LABEL_KEY}={LABEL_VALUE}"}
    ):
        try:
            container.remove(force=True)
            removed += 1
        except Exception:
            pass
    return removed


async def stream_logs(container, timeout: int = TIMEOUT):
    """Liefert Container-Logs zeilenweise als async generator.

    Die blockierende docker-py Iteration laeuft in einem Thread und schiebt
    die Zeilen ueber eine Queue in den Event-Loop, damit der Webserver
    waehrenddessen ansprechbar bleibt.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    sentinel = object()

    def pump():
        try:
            for chunk in container.logs(stream=True, follow=True):
                line = chunk.decode("utf-8", errors="replace").rstrip("\n")
                loop.call_soon_threadsafe(queue.put_nowait, line)
        except Exception as exc:  # Container weggeraeumt, Socket zu, ...
            loop.call_soon_threadsafe(queue.put_nowait, f"[log-fehler] {exc}")
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, sentinel)

    task = asyncio.create_task(asyncio.to_thread(pump))
    deadline = loop.time() + timeout
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            item = await asyncio.wait_for(queue.get(), timeout=remaining)
            if item is sentinel:
                return
            yield item
    finally:
        task.cancel()


def cleanup(container=None, project_dir=None):
    if container is not None:
        try:
            container.remove(force=True)
        except Exception:
            pass
    if project_dir:
        shutil.rmtree(project_dir, ignore_errors=True)
