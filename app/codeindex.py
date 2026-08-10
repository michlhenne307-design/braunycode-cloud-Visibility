"""Ein Index ueber das Projekt: welche Symbole gibt es, wer ruft wen auf.

Das Modell kennt React, Python und Design Patterns auswendig - das steht in
seinen Gewichten. Was es nicht kennen kann, ist DIESES Projekt: welche
Funktionen hier existieren, wie sie zusammenhaengen, was bricht, wenn man eine
Signatur aendert.

Genau das liefert dieses Modul - deterministisch ueber den Syntaxbaum, ohne
Modell und ohne Einbettungen. Statt dem Modell Dokumentation vorzusetzen, die
es ohnehin kennt, bekommt es die paar Stellen aus dem eigenen Projekt, um die
es gerade geht.

Grenzen, die man kennen sollte: Der Aufrufgraph wird ueber Namen gebildet, nicht
ueber aufgeloeste Typen. Zwei gleichnamige Methoden in verschiedenen Klassen
sind fuer den Index dasselbe Ziel. Fuer "wer koennte betroffen sein" reicht das;
eine Typanalyse ist es nicht.
"""

from __future__ import annotations

import ast
import os

import syntax
import re
from dataclasses import dataclass, field

# Obergrenzen, damit der Prompt eines kleinen Modells nicht ueberlaeuft.
MAX_SYMBOLS_IN_SUMMARY = 60
MAX_FULL_SOURCES = 2
MAX_SOURCE_LINES = 60


@dataclass
class Symbol:
    kind: str            # "function" | "class" | "method"
    name: str
    qualname: str        # "Cart.add"
    path: str
    line: int
    end_line: int
    signature: str
    doc: str = ""
    calls: set[str] = field(default_factory=set)

    def header(self) -> str:
        """Eine Zeile fuer die Uebersicht.

        Die Schreibweise richtet sich nach der Sprache der Datei, nicht nach
        der des Werkzeugs: 'def Page()' fuer eine .tsx-Komponente war schlicht
        falsch und legt dem Modell nahe, hier waere Python im Spiel - genau
        die Verwechslung, die einen ganzen Lauf gekostet hat.

        Bei class, interface, type und enum steht die Art schon in der
        Signatur; ein zweites Wort davor waere Doppelung.
        """
        if self.signature.startswith(("class ", "interface ", "type ", "enum ")):
            text = self.signature
        elif self.path.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")):
            text = f"function {self.signature}"
        else:
            text = f"def {self.signature}"
        return f"{text}  — {self.doc}" if self.doc else text


def _split_words(text: str) -> set[str]:
    """Zerlegt Bezeichner in Woerter: calculateTotal_v2 -> {calculate, total, v2}"""
    parts = re.split(r"[^A-Za-z0-9]+", text or "")
    words: set[str] = set()
    for part in parts:
        if not part:
            continue
        # camelCase auftrennen
        for piece in re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|[0-9]+", part):
            if len(piece) > 1:
                words.add(piece.lower())
    return words


class _Collector(ast.NodeVisitor):
    """Sammelt Definitionen und die darin vorkommenden Aufrufe."""

    def __init__(self, path: str, source: str):
        self.path = path
        self.lines = source.splitlines()
        self.symbols: list[Symbol] = []
        self._class_stack: list[str] = []

    # -- Hilfen -------------------------------------------------------

    def _signature(self, node) -> str:
        try:
            args = ast.unparse(node.args)
        except Exception:
            args = "..."
        return f"{node.name}({args})"

    def _doc(self, node) -> str:
        text = ast.get_docstring(node) or ""
        return text.strip().splitlines()[0] if text.strip() else ""

    def _calls_in(self, node, descend_into_defs: bool = True) -> set[str]:
        """Aufrufe innerhalb eines Knotens.

        Bei einer Klasse duerfen die Methodenkoerper NICHT mitzaehlen - sonst
        erscheint die Klasse als Aufrufer von allem, was ihre Methoden tun, und
        der Aufrufgraph wird unbrauchbar. Bei Funktionen zaehlen verschachtelte
        Funktionen mit, weil sie keinen eigenen Eintrag bekommen.
        """
        found: set[str] = set()

        def walk(current, is_root: bool):
            if not is_root and isinstance(
                current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ) and not descend_into_defs:
                return
            if isinstance(current, ast.Call):
                func = current.func
                if isinstance(func, ast.Name):
                    found.add(func.id)
                elif isinstance(func, ast.Attribute):
                    found.add(func.attr)
            for child in ast.iter_child_nodes(current):
                walk(child, False)

        walk(node, True)
        return found

    def _add(self, node, kind: str):
        qual = ".".join([*self._class_stack, node.name])
        self.symbols.append(Symbol(
            kind=kind,
            name=node.name,
            qualname=qual,
            path=self.path,
            line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno),
            signature=self._signature(node) if kind != "class" else node.name,
            doc=self._doc(node),
            calls=self._calls_in(node, descend_into_defs=(kind != "class")),
        ))

    # -- Besuche ------------------------------------------------------

    def visit_FunctionDef(self, node):
        self._add(node, "method" if self._class_stack else "function")
        # Nicht weiter absteigen: verschachtelte Funktionen bekommen keinen
        # eigenen Eintrag, ihre Aufrufe zaehlen zur aeusseren Funktion.

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self._add(node, "class")
        self._class_stack.append(node.name)
        for child in node.body:
            self.visit(child)
        self._class_stack.pop()


class CodeIndex:
    """Index ueber alle Python-Dateien eines Projektverzeichnisses."""

    def __init__(self):
        self.symbols: list[Symbol] = []
        self.files: list[str] = []
        self.skipped: list[tuple[str, str]] = []
        self._sources: dict[str, str] = {}

    # ------------------------------------------------------------ Aufbau

    @classmethod
    def build(cls, ws) -> "CodeIndex":
        index = cls()
        # TypeScript und JavaScript werden gesammelt und in EINEM Node-Lauf
        # ausgewertet. Ein Prozess je Datei waere bei zweihundert Dateien
        # zehn Sekunden reine Startzeit.
        web: list[tuple[str, str]] = []
        for rel in ws.list_files():
            if os.path.splitext(rel)[1].lower() in syntax.TYPESCRIPT:
                try:
                    web.append((rel, str(ws.resolve(rel))))
                except Exception as exc:
                    index.skipped.append((rel, str(exc)))
                continue
            if not rel.endswith(".py"):
                continue
            try:
                source = ws.read(rel)
            except Exception as exc:
                index.skipped.append((rel, str(exc)))
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError as exc:
                # Eine kaputte Datei darf den Index nicht verhindern.
                index.skipped.append((rel, f"SyntaxError Zeile {exc.lineno}"))
                continue
            collector = _Collector(rel, source)
            collector.visit(tree)
            index.symbols.extend(collector.symbols)
            index.files.append(rel)
            index._sources[rel] = source

        # Ohne node oder typescript bleibt es beim Python-Teil. Der Index ist
        # dann unvollstaendig - aber er behauptet nichts, was er nicht weiss.
        gefunden = syntax.symbole(web) if web else None
        if gefunden is None:
            for rel, _ in web:
                index.skipped.append((rel, "kein TypeScript-Parser verfügbar"))
        else:
            for eintrag in gefunden:
                index.symbols.append(Symbol(
                    kind=eintrag.get("kind", "function"),
                    name=eintrag.get("name", ""),
                    qualname=eintrag.get("qualname", ""),
                    path=eintrag.get("path", ""),
                    line=int(eintrag.get("line", 1)),
                    end_line=int(eintrag.get("end_line", 1)),
                    signature=eintrag.get("signature", ""),
                    doc=eintrag.get("doc", ""),
                ))
            for rel, _ in web:
                index.files.append(rel)
        return index

    # ------------------------------------------------------------ Abfragen

    def find(self, name: str) -> list[Symbol]:
        needle = name.strip()
        return [s for s in self.symbols
                if s.name == needle or s.qualname == needle]

    def callers(self, name: str) -> list[Symbol]:
        """Symbole, die `name` aufrufen."""
        needle = name.split(".")[-1]
        return [s for s in self.symbols if needle in s.calls]

    def callees(self, name: str) -> list[str]:
        """Aufrufe innerhalb von `name`, beschraenkt auf bekannte Symbole."""
        known = {s.name for s in self.symbols}
        out: set[str] = set()
        for symbol in self.find(name):
            out |= {c for c in symbol.calls if c in known}
        return sorted(out)

    def impact(self, name: str) -> list[Symbol]:
        """Was koennte brechen, wenn `name` seine Signatur aendert.

        Die Aufrufer - ohne das Symbol selbst, falls es sich rekursiv aufruft.
        """
        targets = {s.qualname for s in self.find(name)}
        return [s for s in self.callers(name) if s.qualname not in targets]

    def relevant(self, task: str, limit: int = 5) -> list[Symbol]:
        """Symbole, die zur Aufgabe passen - lexikalisch, nicht semantisch.

        Bewusst simpel: Wortueberschneidung zwischen Aufgabentext und
        Bezeichner. Das ist keine Bedeutungserkennung, sondern ein Abgleich
        von Woertern - dafuer nachvollziehbar und ohne Rechenkosten.
        """
        wanted = _split_words(task)
        if not wanted:
            return []
        scored: list[tuple[int, Symbol]] = []
        for symbol in self.symbols:
            words = _split_words(symbol.qualname) | _split_words(symbol.doc)
            score = len(wanted & words)
            # Exakter Name im Aufgabentext wiegt schwerer.
            if symbol.name and symbol.name in task:
                score += 3
            if score:
                scored.append((score, symbol))
        scored.sort(key=lambda pair: (-pair[0], pair[1].path, pair[1].line))
        return [symbol for _, symbol in scored[:limit]]

    def source_of(self, symbol: Symbol) -> str:
        text = self._sources.get(symbol.path, "")
        if not text:
            return ""
        lines = text.splitlines()[symbol.line - 1:symbol.end_line]
        if len(lines) > MAX_SOURCE_LINES:
            lines = lines[:MAX_SOURCE_LINES] + ["    # … gekürzt"]
        return "\n".join(lines)

    # ------------------------------------------------------------ Ausgabe

    def overview(self, limit: int = MAX_SYMBOLS_IN_SUMMARY) -> str:
        """Kompakte Projektuebersicht fuer den Prompt."""
        if not self.symbols:
            return ""
        out = [f"Projekt: {len(self.files)} Datei(en), {len(self.symbols)} Symbol(e)."]
        shown = 0
        for path in self.files:
            in_file = [s for s in self.symbols if s.path == path]
            if not in_file:
                continue
            out.append(f"  {path}:")
            for symbol in in_file:
                if shown >= limit:
                    out.append("    … weitere ausgelassen")
                    return "\n".join(out)
                indent = "      " if symbol.kind == "method" else "    "
                out.append(f"{indent}{symbol.header()}")
                shown += 1
        return "\n".join(out)

    def context_for(self, task: str) -> str:
        """Der Block, der dem Modell mitgegeben wird.

        Uebersicht plus Volltext der wenigen Stellen, um die es geht - statt
        Dokumentation, die das Modell ohnehin auswendig kennt.
        """
        if not self.symbols:
            return ""
        parts = [self.overview()]
        hits = self.relevant(task, limit=MAX_FULL_SOURCES)
        if hits:
            parts.append("\nVermutlich betroffene Stellen:")
            for symbol in hits:
                parts.append(f"\n# {symbol.path}:{symbol.line} ({symbol.qualname})")
                parts.append(self.source_of(symbol))
                affected = self.impact(symbol.name)
                if affected:
                    names = ", ".join(sorted({s.qualname for s in affected})[:5])
                    parts.append(f"# Aufrufer: {names}")
        return "\n".join(parts)
