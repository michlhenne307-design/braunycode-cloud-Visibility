"""Skills - Verfahrenswissen als Textdatei.

Ein Sprachmodell weiss, was pytest ist. Was es nicht weiss, ist WIE hier
gearbeitet wird: erst reproduzieren, dann fixen, dann nachweisen. Genau dieses
Verfahrenswissen steckt in einer Skill-Datei - ein paar hundert Zeichen, die
vor die Aufgabe gestellt werden, wenn sie passt.

Fuer ein kleines Modell ist das der billigste Qualitaetssprung, den es gibt:
kein Training, keine Einbettungen, kein zusaetzlicher Modellaufruf. Nur Text,
der zur richtigen Zeit im Prompt steht.

Format (Markdown mit einfachem Kopf):

    ---
    name: tests
    beschreibung: Tests schreiben und ausfuehren
    ausloeser: test, tests, pytest, testen
    werkzeuge: read_file, write_file, run_python, finish
    ---
    Hier steht die Anleitung.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

# Deckel gegen versehentlich riesige oder zu viele Dateien im Skill-Ordner.
MAX_SKILL_BYTES = 8000
MAX_SKILLS = 50
# Nur diese Kopfzeilen werden gelesen; alles andere wird ignoriert.
FIELDS = ("name", "beschreibung", "ausloeser", "werkzeuge")

log = logging.getLogger("brauny.skills")

FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.S)
WORD = re.compile(r"[a-zäöüß0-9_]+")


@dataclass
class Skill:
    name: str
    beschreibung: str = ""
    ausloeser: list[str] = field(default_factory=list)
    werkzeuge: list[str] = field(default_factory=list)
    anleitung: str = ""
    quelle: str = ""

    def prompt(self) -> str:
        """Der Block, der vor die Aufgabe kommt."""
        kopf = f"Arbeitsweise für diese Art Aufgabe ({self.name}):"
        return f"{kopf}\n{self.anleitung.strip()}"


def _split_list(value: str) -> list[str]:
    return [part.strip().lower() for part in value.split(",") if part.strip()]


def parse(text: str, quelle: str = "") -> Skill | None:
    """Liest eine Skill-Datei. Gibt None zurueck, wenn sie unbrauchbar ist.

    Bewusst kein YAML: eine Abhaengigkeit weniger, und das Format ist so
    einfach, dass mehr auch nicht noetig ist.
    """
    match = FRONTMATTER.match(text or "")
    if not match:
        return None
    kopf, koerper = match.group(1), match.group(2).strip()
    if not koerper:
        return None

    werte: dict[str, str] = {}
    for zeile in kopf.splitlines():
        if ":" not in zeile:
            continue
        schluessel, _, wert = zeile.partition(":")
        schluessel = schluessel.strip().lower()
        if schluessel in FIELDS:
            werte[schluessel] = wert.strip()

    name = werte.get("name") or Path(quelle).stem
    if not name:
        return None
    return Skill(
        name=name.strip().lower(),
        beschreibung=werte.get("beschreibung", ""),
        ausloeser=_split_list(werte.get("ausloeser", "")),
        werkzeuge=_split_list(werte.get("werkzeuge", "")),
        anleitung=koerper,
        quelle=quelle,
    )


def load(directory) -> list[Skill]:
    """Laedt alle *.md aus einem Verzeichnis. Fehlt es, gibt es eben keine."""
    ordner = Path(directory)
    if not ordner.is_dir():
        return []
    gefunden: list[Skill] = []
    for pfad in sorted(ordner.glob("*.md"))[:MAX_SKILLS]:
        try:
            if pfad.stat().st_size > MAX_SKILL_BYTES:
                continue
            # errors="replace" statt strikt: eine Datei in Latin-1 wuerde sonst
            # UnicodeDecodeError werfen. Das ist ein ValueError, kein OSError -
            # und weil load() beim Start des Dienstes laeuft, wuerde eine
            # einzige solche Datei im Skill-Ordner den ganzen Server am
            # Hochfahren hindern.
            skill = parse(pfad.read_text(encoding="utf-8", errors="replace"),
                          pfad.name)
        except Exception as exc:
            # Ein kaputter Skill darf die anderen nicht mitreissen - aber der
            # Betreiber soll erfahren, warum einer fehlt.
            log.warning("Skill-Datei %s uebersprungen: %s: %s",
                        pfad.name, type(exc).__name__, exc)
            continue
        if skill is not None:
            gefunden.append(skill)
    return gefunden


# Ab dieser Laenge darf ein Ausloeser auch INNERHALB eines Wortes zaehlen.
#
# Deutsch setzt zusammen: "Sicherheitslücken", "Datenverarbeitung",
# "Programmierung" sind je EIN Wort. Ein rein wortgenauer Abgleich findet
# darin weder "sicherheit" noch "daten" noch "programm" - im Betrieb ging
# "Prüfe den Code auf Sicherheitslücken" deshalb an den Review-Skill statt an
# den Sicherheits-Skill.
#
# Kurze Ausloeser bleiben wortgenau, sonst zuendet "test" in "Kontext" und
# "code" in "Decoder". Sieben Zeichen sind lang genug, dass ein zufaelliges
# Vorkommen unwahrscheinlich wird, und kurz genug fuer die ueblichen
# Grundwoerter.
TEILWORT_AB = 7


def score(task: str, skill: Skill) -> int:
    """Wie gut passt der Skill zur Aufgabe?

    Der Name zaehlt doppelt, weil er meist deutlicher ist als ein einzelnes
    Ausloeserwort.
    """
    klein = (task or "").lower()
    woerter = set(WORD.findall(klein))
    if not woerter:
        return 0

    def zuendet(ausloeser: str) -> bool:
        # Mehrwortige Ausloeser ("leerer test") stehen nach dem Komma-Split
        # als EIN Eintrag mit Leerzeichen da und koennen in einer Menge
        # einzelner Woerter nie vorkommen - sie haetten also nie gezuendet,
        # ohne dass irgendwo ein Fehler auftaucht.
        if " " in ausloeser:
            return ausloeser in klein
        if ausloeser in woerter:
            return True
        if len(ausloeser) >= TEILWORT_AB:
            return any(ausloeser in wort for wort in woerter)
        return False

    treffer = sum(1 for wort in skill.ausloeser if zuendet(wort))
    if skill.name in woerter:
        treffer += 2
    return treffer


def match(task: str, skills) -> Skill | None:
    """Der am besten passende Skill, oder None.

    Im Zweifel None: ein falsch gewaehlter Skill lenkt das Modell in die
    falsche Richtung und ist schlimmer als gar keiner.
    """
    beste, bester_wert = None, 0
    for skill in skills or []:
        wert = score(task, skill)
        if wert > bester_wert:
            beste, bester_wert = skill, wert
    return beste


def overview(skills) -> list[str]:
    """Kurzliste fuer /healthz."""
    return [f"{s.name}: {s.beschreibung}".strip(": ") for s in skills or []]
