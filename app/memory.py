"""Fehlergedaechtnis: was schon einmal kaputt war und was geholfen hat.

Ein Agent, der denselben Fehler zum dritten Mal auf dieselbe Weise falsch
angeht, ist kein Werkzeug, sondern eine Zumutung. Hier wird deshalb
festgehalten, welcher Befund schon einmal auftrat und welche Aenderung ihn
danach zum Verschwinden gebracht hat.

Das ist ausdruecklich KEIN Lernen im Sinne von Gewichtsaenderung. Die
Gewichte des Modells bleiben, wie sie sind - was sich aendert, ist, was es zu
sehen bekommt. Der Unterschied ist wichtig: dieser Weg ist sofort wirksam,
umkehrbar, einsehbar und braucht keine GPU. Nachtrainieren waere auf der
Zielhardware ohnehin unmoeglich.

Was NICHT gespeichert wird
--------------------------
Kein Quelltext, keine Prompts, keine Werte aus dem Programm. Ein
Fehlergedaechtnis, das Dateiinhalte mitschreibt, ist ein Datenleck mit
Zusatznutzen. Gespeichert wird der Fingerabdruck des Befundes, die
zugehoerige Meldung und eine knappe Beschreibung der Aenderung - also
Werkzeugname und Pfad, nicht der Inhalt.

Warum SQLite und nicht eine JSON-Datei
--------------------------------------
Es duerfen zwei Laeufe gleichzeitig arbeiten (BRAUNY_MAX_CONCURRENT). Zwei
Prozesse, die dieselbe JSON-Datei lesen, aendern und zurueckschreiben,
verlieren Eintraege - und zwar still. SQLite serialisiert das selbst und
gehoert zur Standardbibliothek.
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing

# Wie viele fruehere Loesungen zu einem Fingerabdruck hoechstens gezeigt
# werden. Mehr hilft dem Modell nicht, kostet aber Kontext - und Kontext ist
# auf einem 7B die knappste Ressource ueberhaupt.
MAX_TREFFER = 3

# Ab wann ein Eintrag als veraltet gilt. Ein Fix von vor einem Jahr kann sich
# auf Code beziehen, den es nicht mehr gibt.
MAX_ALTER_TAGE = int(os.environ.get("BRAUNY_MEMORY_TAGE", "180"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fehler (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint  TEXT NOT NULL,
    kategorie    TEXT NOT NULL,
    nachricht    TEXT NOT NULL,
    loesung      TEXT NOT NULL,
    zeitpunkt    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS fehler_fp ON fehler(fingerprint);
"""


class Gedaechtnis:
    """Eintraege zu Fehlern und ihren Loesungen, an ein Projekt gebunden."""

    def __init__(self, pfad: str):
        self.pfad = pfad
        ordner = os.path.dirname(os.path.abspath(pfad))
        if ordner:
            os.makedirs(ordner, exist_ok=True)
        with closing(self._verbinden()) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def _verbinden(self) -> sqlite3.Connection:
        # timeout: der zweite Lauf wartet, statt sofort mit 'database is
        # locked' abzubrechen. Fuenf Sekunden sind fuer die winzigen Schreib-
        # vorgaenge hier reichlich.
        return sqlite3.connect(self.pfad, timeout=5.0)

    def merken(self, befund, loesung: str) -> bool:
        """Haelt fest, was einen Befund behoben hat.

        Gibt False zurueck, wenn nichts Brauchbares dabei war - eine leere
        Loesung zu speichern wuerde spaeter nur Platz im Kontext kosten und
        nichts sagen.
        """
        loesung = (loesung or "").strip()
        if not loesung:
            return False
        with closing(self._verbinden()) as conn:
            conn.execute(
                "INSERT INTO fehler (fingerprint, kategorie, nachricht,"
                " loesung, zeitpunkt) VALUES (?, ?, ?, ?, ?)",
                (befund.fingerprint(), befund.kategorie,
                 (befund.nachricht or "")[:300], loesung[:300], time.time()))
            conn.commit()
        return True

    def nachschlagen(self, befund) -> list[dict]:
        """Frueher Gesehenes zu genau diesem Fingerabdruck, neueste zuerst."""
        grenze = time.time() - MAX_ALTER_TAGE * 86400
        with closing(self._verbinden()) as conn:
            zeilen = conn.execute(
                "SELECT loesung, nachricht, zeitpunkt FROM fehler"
                " WHERE fingerprint = ? AND zeitpunkt >= ?"
                " ORDER BY zeitpunkt DESC LIMIT ?",
                (befund.fingerprint(), grenze, MAX_TREFFER)).fetchall()
        return [{"loesung": z[0], "nachricht": z[1], "zeitpunkt": z[2]}
                for z in zeilen]

    def hinweis(self, befund) -> str:
        """Text fuer das Modell - oder leer, wenn nichts bekannt ist.

        Bewusst als Hinweis formuliert und nicht als Anweisung: was damals
        geholfen hat, muss heute nicht richtig sein. Der Agent soll es pruefen,
        nicht befolgen.
        """
        treffer = self.nachschlagen(befund)
        if not treffer:
            return ""
        zeilen = ["Dieser Fehler kam hier schon vor. Was damals geholfen hat "
                  "(nicht ungeprüft übernehmen):"]
        for t in treffer:
            alter = max(0, int((time.time() - t["zeitpunkt"]) / 86400))
            wann = "heute" if alter == 0 else f"vor {alter} Tag(en)"
            zeilen.append(f"  - {wann}: {t['loesung']}")
        return "\n".join(zeilen)

    def aufraeumen(self) -> int:
        """Veraltete Eintraege entfernen. Gibt die Anzahl zurueck."""
        grenze = time.time() - MAX_ALTER_TAGE * 86400
        with closing(self._verbinden()) as conn:
            weg = conn.execute("DELETE FROM fehler WHERE zeitpunkt < ?",
                               (grenze,)).rowcount
            conn.commit()
        return weg

    def anzahl(self) -> int:
        with closing(self._verbinden()) as conn:
            return conn.execute("SELECT COUNT(*) FROM fehler").fetchone()[0]


def loesung_beschreiben(protokoll: list[dict], ab_schritt: int) -> str:
    """Aus dem Protokoll ableiten, was zwischen Fehler und Gruen passiert ist.

    Nur Werkzeugname und Pfad - nicht der geaenderte Inhalt. Damit bleibt der
    Eintrag klein, aussagekraeftig und frei von Quelltext.
    """
    schritte = []
    for eintrag in protokoll:
        if eintrag["nr"] <= ab_schritt or not eintrag["ok"]:
            continue
        pfade = [a["pfad"] for a in eintrag.get("dateien", [])]
        if not pfade:
            continue
        schritte.append(f"{eintrag['werkzeug']}({', '.join(sorted(set(pfade)))})")
    # Wiederholungen zusammenfassen: dreimal edit_file auf derselben Datei ist
    # eine Information, nicht drei.
    gesehen = []
    for s in schritte:
        if s not in gesehen:
            gesehen.append(s)
    return " → ".join(gesehen)
