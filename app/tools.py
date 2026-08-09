"""Die Werkzeuge, mit denen der Agent am Projekt arbeitet.

Das ist der Unterschied zwischen einem Code-Generator und einem Agenten: Statt
einmal zu raten und fertig zu sein, entscheidet das Modell selbst, was als
Naechstes zu tun ist - erst schauen, dann lesen, dann aendern, dann pruefen.

Jedes Werkzeug arbeitet ausschliesslich innerhalb des Projektverzeichnisses.
Die Pfadpruefung liegt in workspace.py und wird hier nicht umgangen.
"""

from __future__ import annotations

import json
import re

import connectors

MAX_OUTPUT = 4000        # Zeichen, die ein Werkzeugergebnis zurueckgeben darf
MAX_SEARCH_HITS = 25

# Ohne dieses Werkzeug kann die Schleife nicht sauber enden - es bleibt
# deshalb auch dann erlaubt, wenn ein Skill die Werkzeuge einschraenkt.
ALWAYS = "finish"


def _tool(name, description, properties, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def base_schema() -> list[dict]:
    """Die Werkzeuge, die immer da sind - alle nur im Projektverzeichnis."""
    return [
        _tool("list_files", "Listet alle Dateien im Projekt auf.", {}, []),
        _tool("read_file", "Liest eine Datei aus dem Projekt.",
              {"path": {"type": "string", "description": "Pfad relativ zum Projekt"}},
              ["path"]),
        _tool("write_file", "Schreibt eine Datei. Überschreibt vorhandenen Inhalt.",
              {"path": {"type": "string", "description": "Pfad relativ zum Projekt"},
               "content": {"type": "string", "description": "Vollständiger Dateiinhalt"}},
              ["path", "content"]),
        _tool("search", "Sucht Text in allen Projektdateien.",
              {"query": {"type": "string", "description": "Suchbegriff"}},
              ["query"]),
        _tool("outline", "Zeigt Funktionen, Klassen und den Aufrufgraph des Projekts.",
              {}, []),
        _tool("run_python", "Führt eine Python-Datei in der isolierten Sandbox aus.",
              {"path": {"type": "string", "description": "Pfad der auszuführenden Datei"}},
              ["path"]),
        _tool(ALWAYS, "Beendet die Arbeit und fasst das Ergebnis zusammen.",
              {"summary": {"type": "string", "description": "Was wurde erreicht"}},
              ["summary"]),
    ]


def connector_schema() -> dict[str, dict]:
    """Werkzeuge, die aus dem Projekt herausreichen - nur wenn konfiguriert."""
    return {
        "fetch_url": _tool(
            "fetch_url",
            "Lädt eine öffentliche Webseite als Text. Interne Adressen und "
            "Cloud-Metadaten sind gesperrt.",
            {"url": {"type": "string", "description": "Vollständige http(s)-Adresse"}},
            ["url"]),
        "git_push": _tool(
            "git_push",
            "Committet den Projektstand und schiebt ihn auf den eingerichteten "
            "Git-Remote.",
            {"branch": {"type": "string", "description": "Gewünschter Branch-Name"},
             "message": {"type": "string", "description": "Commit-Nachricht"}},
            ["message"]),
    }


def schema(enabled=None) -> list[dict]:
    """Basiswerkzeuge plus die freigeschalteten Konnektoren."""
    verfuegbar = connector_schema()
    zusatz = [verfuegbar[name] for name in (enabled or []) if name in verfuegbar]
    return base_schema() + zusatz


BASE_NAMES = {item["function"]["name"] for item in base_schema()}
CONNECTOR_NAMES = set(connector_schema())
NAMES = BASE_NAMES | CONNECTOR_NAMES


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT] + f"\n… gekürzt ({len(text)} Zeichen insgesamt)"


class Toolbox:
    """Fuehrt Werkzeugaufrufe gegen ein Projektverzeichnis aus.

    run_sandbox wird hineingereicht, damit die Ausfuehrung ohne echten Docker
    testbar bleibt.
    """

    def __init__(self, ws, run_sandbox=None, index_builder=None, enabled=None):
        self.ws = ws
        self.run_sandbox = run_sandbox
        self.index_builder = index_builder
        # Konnektoren, die dieser Lauf benutzen darf. Leer = keine.
        self.enabled = [n for n in (enabled or []) if n in CONNECTOR_NAMES]
        self.allowed = BASE_NAMES | set(self.enabled)
        self.written: list[str] = []
        self.pushed: list[str] = []
        self.fetched: list[str] = []
        self.finished: str | None = None

    def restrict(self, names) -> None:
        """Schraenkt die Werkzeuge ein, etwa weil ein Skill es so vorgibt.

        Erweitern kann das nie: es wird mit dem geschnitten, was ohnehin
        erlaubt ist. Ein Skill kann also keinen Konnektor freischalten, der
        nicht konfiguriert ist. 'finish' bleibt immer drin, sonst koennte die
        Schleife nicht enden.
        """
        gewuenscht = {n.strip() for n in (names or []) if n and n.strip()}
        if not gewuenscht:
            return
        self.allowed = (self.allowed & gewuenscht) | {ALWAYS}

    def schema(self) -> list[dict]:
        """Nur die Werkzeuge, die dieser Lauf wirklich benutzen darf.

        Was das Modell nicht sieht, kann es nicht aufrufen - das ist
        wirksamer als jede Ermahnung im Prompt.
        """
        return [item for item in schema(self.enabled)
                if item["function"]["name"] in self.allowed]

    async def call(self, name: str, arguments: dict) -> str:
        """Fuehrt ein Werkzeug aus. Fehler werden als Text zurueckgegeben,
        damit das Modell darauf reagieren kann statt abzubrechen."""
        if name not in self.allowed:
            grund = ("ist für diese Aufgabe nicht freigegeben"
                     if name in NAMES else "gibt es nicht")
            return (f"FEHLER: Unbekanntes Werkzeug '{name}' — {grund}. "
                    f"Erlaubt: {sorted(self.allowed)}")
        try:
            handler = getattr(self, f"_{name}")
            return _clip(await handler(arguments))
        except Exception as exc:
            return f"FEHLER: {type(exc).__name__}: {exc}"

    # -------------------------------------------------------------- Werkzeuge

    async def _list_files(self, _args) -> str:
        files = self.ws.list_files()
        return "\n".join(files) if files else "(Projekt ist leer)"

    async def _read_file(self, args) -> str:
        path = str(args.get("path", "")).strip()
        text = self.ws.read(path)
        lines = text.splitlines()
        # Zeilennummern helfen dem Modell, sich auf Stellen zu beziehen.
        return "\n".join(f"{i:4} | {line}" for i, line in enumerate(lines, 1))

    async def _write_file(self, args) -> str:
        path = str(args.get("path", "")).strip()
        content = args.get("content")
        if content is None:
            return "FEHLER: 'content' fehlt."
        written = self.ws.write(path, str(content))
        self.written.append(written)
        return f"Geschrieben: {written} ({len(str(content))} Zeichen)"

    async def _search(self, args) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return "FEHLER: 'query' fehlt."
        pattern = re.compile(re.escape(query), re.I)
        hits: list[str] = []
        for rel in self.ws.list_files():
            try:
                text = self.ws.read(rel)
            except Exception:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    hits.append(f"{rel}:{number}: {line.strip()[:160]}")
                    if len(hits) >= MAX_SEARCH_HITS:
                        return "\n".join(hits) + "\n… weitere Treffer ausgelassen"
        return "\n".join(hits) if hits else f"Keine Treffer für '{query}'."

    async def _outline(self, _args) -> str:
        if self.index_builder is None:
            return "Kein Index verfügbar."
        index = self.index_builder(self.ws)
        text = index.overview()
        return text or "(keine Symbole gefunden)"

    async def _run_python(self, args) -> str:
        path = str(args.get("path", "")).strip()
        if self.run_sandbox is None:
            return "FEHLER: Keine Sandbox verfügbar."
        code = self.ws.read(path)
        exit_code, output = await self.run_sandbox(code)
        status = "erfolgreich" if exit_code == 0 else f"Exit-Code {exit_code}"
        return f"Lauf {status}.\nAusgabe:\n{output or '(keine)'}"

    async def _finish(self, args) -> str:
        self.finished = str(args.get("summary", "")).strip() or "Fertig."
        return self.finished

    # ------------------------------------------------------------ Konnektoren

    async def _fetch_url(self, args) -> str:
        url = str(args.get("url", "")).strip()
        if not url:
            return "FEHLER: 'url' fehlt."
        import asyncio
        text = await asyncio.to_thread(connectors.fetch, url)
        self.fetched.append(url)
        return text

    async def _git_push(self, args) -> str:
        import asyncio
        ergebnis = await asyncio.to_thread(
            connectors.git_push, self.ws,
            str(args.get("branch", "")).strip(),
            str(args.get("message", "")).strip())
        self.pushed.append(ergebnis.splitlines()[0] if ergebnis else "gepusht")
        return ergebnis


def format_result(name: str, result: str, call_id: str = "") -> dict:
    """Werkzeugergebnis als Nachricht fuer die naechste Modellrunde.

    OpenAI-kompatible APIs ordnen Ergebnis und Aufruf ueber tool_call_id zu
    und lehnen ein Ergebnis ohne passende ID ab. Ollama ignoriert das Feld.
    """
    message = {"role": "tool", "name": name, "content": result}
    if call_id:
        message["tool_call_id"] = call_id
    return message


def _json_objects(text: str):
    """Findet vollstaendige JSON-Objekte im Fliesstext.

    Ein regulaerer Ausdruck reicht dafuer nicht: der interessante Fall ist
    gerade der verschachtelte - {"tool": ..., "arguments": {...}}. Deshalb
    wird geklammert gezaehlt, mit Ruecksicht auf Zeichenketten, damit eine
    geschweifte Klammer im Code-Inhalt nichts durcheinanderbringt.
    """
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text or ""):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                yield text[start:index + 1]


def parse_fallback_call(text: str):
    """Notnagel fuer Modelle ohne echte Werkzeugunterstuetzung.

    Kleine Modelle koennen oft keine Tool-Calls, geben aber JSON aus. Findet
    sich ein Block mit "tool" und "arguments", wird er als Aufruf gewertet.
    Gibt (name, arguments) zurueck oder None.
    """
    for block in _json_objects(text):
        if '"tool"' not in block:
            continue
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        name = data.get("tool")
        if name in NAMES:
            args = data.get("arguments")
            if args is None:
                args = data.get("args") or {}
            return name, (args if isinstance(args, dict) else {})
    return None
