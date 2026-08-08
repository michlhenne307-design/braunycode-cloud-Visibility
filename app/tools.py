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

MAX_OUTPUT = 4000        # Zeichen, die ein Werkzeugergebnis zurueckgeben darf
MAX_SEARCH_HITS = 25


def schema() -> list[dict]:
    """Werkzeugbeschreibung im OpenAI-Format - beide Anbieter verstehen das."""
    def tool(name, description, properties, required):
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

    return [
        tool("list_files", "Listet alle Dateien im Projekt auf.", {}, []),
        tool("read_file", "Liest eine Datei aus dem Projekt.",
             {"path": {"type": "string", "description": "Pfad relativ zum Projekt"}},
             ["path"]),
        tool("write_file", "Schreibt eine Datei. Überschreibt vorhandenen Inhalt.",
             {"path": {"type": "string", "description": "Pfad relativ zum Projekt"},
              "content": {"type": "string", "description": "Vollständiger Dateiinhalt"}},
             ["path", "content"]),
        tool("search", "Sucht Text in allen Projektdateien.",
             {"query": {"type": "string", "description": "Suchbegriff"}},
             ["query"]),
        tool("outline", "Zeigt Funktionen, Klassen und den Aufrufgraph des Projekts.",
             {}, []),
        tool("run_python", "Führt eine Python-Datei in der isolierten Sandbox aus.",
             {"path": {"type": "string", "description": "Pfad der auszuführenden Datei"}},
             ["path"]),
        tool("finish", "Beendet die Arbeit und fasst das Ergebnis zusammen.",
             {"summary": {"type": "string", "description": "Was wurde erreicht"}},
             ["summary"]),
    ]


NAMES = {item["function"]["name"] for item in schema()}


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT] + f"\n… gekürzt ({len(text)} Zeichen insgesamt)"


class Toolbox:
    """Fuehrt Werkzeugaufrufe gegen ein Projektverzeichnis aus.

    run_sandbox wird hineingereicht, damit die Ausfuehrung ohne echten Docker
    testbar bleibt.
    """

    def __init__(self, ws, run_sandbox=None, index_builder=None):
        self.ws = ws
        self.run_sandbox = run_sandbox
        self.index_builder = index_builder
        self.written: list[str] = []
        self.finished: str | None = None

    async def call(self, name: str, arguments: dict) -> str:
        """Fuehrt ein Werkzeug aus. Fehler werden als Text zurueckgegeben,
        damit das Modell darauf reagieren kann statt abzubrechen."""
        if name not in NAMES:
            return f"FEHLER: Unbekanntes Werkzeug '{name}'. Erlaubt: {sorted(NAMES)}"
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
