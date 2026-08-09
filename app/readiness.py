"""Vor dem Schreiben pruefen, ob der Auftrag ueberhaupt eindeutig ist.

Der teuerste Fehler eines Agenten ist nicht der Absturz, sondern die saubere
Ausfuehrung am falschen Ziel. Nennt ein Auftrag `berechne_summe` und es gibt
nur `berechne_summen` und `berechne_saldo`, dann waehlt das Modell eines davon
und arbeitet ueberzeugend an der falschen Stelle. Hinterher stimmt jede
Pruefung - nur nicht die Aufgabe.

Dieses Modul stellt deshalb genau eine Frage: Existiert alles, was der Auftrag
beim Namen nennt? Das ist deterministisch beantwortbar, kostet keinen
Modellaufruf und trifft die Luecke, die wirklich weh tut.

Was es ausdruecklich NICHT tut: die Aufgabe inhaltlich bewerten, Vollstaendig-
keit messen oder eine Prozentzahl fuer Mehrdeutigkeit ausgeben. Ein
Klassifikator, der "95 % mehrdeutig" sagt, hat sich die Zahl ausgedacht.
Hier gibt es nur nachpruefbare Aussagen: dieser Name kommt im Projekt vor,
oder er kommt nicht vor.

Fragen werden nur zu entscheidungsrelevanten Luecken gestellt - nicht fuenf
Stueck aus Prinzip.
"""

from __future__ import annotations

import difflib
import posixpath
import re

BEREIT = "READY"
BEREIT_MIT_ANNAHME = "READY_WITH_ASSUMPTIONS"
RUECKFRAGE = "CLARIFICATION_REQUIRED"

# Endungen, an denen ein Wort im Auftragstext als Dateiname gilt.
ENDUNGEN = (".py", ".js", ".ts", ".tsx", ".jsx", ".md", ".json", ".yaml",
            ".yml", ".toml", ".cfg", ".ini", ".txt", ".html", ".css", ".sh")

# Dateiname: Wort mit bekannter Endung, optional mit Pfad davor.
_DATEI = re.compile(r"[\w./-]*\w+(?:" + "|".join(re.escape(e) for e in ENDUNGEN) + r")\b")

# Bezeichner, aber nur die eindeutigen Faelle: snake_case mit Unterstrich, oder
# CamelCase mit mindestens zwei Grossbuchstaben, oder Name mit Klammern.
# Gewoehnliche deutsche Woerter duerfen hier nicht hineingeraten - eine
# Rueckfrage zu "Datenbank" waere Laerm und wuerde den Agenten unbrauchbar
# machen.
_BEZEICHNER = re.compile(r"\b(?:[a-z][a-z0-9]*(?:_[a-z0-9]+)+"
                         r"|[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*)\b")
_MIT_KLAMMERN = re.compile(r"\b([A-Za-z_]\w*)\s*\(\s*\)")

# Ab wie vielen aehnlichen Kandidaten eine Rueckfrage sinnvoll ist. Bei genau
# einem naheliegenden Treffer wird stattdessen die Annahme benannt: eine Frage,
# deren Antwort ohnehin feststeht, ist nur eine Verzoegerung.
_AEHNLICHKEIT = 0.72


# Steht vor dem Dateinamen eine Anlege-Absicht, ist "gibt es nicht" kein
# Mangel, sondern der Auftrag. Verlangt wird ein Datei-Wort ZWISCHEN Absicht
# und Name oder der Name direkt dahinter - sonst wuerde "fuege eine neue
# Funktion in app/kern.py ein" als Dateianlage gelesen, obwohl sich das "neu"
# auf die Funktion bezieht und die Datei sehr wohl existieren soll.
_ANLEGEN = re.compile(
    r"\b(?:neue[nsr]?|erstell\w*|leg\w*|anleg\w*|schreib\w*|create|new)\b"
    # Bis zu zwei Artikel dazwischen: "erstelle DIE DATEI x.py".
    r"(?:\s+(?:die|der|das|den|dem|ein|eine|einen|einem|neue[nsr]?|the|an?)\b){0,2}"
    r"(?:\s+\w*(?:datei|modul|skript|script|file|module)\w*)?\s+$",
    re.I)


def _soll_angelegt_werden(text: str, treffer_start: int) -> bool:
    """Deutet der Text unmittelbar vor dem Dateinamen auf Neuanlage?"""
    davor = text[max(0, treffer_start - 40):treffer_start]
    return bool(_ANLEGEN.search(davor))


def _dateikandidaten(text: str) -> set[str]:
    return {t for t in _DATEI.findall(text) if not t.startswith(".")}


def _bezeichnerkandidaten(text: str) -> set[str]:
    treffer = set(_BEZEICHNER.findall(text)) | set(_MIT_KLAMMERN.findall(text))
    # Was schon als Dateiname erkannt wurde, ist kein Symbol.
    return {t for t in treffer if not t.endswith(ENDUNGEN)}


def _naechste(name: str, kandidaten) -> list[str]:
    exakt = [k for k in kandidaten if posixpath.basename(k) == name or k == name]
    if exakt:
        return exakt
    return difflib.get_close_matches(name, list(kandidaten), n=3,
                                     cutoff=_AEHNLICHKEIT)


class Urteil:
    """Ergebnis der Bereitschaftspruefung."""

    def __init__(self, stand: str, fragen: list[str], annahmen: list[str]):
        self.stand = stand
        self.fragen = fragen
        self.annahmen = annahmen

    @property
    def darf_starten(self) -> bool:
        return self.stand != RUECKFRAGE

    def text(self) -> str:
        zeilen = []
        if self.annahmen:
            zeilen.append("Angenommen:")
            zeilen.extend(f"  - {a}" for a in self.annahmen)
        if self.fragen:
            zeilen.append("Bevor ich etwas ändere, brauche ich eine Antwort:")
            zeilen.extend(f"  - {f}" for f in self.fragen)
        return "\n".join(zeilen)


def pruefen(task: str, dateien, symbole=()) -> Urteil:
    """Nennt der Auftrag etwas, das es im Projekt nicht gibt?

    dateien: projektrelative Pfade. symbole: bekannte Funktions- und
    Klassennamen, etwa aus dem Codeindex.

    Ein leeres Projekt loest nie eine Rueckfrage aus - dort wird etwas Neues
    gebaut, und dass die Datei noch nicht existiert, ist der Normalfall.
    """
    dateien = list(dateien)
    symbole = list(symbole)
    fragen: list[str] = []
    annahmen: list[str] = []

    if not dateien:
        return Urteil(BEREIT, [], [])

    basisnamen = {posixpath.basename(p) for p in dateien}

    for genannt in sorted(_dateikandidaten(task)):
        stelle = task.find(genannt)
        neu_gewollt = stelle >= 0 and _soll_angelegt_werden(task, stelle)
        vorhanden = (genannt in dateien
                     or posixpath.basename(genannt) in basisnamen)

        if vorhanden and not neu_gewollt:
            continue
        if vorhanden:
            # "Lege tools.py an", und tools.py gibt es schon. Ueberschreiben
            # und Ergaenzen sind verschiedene Arbeiten - die Pruefung muss VOR
            # dem Vorhandensein greifen, sonst faellt der Widerspruch weg.
            fragen.append(f"„{genannt}“ soll neu entstehen, aber die Datei "
                          "gibt es schon. Überschreiben oder etwas anderes "
                          "gemeint?")
            continue

        nah = _naechste(posixpath.basename(genannt), dateien)

        if neu_gewollt and not nah:
            continue          # genau das war der Auftrag
        if neu_gewollt:
            # Beides ist plausibel und fuehrt zu verschiedener Arbeit: eine
            # zweite, fast gleich heissende Datei anlegen oder die vorhandene
            # anfassen. Das ist entscheidungsrelevant.
            fragen.append(f"„{genannt}“ soll neu entstehen, aber es gibt "
                          f"schon {' bzw. '.join(nah)}. Wirklich zusätzlich "
                          "anlegen?")
        elif len(nah) == 1:
            annahmen.append(f"„{genannt}“ meint {nah[0]}")
        elif nah:
            fragen.append(f"„{genannt}“ gibt es nicht. Meinst du "
                          + " oder ".join(nah) + "?")
        else:
            fragen.append(f"„{genannt}“ gibt es im Projekt nicht, und nichts "
                          "kommt dem nahe. Soll die Datei neu angelegt werden?")

    if symbole:
        bekannt = set(symbole)
        for genannt in sorted(_bezeichnerkandidaten(task)):
            if genannt in bekannt:
                continue
            nah = _naechste(genannt, bekannt)
            if len(nah) == 1:
                annahmen.append(f"„{genannt}“ meint {nah[0]}")
            elif nah:
                fragen.append(f"„{genannt}“ gibt es nicht. Meinst du "
                              + " oder ".join(nah) + "?")
            # Kein Treffer und nichts Aehnliches: das ist meist ein Name fuer
            # etwas Neues. Danach zu fragen waere die Rueckfrage aus Prinzip,
            # die dieses Modul gerade vermeiden soll.

    if fragen:
        return Urteil(RUECKFRAGE, fragen, annahmen)
    if annahmen:
        return Urteil(BEREIT_MIT_ANNAHME, [], annahmen)
    return Urteil(BEREIT, [], [])
