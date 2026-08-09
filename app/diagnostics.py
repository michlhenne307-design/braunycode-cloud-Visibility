"""Rohe Werkzeugausgabe in einheitliche Befunde uebersetzen.

Ein Agent bekommt heute drei verschiedene Sprachen zu lesen: einen Python-
Traceback aus dem Container, die deutsche Fehlermeldung von check_syntax und
die 'FEHLER:'-Zeilen der Werkzeuge selbst. Er muss jedes Mal neu raten, wo der
Dateiname steht und was ueberhaupt kaputt ist - und rate er falsch, sucht er
an der falschen Stelle.

Dieses Modul macht daraus einen Befund mit festen Feldern. Bewusst ohne
Modell: ein Parser, der raet, waere genau der Fehler, den er beheben soll.
Was nicht sicher erkannt wird, bleibt None und wird nicht erfunden.

Der Nutzen entsteht erst im Zusammenspiel: derselbe Befund ist Eingabe fuer
die Reparatur, Schluessel fuer das Fehlergedaechtnis und Zeile im Protokoll.
Deshalb ist die Kategorie grob und stabil gehalten statt fein und wackelig.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Kategorien. Absichtlich wenige: sie sollen ueber Jahre gleich bleiben, weil
# das Fehlergedaechtnis darauf zeigt. Feiner unterscheiden kann man spaeter
# ueber 'typ', ohne alte Eintraege zu entwerten.
SYNTAX = "SYNTAX"
TYPE = "TYPE"
NAME = "NAME"
IMPORT = "IMPORT"
ASSERTION = "ASSERTION"
RUNTIME = "RUNTIME"
TIMEOUT = "TIMEOUT"
TOOL = "TOOL"
UNKNOWN = "UNKNOWN"

FEHLER = "ERROR"
WARNUNG = "WARNING"

# Ausnahmetyp -> Kategorie. Alles Unbekannte wird RUNTIME, nicht UNKNOWN:
# eine Ausnahme ist immer ein Laufzeitfehler, auch wenn wir sie nicht kennen.
_KATEGORIEN = {
    "SyntaxError": SYNTAX,
    "IndentationError": SYNTAX,
    "TabError": SYNTAX,
    "TypeError": TYPE,
    "ValueError": TYPE,
    "NameError": NAME,
    "AttributeError": NAME,
    "UnboundLocalError": NAME,
    "ImportError": IMPORT,
    "ModuleNotFoundError": IMPORT,
    "AssertionError": ASSERTION,
    "TimeoutError": TIMEOUT,
}

# Das Projekt wird unter /app in den Container gehaengt. Der Agent arbeitet
# aber mit projektrelativen Pfaden - ohne diese Umrechnung sucht er nach
# '/app/main.py' und findet nichts.
_CONTAINER_WURZEL = "/app/"

_RAHMEN = re.compile(r'^\s*File "([^"]+)", line (\d+)(?:, in (\S+))?', re.M)
# Die Abschlusszeile eines Tracebacks: 'Modul.Fehler: Text' oder 'Fehler: Text'.
# Der Doppelpunkt darf fehlen (z. B. blosses 'KeyboardInterrupt').
_ABSCHLUSS = re.compile(
    r"^(?P<typ>(?:[A-Za-z_][\w.]*\.)?[A-Z]\w*(?:Error|Exception|Exit|Interrupt))"
    r"(?::\s*(?P<text>.*))?$"
)
# check_syntax spricht deutsch - eigenes Muster, damit sein Befund dieselben
# Felder bekommt wie ein Traceback.
_CHECK_SYNTAX = re.compile(r"^SyntaxError in (?P<datei>.+?), Zeile (?P<zeile>\d+): (?P<text>.*)$")
_WERKZEUGFEHLER = re.compile(r"^FEHLER: (?:(?P<typ>\w+Error|\w+Exception): )?(?P<text>.*)$")


def _rel(pfad: str) -> str:
    """Containerpfad auf den projektrelativen Pfad zurueckfuehren."""
    if pfad.startswith(_CONTAINER_WURZEL):
        return pfad[len(_CONTAINER_WURZEL):]
    return pfad


@dataclass(frozen=True)
class Finding:
    """Ein Befund. Felder, die nicht sicher erkannt wurden, bleiben None."""

    kategorie: str
    schwere: str
    nachricht: str
    quelle: str = "unbekannt"
    typ: str | None = None
    datei: str | None = None
    zeile: int | None = None
    symbol: str | None = None
    roh: str = field(default="", repr=False)

    def fingerprint(self) -> str:
        """Stabiler Schluessel fuers Fehlergedaechtnis.

        Bewusst OHNE Zeilennummer und ohne den freien Text: derselbe Fehler
        wandert bei jeder Umformatierung eine Zeile weiter, und die Meldung
        enthaelt oft wechselnde Werte. Wer danach sucht, will 'dieser Fehler
        in dieser Datei an diesem Symbol' wiederfinden, nicht 'exakt dieselbe
        Zeichenkette'.
        """
        return "|".join([self.kategorie, self.typ or "-",
                         self.datei or "-", self.symbol or "-"])

    def einzeiler(self) -> str:
        ort = self.datei or "?"
        if self.zeile is not None:
            ort += f":{self.zeile}"
        kopf = f"{self.kategorie}"
        if self.typ and self.typ != self.kategorie:
            kopf += f"/{self.typ}"
        return f"{kopf} {ort} — {self.nachricht}"

    def to_dict(self) -> dict:
        return {"kategorie": self.kategorie, "schwere": self.schwere,
                "typ": self.typ, "datei": self.datei, "zeile": self.zeile,
                "symbol": self.symbol, "nachricht": self.nachricht,
                "quelle": self.quelle}


def kategorie_fuer(typ: str) -> str:
    """Ausnahmetyp -> Kategorie. Punktierte Namen werden hinten aufgeloest,
    damit 'json.JSONDecodeError' nicht als unbekannt durchfaellt."""
    kurz = typ.rsplit(".", 1)[-1]
    return _KATEGORIEN.get(kurz, RUNTIME)


def _tracebacks(text: str, quelle: str) -> list[Finding]:
    """Jeden Traceback im Text zu einem Befund verdichten.

    Genommen wird der LETZTE Rahmen vor der Abschlusszeile - dort ist der
    Fehler passiert. Der erste Rahmen ist der Einstiegspunkt und fuer die
    Reparatur uninteressant.
    """
    befunde: list[Finding] = []
    zeilen = text.splitlines()
    start = None

    for i, zeile in enumerate(zeilen):
        if zeile.startswith("Traceback (most recent call last)"):
            start = i
            continue
        if start is None:
            continue
        treffer = _ABSCHLUSS.match(zeile.strip())
        if not treffer or not zeile[:1].strip():
            # Eingerueckte Zeilen gehoeren zum Rumpf; die Abschlusszeile steht
            # ohne Einrueckung. Ohne diese Pruefung wuerde eine ausgegebene
            # Fehlermeldung im Rumpf den Traceback vorzeitig beenden.
            continue
        block = "\n".join(zeilen[start:i + 1])
        rahmen = _RAHMEN.findall(block)
        datei = zeile_nr = symbol = None
        if rahmen:
            letzter = rahmen[-1]
            datei = _rel(letzter[0])
            zeile_nr = int(letzter[1])
            symbol = letzter[2] or None
        typ = treffer.group("typ")
        befunde.append(Finding(
            kategorie=kategorie_fuer(typ), schwere=FEHLER, typ=typ,
            nachricht=(treffer.group("text") or "").strip() or typ,
            datei=datei, zeile=zeile_nr, symbol=symbol,
            quelle=quelle, roh=block,
        ))
        start = None
    return befunde


def _kopfloser_syntaxfehler(text: str, quelle: str) -> list[Finding]:
    """Der Sonderfall ohne 'Traceback'-Kopf.

    Scheitert eine Datei schon beim Parsen, gibt Python keinen Traceback aus,
    sondern nur:

        File "/app/kaputt.py", line 1
            def f(:
                  ^
        SyntaxError: invalid syntax

    Genau dieser Fall ist der haeufigste direkt nach einer Aenderung durch den
    Agenten - er darf nicht durchrutschen, nur weil das Kopfwort fehlt.
    """
    zeilen = text.splitlines()
    befunde: list[Finding] = []
    for i, zeile in enumerate(zeilen):
        if zeile[:1].strip() == "" and zeile.strip():
            continue                      # eingerueckt: gehoert zum Rumpf
        treffer = _ABSCHLUSS.match(zeile.strip())
        if not treffer or kategorie_fuer(treffer.group("typ")) != SYNTAX:
            continue
        # Den zugehoerigen Rahmen rueckwaerts suchen - er steht unmittelbar
        # ueber dem Ausschnitt, nicht irgendwo im Text.
        rahmen = _RAHMEN.findall("\n".join(zeilen[max(0, i - 6):i]))
        if not rahmen:
            continue
        datei, zeile_nr, symbol = rahmen[-1]
        befunde.append(Finding(
            kategorie=SYNTAX, schwere=FEHLER, typ=treffer.group("typ"),
            nachricht=(treffer.group("text") or "").strip() or treffer.group("typ"),
            datei=_rel(datei), zeile=int(zeile_nr), symbol=symbol or None,
            quelle=quelle, roh="\n".join(zeilen[max(0, i - 6):i + 1])))
    return befunde


def parse(text: str, quelle: str = "sandbox") -> list[Finding]:
    """Alle erkennbaren Befunde aus einer Werkzeugausgabe.

    Leere Liste heisst 'nichts sicher erkannt', nicht 'alles in Ordnung' -
    der Aufrufer darf daraus keinen Erfolg ableiten.
    """
    if not text or not text.strip():
        return []

    befunde = _tracebacks(text, quelle)
    if befunde:
        return befunde

    # Erst wenn kein Traceback da ist - sonst wuerde ein SyntaxError INNERHALB
    # eines Tracebacks (kaputtes Modul beim Import) doppelt gezaehlt.
    befunde = _kopfloser_syntaxfehler(text, quelle)
    if befunde:
        return befunde

    for zeile in text.splitlines():
        blank = zeile.strip()
        if not blank:
            continue

        treffer = _CHECK_SYNTAX.match(blank)
        if treffer:
            befunde.append(Finding(
                kategorie=SYNTAX, schwere=FEHLER, typ="SyntaxError",
                nachricht=treffer.group("text"),
                datei=_rel(treffer.group("datei")),
                zeile=int(treffer.group("zeile")),
                quelle=quelle, roh=blank))
            continue

        treffer = _WERKZEUGFEHLER.match(blank)
        if treffer:
            typ = treffer.group("typ")
            befunde.append(Finding(
                # Ohne Ausnahmetyp ist es eine abgewiesene Werkzeugnutzung
                # (fehlendes Argument, gesperrtes Werkzeug), kein Absturz.
                kategorie=kategorie_fuer(typ) if typ else TOOL,
                schwere=FEHLER, typ=typ,
                nachricht=treffer.group("text"), quelle=quelle, roh=blank))
            continue

        if "Zeitüberschreitung" in blank or "timed out" in blank.lower():
            befunde.append(Finding(kategorie=TIMEOUT, schwere=FEHLER,
                                   nachricht=blank, quelle=quelle, roh=blank))

    return befunde


def zusammenfassen(befunde: list[Finding], grenze: int = 3) -> str:
    """Kurzfassung fuer die Oberflaeche und fuer den Verlauf des Modells."""
    if not befunde:
        return ""
    zeilen = [f.einzeiler() for f in befunde[:grenze]]
    if len(befunde) > grenze:
        zeilen.append(f"… und {len(befunde) - grenze} weitere")
    return "\n".join(zeilen)
