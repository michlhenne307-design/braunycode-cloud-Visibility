"""Deterministische Code-Umbauten - ohne Modell, ohne Raten.

Ein grosser Teil alltaeglicher Aenderungen ist rein mechanisch: umbenennen,
Docstrings ergaenzen, tote Importe entfernen. Dafuer ist ein Sprachmodell das
falsche Werkzeug - es kostet Rechenzeit und kann danebenliegen. Diese
Operationen laufen hier ueber den konkreten Syntaxbaum (libcst): in
Millisekunden, reproduzierbar, formatierungserhaltend.

Was dieses Modul NICHT ist: eine Absichtserkennung. classify() ist ein
Mustervergleich auf gaengige Formulierungen. Trifft kein Muster, gibt es None
zurueck und der Auftrag geht den normalen Weg ueber das Modell.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import libcst as cst

IDENT = r"[A-Za-z_][A-Za-z0-9_]*"


@dataclass
class MechanicalTask:
    """Ein erkannter mechanischer Auftrag."""
    kind: str
    params: dict = field(default_factory=dict)
    label: str = ""


@dataclass
class RefactorResult:
    code: str
    changed: bool
    summary: str


# ------------------------------------------------------------------ Umbenennen

class _Rename(cst.CSTTransformer):
    """Benennt freie Bezeichner um.

    Bewusst ausgenommen: Attributnamen (obj.alt) und Schluesselwort-Argumente
    (f(alt=1)). Beide gehoeren zu einer fremden Schnittstelle - sie blind
    mitzuziehen waere in den meisten Faellen falsch.
    """

    def __init__(self, old: str, new: str):
        self.old, self.new = old, new
        self.count = 0

    def leave_Name(self, original: cst.Name, updated: cst.Name) -> cst.Name:
        if original.value == self.old:
            self.count += 1
            return updated.with_changes(value=self.new)
        return updated

    def leave_Attribute(self, original: cst.Attribute, updated: cst.Attribute):
        # leave_Name hat den Attributnamen schon geaendert - zuruecknehmen.
        if original.attr.value == self.old:
            self.count -= 1
            return updated.with_changes(attr=original.attr)
        return updated

    def leave_Arg(self, original: cst.Arg, updated: cst.Arg):
        if original.keyword is not None and original.keyword.value == self.old:
            self.count -= 1
            return updated.with_changes(keyword=original.keyword)
        return updated


def rename_symbol(code: str, old: str, new: str) -> RefactorResult:
    tree = cst.parse_module(code)
    transformer = _Rename(old, new)
    result = tree.visit(transformer)
    n = transformer.count
    return RefactorResult(
        code=result.code,
        changed=n > 0,
        summary=(f"{n} Vorkommen von '{old}' zu '{new}' umbenannt."
                 if n else f"'{old}' kommt als freier Bezeichner nicht vor."),
    )


# ------------------------------------------------------------------ Docstrings

def _has_docstring(node) -> bool:
    body = getattr(node.body, "body", None)
    if not body:
        return False
    first = body[0]
    if not isinstance(first, cst.SimpleStatementLine) or not first.body:
        return False
    expr = first.body[0]
    return isinstance(expr, cst.Expr) and isinstance(
        expr.value, (cst.SimpleString, cst.ConcatenatedString)
    )


def _describe(name: str) -> str:
    """Macht aus calculate_total einen Satz: 'Calculate total.'"""
    words = [w for w in name.strip("_").split("_") if w]
    if not words:
        return "TODO: Beschreibung."
    text = " ".join(words)
    return text[:1].upper() + text[1:] + "."


class _AddDocstrings(cst.CSTTransformer):
    def __init__(self):
        self.count = 0

    def _insert(self, updated, name: str):
        # Einzeiler (def f(): pass) haben keinen IndentedBlock - auslassen,
        # statt die Zeile umzubauen.
        if not isinstance(updated.body, cst.IndentedBlock):
            return updated
        if _has_docstring(updated):
            return updated
        doc = cst.SimpleStatementLine(
            body=[cst.Expr(cst.SimpleString(f'"""{_describe(name)}"""'))]
        )
        self.count += 1
        return updated.with_changes(
            body=updated.body.with_changes(body=[doc, *updated.body.body])
        )

    def leave_FunctionDef(self, original, updated):
        return self._insert(updated, original.name.value)

    def leave_ClassDef(self, original, updated):
        return self._insert(updated, original.name.value)


def add_docstrings(code: str) -> RefactorResult:
    tree = cst.parse_module(code)
    transformer = _AddDocstrings()
    result = tree.visit(transformer)
    n = transformer.count
    return RefactorResult(
        code=result.code,
        changed=n > 0,
        summary=(f"{n} Docstring(s) ergaenzt." if n
                 else "Alle Funktionen und Klassen haben bereits einen Docstring."),
    )


# ------------------------------------------------------------------ Tote Importe

class _CollectUsed(cst.CSTVisitor):
    """Sammelt alle Bezeichner ausserhalb von Import-Anweisungen."""

    def __init__(self):
        self.used: set[str] = set()
        self.has_star = False
        self.has_dunder_all = False
        self._in_import = 0

    def visit_Import(self, node): self._in_import += 1
    def leave_Import(self, node): self._in_import -= 1

    def visit_ImportFrom(self, node):
        self._in_import += 1
        if isinstance(node.names, cst.ImportStar):
            self.has_star = True

    def leave_ImportFrom(self, node): self._in_import -= 1

    def visit_Name(self, node):
        if node.value == "__all__":
            self.has_dunder_all = True
        if not self._in_import:
            self.used.add(node.value)


def _bound_names(alias: cst.ImportAlias) -> str:
    """Der Name, unter dem ein Import im Modul sichtbar wird."""
    if alias.asname is not None:
        return alias.asname.name.value
    node = alias.name
    # Bei "import a.b.c" ist nur "a" gebunden.
    while isinstance(node, cst.Attribute):
        node = node.value
    return node.value


class _DropUnused(cst.CSTTransformer):
    def __init__(self, used: set[str]):
        self.used = used
        self.count = 0

    def _filter(self, updated):
        if isinstance(updated.names, cst.ImportStar):
            return updated
        keep = [a for a in updated.names if _bound_names(a) in self.used]
        removed = len(updated.names) - len(keep)
        if not removed:
            return updated
        self.count += removed
        if not keep:
            return cst.RemoveFromParent()
        # Das letzte Element darf kein nachlaufendes Komma tragen.
        keep[-1] = keep[-1].with_changes(comma=cst.MaybeSentinel.DEFAULT)
        return updated.with_changes(names=keep)

    def leave_Import(self, original, updated):
        return self._filter(updated)

    def leave_ImportFrom(self, original, updated):
        return self._filter(updated)


def remove_unused_imports(code: str) -> RefactorResult:
    tree = cst.parse_module(code)
    collector = _CollectUsed()
    tree.visit(collector)

    # Bei "from x import *" oder __all__ ist nicht sicher entscheidbar,
    # was gebraucht wird - dann lieber nichts anfassen.
    if collector.has_star or collector.has_dunder_all:
        return RefactorResult(code, False,
                              "Übersprungen: Stern-Import oder __all__ vorhanden, "
                              "eine sichere Entscheidung ist so nicht möglich.")

    transformer = _DropUnused(collector.used)
    result = tree.visit(transformer)
    n = transformer.count
    return RefactorResult(
        code=result.code,
        changed=n > 0,
        summary=(f"{n} ungenutzte(r) Import(e) entfernt." if n
                 else "Keine ungenutzten Importe gefunden."),
    )


# ------------------------------------------------------------------ Erkennung

_RENAME_PATTERNS = [
    re.compile(rf"benenne\s+(?P<old>{IDENT})\s+(?:in|zu)\s+(?P<new>{IDENT})\s+um", re.I),
    re.compile(rf"(?P<old>{IDENT})\s+(?:in|zu)\s+(?P<new>{IDENT})\s+umbenennen", re.I),
    re.compile(rf"rename\s+(?P<old>{IDENT})\s+to\s+(?P<new>{IDENT})", re.I),
]

_DOCSTRING_PATTERNS = [
    re.compile(r"docstrings?\b.*\b(hinzu|ergänz|ergaenz|einfüg|einfueg)", re.I),
    re.compile(r"(füge|fuege|add)\b.*\bdocstrings?", re.I),
]

_UNUSED_PATTERNS = [
    re.compile(r"(ungenutzte|unbenutzte|nicht genutzte|tote)\s+import", re.I),
    re.compile(r"unused\s+imports?", re.I),
]


def classify(intent: str):
    """Erkennt einen mechanischen Auftrag - oder None.

    Bewusst konservativ: im Zweifel None, damit der Auftrag den normalen Weg
    ueber das Modell nimmt. Ein falsch erkannter Umbau waere schlimmer als
    ein verpasster.
    """
    text = (intent or "").strip()
    if not text:
        return None

    for pattern in _RENAME_PATTERNS:
        m = pattern.search(text)
        if m and m.group("old") != m.group("new"):
            return MechanicalTask(
                kind="rename",
                params={"old": m.group("old"), "new": m.group("new")},
                label=f"'{m.group('old')}' → '{m.group('new')}' umbenennen",
            )

    for pattern in _DOCSTRING_PATTERNS:
        if pattern.search(text):
            return MechanicalTask(kind="docstrings", label="Docstrings ergänzen")

    for pattern in _UNUSED_PATTERNS:
        if pattern.search(text):
            return MechanicalTask(kind="unused_imports",
                                  label="Ungenutzte Importe entfernen")

    return None


def apply(task: MechanicalTask, code: str) -> RefactorResult:
    """Fuehrt einen erkannten Auftrag aus. Syntaxfehler werden gemeldet."""
    try:
        if task.kind == "rename":
            return rename_symbol(code, task.params["old"], task.params["new"])
        if task.kind == "docstrings":
            return add_docstrings(code)
        if task.kind == "unused_imports":
            return remove_unused_imports(code)
    except cst.ParserSyntaxError as exc:
        return RefactorResult(code, False, f"Quelltext nicht parsebar: {exc}")
    return RefactorResult(code, False, f"Unbekannte Operation: {task.kind}")
