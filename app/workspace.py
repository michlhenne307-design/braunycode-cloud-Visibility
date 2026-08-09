"""Ein Projektverzeichnis, in dem der Agent arbeitet - mit Git-Historie.

Bisher schrieb der Agent eine Datei in ein Wegwerf-Verzeichnis und vergass sie
danach. Hier bleiben die Dateien liegen, koennen gelesen und geaendert werden,
und jede Aenderung landet als Commit. Erst damit kann der Agent an einem
Projekt arbeiten statt nur Schnipsel zu erzeugen.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Groessere Dateien landen nicht vollstaendig im Modellkontext.
MAX_READ = int(os.environ.get("BRAUNY_MAX_FILE_BYTES", "200000"))
MAX_LIST = int(os.environ.get("BRAUNY_MAX_FILES", "500"))

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache"}


class WorkspaceError(Exception):
    pass


class Workspace:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------- Pfade

    def resolve(self, relative: str) -> Path:
        """Loest einen relativen Pfad auf und laesst ihn nicht aus dem Projekt heraus.

        Ohne diese Pruefung koennte ein vom Modell erfundener Pfad wie
        ../../etc/passwd ausserhalb des Projekts schreiben.
        """
        if not relative or not str(relative).strip():
            raise WorkspaceError("Leerer Pfad.")
        candidate = Path(str(relative).strip())
        if candidate.is_absolute():
            raise WorkspaceError(f"Absolute Pfade sind nicht erlaubt: {relative}")

        target = (self.root / candidate).resolve()
        # strict=False oben: die Datei muss noch nicht existieren. Entscheidend
        # ist, dass der aufgeloeste Pfad unterhalb der Wurzel liegt - das faengt
        # auch Symlinks ab, die nach draussen zeigen.
        if target != self.root and self.root not in target.parents:
            raise WorkspaceError(f"Pfad verlaesst das Projekt: {relative}")
        return target

    # -------------------------------------------------------------- Dateien

    def list_files(self, limit: int = MAX_LIST) -> list[str]:
        out: list[str] = []
        for path in sorted(self.root.rglob("*")):
            if len(out) >= limit:
                break
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.relative_to(self.root).parts):
                continue
            out.append(str(path.relative_to(self.root)))
        return out

    def read(self, relative: str) -> str:
        path = self.resolve(relative)
        if not path.is_file():
            raise WorkspaceError(f"Datei nicht gefunden: {relative}")
        if path.stat().st_size > MAX_READ:
            raise WorkspaceError(
                f"Datei zu gross ({path.stat().st_size} Bytes, Grenze {MAX_READ})."
            )
        return path.read_text(encoding="utf-8", errors="replace")

    def write(self, relative: str, content: str) -> str:
        path = self.resolve(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path.relative_to(self.root))

    def exists(self, relative: str) -> bool:
        try:
            return self.resolve(relative).is_file()
        except WorkspaceError:
            return False

    def delete(self, relative: str) -> str:
        path = self.resolve(relative)
        if not path.is_file():
            raise WorkspaceError(f"Datei nicht gefunden: {relative}")
        path.unlink()
        self._prune_empty(path.parent)
        return str(path.relative_to(self.root))

    def move(self, source: str, destination: str) -> str:
        """Verschiebt oder benennt um. Ueberschreibt nichts stillschweigend."""
        alt = self.resolve(source)
        neu = self.resolve(destination)
        if not alt.is_file():
            raise WorkspaceError(f"Datei nicht gefunden: {source}")
        if neu.exists():
            raise WorkspaceError(f"Ziel existiert bereits: {destination}")
        neu.parent.mkdir(parents=True, exist_ok=True)
        alt.rename(neu)
        self._prune_empty(alt.parent)
        return str(neu.relative_to(self.root))

    def _prune_empty(self, ordner: Path) -> None:
        """Raeumt leer gewordene Verzeichnisse weg, bis zur Projektwurzel."""
        while ordner != self.root and self.root in ordner.parents:
            try:
                ordner.rmdir()          # schlaegt fehl, wenn nicht leer
            except OSError:
                return
            ordner = ordner.parent

    # -------------------------------------------------------------- Git

    def _git(self, *args: str, check: bool = True):
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=check,
            timeout=30,
        )

    def git_ready(self) -> bool:
        """Legt bei Bedarf ein Repository an. False, wenn kein git da ist."""
        try:
            if (self.root / ".git").is_dir():
                return True
            self._git("init", "-q")
            # Identitaet lokal setzen, damit Commits ohne globale
            # Konfiguration funktionieren.
            self._git("config", "user.email", "agent@braunycode.local")
            self._git("config", "user.name", "BraunyCode Agent")
            return True
        except (FileNotFoundError, subprocess.CalledProcessError,
                subprocess.TimeoutExpired):
            return False

    def git_commit(self, message: str) -> str | None:
        """Committet alle Aenderungen. None, wenn nichts zu tun war."""
        if not self.git_ready():
            return None
        try:
            self._git("add", "-A")
            status = self._git("status", "--porcelain")
            if not status.stdout.strip():
                return None
            self._git("commit", "-q", "-m", message)
            return self._git("rev-parse", "--short", "HEAD").stdout.strip()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None

    def git_revert(self) -> str | None:
        """Setzt das Projekt auf den letzten Commit zurueck.

        Der Rueckwaertsgang: hat der Agent etwas zerschossen, ist das der Weg
        zurueck, ohne dass ein Mensch eingreifen muss. Verwirft ausdruecklich
        alle nicht committeten Aenderungen - genau das ist der Zweck.

        Gibt den Commit zurueck, auf dem das Projekt jetzt steht, oder None,
        wenn es keine Historie gibt.
        """
        if not (self.root / ".git").is_dir():
            return None
        try:
            if not self._git("rev-parse", "HEAD", check=False).stdout.strip():
                return None          # noch kein Commit, nichts zum Zurueckgehen
            self._git("checkout", "--", ".")
            # Neu angelegte Dateien sind untracked und ueberleben checkout.
            self._git("clean", "-fdq")
            return self._git("rev-parse", "--short", "HEAD").stdout.strip()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None

    def git_log(self, limit: int = 10) -> list[str]:
        if not (self.root / ".git").is_dir():
            return []
        try:
            out = self._git("log", f"-{limit}", "--pretty=%h %s", check=False)
            return [l for l in out.stdout.splitlines() if l.strip()]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
