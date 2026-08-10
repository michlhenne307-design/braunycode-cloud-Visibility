"""Die Pruefbefehle des Projekts finden - nicht erfinden.

Bisher hat der Agent selbst geraten, womit ein Projekt geprueft wird. Bei
Python lag er meistens richtig ('python -m pytest'), bei allem anderen fast
immer daneben: er rief 'npm test' in einem Projekt ohne dieses Skript, bekam
einen Fehler, versuchte etwas anderes - und gab nach ein paar Runden auf,
ohne eine einzige Aenderung gemacht zu haben.

Hier wird stattdessen nachgesehen, was das Projekt WIRKLICH anbietet:
package.json, Makefile, pyproject.toml. Was nicht dasteht, wird nicht
aufgerufen.

Zwei Regeln, die den Unterschied machen:

1. Ein Dauerlaeufer ist kein Pruefbefehl. 'dev', 'start' und 'watch' kehren
   nie zurueck - sie wuerden die Sandbox bis zum Zeitlimit blockieren und der
   Lauf saehe aus wie ein Absturz.
2. Findet sich kein einziger Pruefbefehl, ist das Ergebnis eine LEERE Liste
   und niemals ein stilles "dann ist eben alles in Ordnung". Wer nichts
   geprueft hat, hat nichts belegt.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

# Skripte, die nie zurueckkehren. Sie als Pruefung zu starten hiesse, jeden
# Lauf ins Zeitlimit zu schicken.
DAUERLAEUFER = {"dev", "start", "serve", "watch", "storybook", "preview"}

# Welche Skriptnamen welche Rolle haben. Die Reihenfolge hier ist die
# Reihenfolge der Ausfuehrung: erst das Billige und Genaue (Lint, Typen),
# dann der teure Bau, zuletzt die Tests. Ein Tippfehler soll nach zwei
# Sekunden auffallen und nicht nach dem vollen Build.
ROLLEN: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("lint", ("lint", "eslint", "lint:js", "check:lint")),
    ("typecheck", ("typecheck", "type-check", "tsc", "check-types", "types")),
    ("build", ("build", "compile")),
    ("test", ("test", "tests", "test:unit", "unit")),
    ("e2e", ("e2e", "test:e2e", "playwright", "cypress:run")),
)


@dataclass(frozen=True)
class Gate:
    """Ein Pruefbefehl, den das Projekt selbst mitbringt."""

    rolle: str          # lint | typecheck | build | test | e2e
    befehl: list[str]   # ohne Shell, direkt so ausfuehrbar
    quelle: str         # woher er stammt: package.json, Makefile, ...
    braucht_module: bool = False   # node_modules noetig?

    def zeile(self) -> str:
        return f"{self.rolle}: {' '.join(self.befehl)}  (aus {self.quelle})"


def _paketmanager(dateien: dict[str, str]) -> str:
    """Am Lockfile ablesen, nicht raten. Der falsche Manager scheitert sonst
    an einer Kleinigkeit und sieht aus wie ein kaputtes Projekt."""
    if "yarn.lock" in dateien:
        return "yarn"
    if "pnpm-lock.yaml" in dateien:
        return "pnpm"
    return "npm"


def _js_befehl(manager: str, skript: str) -> list[str]:
    # yarn kennt 'yarn <skript>' direkt, npm und pnpm brauchen 'run'.
    if manager == "yarn":
        return ["yarn", skript]
    return [manager, "run", skript]


def _aus_package_json(inhalt: str, manager: str) -> list[Gate]:
    try:
        daten = json.loads(inhalt)
    except (ValueError, TypeError):
        return []
    if not isinstance(daten, dict):
        return []
    skripte = daten.get("scripts")
    if not isinstance(skripte, dict):
        return []

    gefunden: list[Gate] = []
    belegt: set[str] = set()
    for rolle, namen in ROLLEN:
        for name in namen:
            if name in belegt or name in DAUERLAEUFER:
                continue
            wert = skripte.get(name)
            if not isinstance(wert, str) or not wert.strip():
                continue
            belegt.add(name)
            gefunden.append(Gate(rolle=rolle, befehl=_js_befehl(manager, name),
                                 quelle="package.json", braucht_module=True))
            break
    return gefunden


# Make-Ziele stehen am Zeilenanfang vor einem Doppelpunkt. '.PHONY' und
# Variablenzuweisungen ('CC := gcc') sind keine Ziele.
_MAKE_ZIEL = re.compile(r"^(?P<ziel>[A-Za-z][\w.-]*)\s*:(?!=)", re.MULTILINE)


def _aus_makefile(inhalt: str) -> list[Gate]:
    ziele = {t.group("ziel") for t in _MAKE_ZIEL.finditer(inhalt)}
    gefunden = []
    for rolle, namen in ROLLEN:
        for name in namen:
            if name in ziele and name not in DAUERLAEUFER:
                gefunden.append(Gate(rolle=rolle, befehl=["make", name],
                                     quelle="Makefile"))
                break
    return gefunden


def _aus_python(dateien: dict[str, str]) -> list[Gate]:
    """Python-Projekte tragen ihre Pruefung selten als Skript ein.

    Deshalb hier an dem festgemacht, was nachweislich da ist: eine
    ruff-Konfiguration, eine mypy-Konfiguration, echte Testdateien. Nichts
    davon wird angenommen, weil 'es ist ja Python'.
    """
    konfig = "\n".join(dateien.get(name, "") for name in
                       ("pyproject.toml", "setup.cfg", "tox.ini", "ruff.toml",
                        ".ruff.toml", "mypy.ini"))
    gefunden = []
    if "[tool.ruff" in konfig or "[ruff]" in konfig or "ruff.toml" in "".join(
            n for n in dateien if n.endswith("ruff.toml")):
        gefunden.append(Gate(rolle="lint", befehl=["ruff", "check", "."],
                             quelle="ruff-Konfiguration"))
    if "[tool.mypy" in konfig or "[mypy]" in konfig:
        gefunden.append(Gate(rolle="typecheck", befehl=["python", "-m", "mypy", "."],
                             quelle="mypy-Konfiguration"))
    testdateien = [n for n in dateien
                   if re.search(r"(^|/)(test_[^/]+|[^/]+_test)\.py$", n)]
    if testdateien:
        gefunden.append(Gate(rolle="test", befehl=["python", "-m", "pytest", "-q"],
                             quelle=f"{len(testdateien)} Testdatei(en)"))
    return gefunden


def ermitteln(dateien: dict[str, str]) -> list[Gate]:
    """Die Pruefbefehle dieses Projekts, in Ausfuehrungsreihenfolge.

    'dateien' ist Pfad -> Inhalt; gelesen wird nur, was auch gebraucht wird.
    Eine leere Liste heisst 'dieses Projekt sagt nicht, wie man es prueft' -
    und genau das muss der Aufrufer dann auch sagen, statt es als bestanden
    zu werten.
    """
    if "package.json" in dateien:
        gefunden = _aus_package_json(dateien["package.json"],
                                     _paketmanager(dateien))
        if gefunden:
            return gefunden
    if "Makefile" in dateien:
        gefunden = _aus_makefile(dateien["Makefile"])
        if gefunden:
            return gefunden
    return _aus_python(dateien)


def braucht_fremdpakete(gates: list[Gate]) -> bool:
    """Haengt mindestens ein Gate an installierten Paketen (node_modules)?

    Diese Gates sind in der jetzigen Sandbox NICHT ausfuehrbar, und zwar aus
    drei unabhaengigen Gruenden:

      1. node_modules wird bewusst nicht in den Container kopiert (SKIP_DIRS
         in workspace.py) - es waeren zehntausende Dateien.
      2. Der Container hat kein Netz, 'npm install' ist dort unmoeglich.
      3. /app ist nur lesend eingehaengt; 'next build' will nach .next/
         schreiben und scheitert schon daran.

    Das ist kein Fehler des Projekts und kein Fehler des Agenten. Es
    trotzdem zu starten wuerde einen Paketfehler erzeugen, den das Modell
    dann als Codefehler missversteht und "repariert" - schlimmer als gar
    nicht zu laufen. Deshalb wird es benannt statt versucht.
    """
    return any(g.braucht_module for g in gates)
