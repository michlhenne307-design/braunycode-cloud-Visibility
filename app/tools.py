"""Die Werkzeuge, mit denen der Agent am Projekt arbeitet.

Das ist der Unterschied zwischen einem Code-Generator und einem Agenten: Statt
einmal zu raten und fertig zu sein, entscheidet das Modell selbst, was als
Naechstes zu tun ist - erst schauen, dann lesen, dann aendern, dann pruefen.

Jedes Werkzeug arbeitet ausschliesslich innerhalb des Projektverzeichnisses.
Die Pfadpruefung liegt in workspace.py und wird hier nicht umgangen.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import fnmatch
import hashlib
import json
import re
import shlex
import time

import connectors
import diagnostics
import memory as memory_mod
import testimpact

MAX_OUTPUT = 4000        # Zeichen, die ein Werkzeugergebnis zurueckgeben darf
MAX_SEARCH_HITS = 25
# Deckel fuer das, was in die Sandbox kopiert wird. Ein grosses
# Projektverzeichnis wuerde sonst jeden Container-Start ausbremsen.
MAX_SANDBOX_FILES = 60
MAX_SANDBOX_BYTES = 400_000

# Ohne dieses Werkzeug kann die Schleife nicht sauber enden - es bleibt
# deshalb auch dann erlaubt, wenn ein Skill die Werkzeuge einschraenkt.
ALWAYS = "finish"

# Werkzeuge, die den Projektstand veraendern, und solche, die ihn pruefen.
# Aus dem Zusammenspiel der beiden Mengen ergibt sich, ob der aktuelle Stand
# belegt ist oder nur behauptet.
MODIFYING = {"write_file", "edit_file", "rename_symbol", "delete_file",
             "move_file"}
CHECKING = {"check_syntax", "run_python", "run_command"}

# Anfaenge, an denen eine Pruefung als bestanden gilt. Bewusst an den
# Rueckgabetexten der Werkzeuge festgemacht und nicht am Exit-Code: was das
# Modell zu sehen bekommt, ist genau das, was hier gewertet wird.
GREEN_PREFIXES = ("Lauf erfolgreich", "Befehl erfolgreich")
GREEN_SUFFIX_SYNTAX = ": Syntax in Ordnung."

# So oft darf 'finish' abgewiesen werden, bevor der Lauf trotzdem enden darf -
# dann aber ausdruecklich als unbelegt. Ohne diesen Deckel koennte ein Modell,
# das die Pruefung nicht hinbekommt, die Schleife bis zum Schrittlimit drehen.
FINISH_BLOCK_LIMIT = 2


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
    pfad = {"type": "string", "description": "Pfad relativ zum Projekt"}
    return [
        # ---------------------------------------------------------- Lesen
        _tool("list_files", "Listet alle Dateien im Projekt auf.", {}, []),
        _tool("glob", "Findet Dateien über ein Muster, z. B. '*.py' oder 'src/*.md'.",
              {"pattern": {"type": "string",
                           "description": "Muster; ohne '/' wird der Dateiname verglichen"}},
              ["pattern"]),
        _tool("read_file",
              "Liest eine Datei. Bei großen Dateien mit offset und limit "
              "abschnittsweise lesen, statt alles auf einmal zu holen.",
              {"path": pfad,
               "offset": {"type": "integer",
                          "description": "Erste Zeile (1-basiert), optional"},
               "limit": {"type": "integer",
                         "description": "Anzahl Zeilen, optional"}},
              ["path"]),
        _tool("search", "Sucht Text in den Projektdateien.",
              {"query": {"type": "string", "description": "Suchbegriff"},
               "regex": {"type": "boolean",
                         "description": "query als regulären Ausdruck lesen"},
               "glob": {"type": "string",
                        "description": "Nur Dateien, die diesem Muster entsprechen"},
               "context": {"type": "integer",
                           "description": "Zeilen Kontext um jeden Treffer"}},
              ["query"]),
        _tool("outline", "Zeigt Funktionen, Klassen und den Aufrufgraph des Projekts.",
              {}, []),
        _tool("symbol_info",
              "Alles zu einem Symbol: Signatur, Fundstelle, wer es aufruft, "
              "was es aufruft, und was bei einer Änderung brechen könnte.",
              {"name": {"type": "string",
                        "description": "Name der Funktion, Klasse oder Methode"}},
              ["name"]),

        # ---------------------------------------------------------- Ändern
        _tool("edit_file",
              "Ersetzt eine Textstelle in einer Datei. Das ist der normale Weg "
              "für Änderungen — nur den Ausschnitt angeben, nicht die ganze "
              "Datei neu schreiben. old_text muss exakt und eindeutig sein.",
              {"path": pfad,
               "old_text": {"type": "string",
                            "description": "Genau der Text, der ersetzt wird"},
               "new_text": {"type": "string", "description": "Der neue Text"},
               "replace_all": {"type": "boolean",
                               "description": "Alle Vorkommen ersetzen statt genau eines"}},
              ["path", "old_text", "new_text"]),
        _tool("write_file",
              "Schreibt eine Datei komplett neu. Nur für neue Dateien oder "
              "vollständigen Ersatz — für Änderungen edit_file benutzen.",
              {"path": pfad,
               "content": {"type": "string", "description": "Vollständiger Dateiinhalt"}},
              ["path", "content"]),
        _tool("delete_file", "Löscht eine Datei aus dem Projekt.", {"path": pfad},
              ["path"]),
        _tool("move_file", "Verschiebt oder benennt eine Datei um.",
              {"source": pfad,
               "destination": {"type": "string", "description": "Neuer Pfad"}},
              ["source", "destination"]),
        _tool("rename_symbol",
              "Benennt eine Funktion, Klasse oder Variable über den Syntaxbaum "
              "um — formaterhaltend, ohne Zeichenketten und fremde Attribute "
              "anzufassen. Genauer als eine Textersetzung.",
              {"path": pfad,
               "old_name": {"type": "string", "description": "Bisheriger Name"},
               "new_name": {"type": "string", "description": "Neuer Name"}},
              ["path", "old_name", "new_name"]),

        # ---------------------------------------------------------- Prüfen
        _tool("affected_tests",
              "Nennt die Testdateien, die den geänderten Code über Importe "
              "erreichen. Ohne Angabe werden die in diesem Lauf geänderten "
              "Dateien genommen. Damit läuft die kurze Prüfung statt der "
              "ganzen Suite.",
              {"paths": {"type": "string",
                         "description": "Optional, kommagetrennt. Leer lassen "
                                        "für die selbst geänderten Dateien."}},
              []),
        _tool("check_syntax",
              "Prüft eine Python-Datei auf Syntaxfehler, ohne sie auszuführen. "
              "Kostet nichts — vor jedem Lauf sinnvoll.",
              {"path": pfad}, ["path"]),
        _tool("run_python",
              "Führt eine Python-Datei in der isolierten Sandbox aus. Das ganze "
              "Projekt kommt mit, Importe funktionieren also.",
              {"path": {"type": "string", "description": "Pfad der auszuführenden Datei"}},
              ["path"]),
        _tool("run_command",
              "Führt einen Befehl in der isolierten Sandbox aus, z. B. "
              "'python -m unittest test_x.py'. Kein Netz, keine Shell, "
              "Änderungen wirken nicht auf das echte Projekt.",
              {"command": {"type": "string", "description": "Befehl mit Argumenten"}},
              ["command"]),

        # ---------------------------------------------------------- Zurück
        _tool("undo",
              "Setzt das Projekt auf den letzten Commit zurück und verwirft alle "
              "Änderungen seitdem. Der Weg zurück, wenn etwas schiefgegangen ist.",
              {}, []),

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
             "message": {"type": "string", "description": "Commit-Nachricht"},
             "projekt": {"type": "string",
                         "description": "Unterordner eines geklonten Projekts. "
                                        "Dann geht der Push auf dessen eigenen "
                                        "Remote statt auf den allgemeinen."}},
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


def _als_zahl(wert, ersatz: int) -> int:
    """Modelle liefern Zahlen gern als Text - das darf nicht werfen."""
    try:
        return int(str(wert).strip())
    except (TypeError, ValueError):
        return ersatz


def _passt(relativ: str, muster: str) -> bool:
    """Dateimuster gegen einen Projektpfad.

    Ohne '/' im Muster wird der blosse Dateiname verglichen - '*.py' meint
    dann alle Python-Dateien, egal in welchem Verzeichnis. Mit '/' wird der
    ganze Pfad verglichen.
    """
    muster = (muster or "").strip()
    if not muster:
        return False
    if "/" in muster:
        return fnmatch.fnmatch(relativ, muster)
    return fnmatch.fnmatch(relativ.rsplit("/", 1)[-1], muster)


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT] + f"\n… gekürzt ({len(text)} Zeichen insgesamt)"


def _kurz(value, limit: int = 120) -> str:
    """Argumentwert fuers Protokoll. Ein ganzer Dateiinhalt gehoert da nicht
    hinein - der steht ohnehin als Fingerabdruck daneben."""
    text = str(value).replace("\n", "⏎")
    return text if len(text) <= limit else text[:limit] + "…"


class Toolbox:
    """Fuehrt Werkzeugaufrufe gegen ein Projektverzeichnis aus.

    run_sandbox wird hineingereicht, damit die Ausfuehrung ohne echten Docker
    testbar bleibt.
    """

    def __init__(self, ws, run_sandbox=None, index_builder=None, enabled=None,
                 gedaechtnis=None):
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

        # --- Belegfuehrung -------------------------------------------------
        # Dateien, die seit der letzten bestandenen Pruefung veraendert wurden.
        # Solange hier etwas drinsteht, ist der Stand unbelegt.
        self.unverified: set[str] = set()
        # Geaenderte Dateien, ueber die keine Ausfuehrung etwas aussagen kann
        # (Text, Markdown, Konfiguration). Sie blockieren den Abschluss nicht,
        # werden aber am Ende genannt.
        self.ungeprueft_sonstige: set[str] = set()
        # Lueckenloses Protokoll aller Aufrufe. Das ist die Nachvollziehbarkeit:
        # was wurde aufgerufen, mit welchen Argumenten, was kam heraus, und wie
        # sah die Datei vorher und nachher aus.
        self.protokoll: list[dict] = []
        # Bestandene Pruefungen mit dem, was sie abgedeckt haben.
        self.belege: list[dict] = []
        # Alle erkannten Befunde des Laufs, in Reihenfolge. Grundlage fuer
        # das Fehlergedaechtnis und fuer die gezielte Reparatur.
        self.befunde: list = []
        # Optional: was frueher schon einmal kaputt war. Fehlt es,
        # arbeitet alles genauso weiter - nur ohne Vorwissen.
        self.gedaechtnis = gedaechtnis
        # Letzter bekannter Inhalts-Fingerabdruck je Datei.
        self._hashes: dict[str, str | None] = {}
        self.finish_blocked = 0
        # Wird beim Abschluss gesetzt: war der gemeldete Stand geprueft?
        self.verified = False

    def restrict(self, names) -> list[str]:
        """Schraenkt die Werkzeuge ein, etwa weil ein Skill es so vorgibt.

        Erweitern kann das nie: es wird mit dem geschnitten, was ohnehin
        erlaubt ist. Ein Skill kann also keinen Konnektor freischalten, der
        nicht konfiguriert ist. 'finish' bleibt immer drin, sonst koennte die
        Schleife nicht enden.

        Zwei Faelle, die frueher still danebengingen:

        - Ein Tippfehler in der Skill-Datei liess die Schnittmenge leer werden.
          Uebrig blieb nur 'finish', der Agent konnte nichts mehr tun und
          meldete trotzdem Erfolg. Unbekannte Namen werden jetzt verworfen,
          und bleibt danach nichts Brauchbares uebrig, wird gar nicht
          eingeschraenkt.
        - Konnektoren verschwanden, sobald ein Skill griff, weil keiner sie
          auflistet. Sie sind ausdruecklich konfiguriert worden und bleiben
          deshalb erhalten, sofern der Skill nicht selbst welche nennt.

        Gibt die verworfenen Namen zurueck, damit der Aufrufer sie melden kann.
        """
        gewuenscht = {n.strip() for n in (names or []) if n and n.strip()}
        if not gewuenscht:
            return []

        unbekannt = sorted(gewuenscht - NAMES)
        gueltig = gewuenscht & self.allowed
        if not gueltig - {ALWAYS}:
            # Nichts Brauchbares uebrig - lieber nicht einschraenken als den
            # Agenten handlungsunfaehig machen.
            return unbekannt

        neu = gueltig | {ALWAYS}
        # Nennt der Skill selbst keinen Konnektor, bleiben die konfigurierten
        # erhalten - sie waren eine bewusste Entscheidung des Betreibers.
        if not (gewuenscht & CONNECTOR_NAMES):
            neu |= set(self.enabled)
        self.allowed = neu
        return unbekannt

    def schema(self) -> list[dict]:
        """Nur die Werkzeuge, die dieser Lauf wirklich benutzen darf.

        Was das Modell nicht sieht, kann es nicht aufrufen - das ist
        wirksamer als jede Ermahnung im Prompt.
        """
        return [item for item in schema(self.enabled)
                if item["function"]["name"] in self.allowed]

    def _hash(self, path: str) -> str | None:
        """Fingerabdruck des Dateiinhalts, oder None wenn es sie nicht gibt.

        Zwoelf Hex-Stellen reichen: das ist kein Schutz gegen Manipulation,
        sondern ein Beleg dafuer, DASS sich etwas geaendert hat und was.
        """
        try:
            roh = self.ws.read(path).encode("utf-8", "replace")
        except Exception:
            return None
        return hashlib.sha256(roh).hexdigest()[:12]

    def _abdeckung(self, name: str, arguments: dict) -> set[str]:
        """Welche offenen Dateien ein gruener Lauf wirklich belegt hat.

        Ueber den Importgraphen vorwaerts, ausgehend von dem, was gestartet
        wurde. Nennt ein Befehl keine einzige Projektdatei ('python -m pytest'
        ueber alles), gilt der Lauf als vollstaendig - dann IST alles gelaufen.
        """
        try:
            dateien = self._alle_python()
        except Exception:
            # Ohne Graph lieber vollstaendig belegen als gar nicht: sonst
            # blockierte ein Lesefehler den Abschluss dauerhaft.
            return set(self.unverified)

        if name == "run_python":
            start = [str(arguments.get("path", "")).strip()]
        else:
            start = testimpact.dateien_aus_befehl(
                str(arguments.get("command", "")), dateien)
            if not start:
                return set(self.unverified)   # Lauf ueber alles

        erreicht = testimpact.abgedeckt(dateien, start)
        # Nicht-Python-Dateien kann der Graph nicht einordnen. Sie bleiben
        # offen, statt stillschweigend als belegt zu gelten.
        return self.unverified & erreicht

    def _ist_gruen(self, name: str, result: str) -> bool:
        if name == "check_syntax":
            return result.rstrip().endswith(GREEN_SUFFIX_SYNTAX)
        return result.startswith(GREEN_PREFIXES)

    async def call(self, name: str, arguments: dict) -> str:
        """Fuehrt ein Werkzeug aus und schreibt den Vorgang ins Protokoll.

        Fehler werden als Text zurueckgegeben, damit das Modell darauf
        reagieren kann statt abzubrechen.

        Nebenher wird mitgefuehrt, welche Dateien seit der letzten bestandenen
        Pruefung veraendert wurden. Genau daran haengt spaeter, ob 'finish'
        durchgeht - das Modell kann diese Buchhaltung nicht beeinflussen, weil
        sie hier passiert und nicht in seinem Text.
        """
        if name not in self.allowed:
            grund = ("ist für diese Aufgabe nicht freigegeben"
                     if name in NAMES else "gibt es nicht")
            return (f"FEHLER: Unbekanntes Werkzeug '{name}' — {grund}. "
                    f"Erlaubt: {sorted(self.allowed)}")

        vorher_written = len(self.written)
        begonnen = time.monotonic()
        try:
            handler = getattr(self, f"_{name}")
            result = _clip(await handler(arguments))
        except Exception as exc:
            result = f"FEHLER: {type(exc).__name__}: {exc}"

        ok = not result.startswith("FEHLER")
        # Welche Dateien angefasst wurden, wird nicht aus den Argumenten
        # geraten, sondern von den Werkzeugen selbst gemeldet.
        beruehrt = list(dict.fromkeys(self.written[vorher_written:]))

        aenderungen = []
        for pfad in beruehrt:
            alt = self._hashes.get(pfad)
            neu = self._hash(pfad)
            self._hashes[pfad] = neu
            aenderungen.append({"pfad": pfad, "vorher": alt, "nachher": neu})

        if ok and name in MODIFYING:
            # Nur Dateien, ueber die der Importgraph ueberhaupt etwas aussagen
            # kann. Eine .md oder .txt hat kein Verhalten, das ein Lauf
            # belegen koennte - sie in die Sperre zu legen hiesse, den Agenten
            # bei jeder README-Aenderung festzuhalten.
            self.unverified.update(p for p in beruehrt if p.endswith(".py"))
            # Verschwiegen wird sie deshalb aber nicht: am Ende steht, was
            # ausserhalb der Reichweite jeder Pruefung geaendert wurde.
            self.ungeprueft_sonstige.update(
                p for p in beruehrt if not p.endswith(".py"))
        elif ok and name in CHECKING and self._ist_gruen(name, result):
            if name == "check_syntax":
                # Belegt genau die eine Datei - und nur ihre Syntax.
                geprueft = {str(arguments.get("path", "")).strip()}
                self.unverified -= geprueft
            else:
                # Ein gruener Lauf belegt NICHT alles, was gerade offen ist -
                # nur das, was er tatsaechlich erreicht hat. 'pytest test_a.py'
                # sagt nichts ueber eine gleichzeitig geaenderte b.py, die kein
                # Test anfasst. Diese Unterscheidung ist der Unterschied
                # zwischen einem Beleg und einer Behauptung mit Testausgabe
                # daneben.
                geprueft = self._abdeckung(name, arguments)
                self.unverified -= geprueft
            if geprueft:
                self.belege.append({"nr": len(self.protokoll) + 1,
                                    "werkzeug": name,
                                    "abgedeckt": sorted(geprueft)})

        # Rohausgabe zu Befunden verdichten. Ein Traceback zwingt das Modell
        # sonst, sich Datei und Zeile selbst herauszusuchen - und dabei raet
        # es gern den ersten Rahmen statt den letzten.
        gescheitert = not ok or (name in CHECKING
                                 and not self._ist_gruen(name, result))
        befunde = diagnostics.parse(result, quelle=name) if gescheitert else []
        # Den Schritt festhalten: erst damit laesst sich spaeter sagen, was
        # NACH dem Fehler passiert ist und ihn behoben hat.
        nr = len(self.protokoll) + 1
        befunde = [dataclasses.replace(f, schritt=nr) for f in befunde]

        self.protokoll.append({
            "nr": len(self.protokoll) + 1,
            "werkzeug": name,
            "argumente": {k: _kurz(v) for k, v in (arguments or {}).items()},
            "ok": ok,
            "ms": int((time.monotonic() - begonnen) * 1000),
            "dateien": aenderungen,
            "befunde": [f.to_dict() for f in befunde],
        })
        self.befunde.extend(befunde)

        # Nur anhaengen, wenn der Befund mehr weiss als die Meldung selbst.
        # Bei "FEHLER: 'pattern' fehlt." waere die Wiederholung nur Laerm.
        ergiebig = [f for f in befunde if f.datei or f.typ]
        if ergiebig:
            result = f"{result}\n\nBefund:\n{diagnostics.zusammenfassen(ergiebig)}"
            # Vorwissen dazustellen, falls dieser Fehler hier schon einmal
            # auftrat. Als Hinweis, nicht als Anweisung - was damals half,
            # muss heute nicht richtig sein.
            if self.gedaechtnis is not None:
                hinweise = [h for h in
                            (self.gedaechtnis.hinweis(f) for f in ergiebig) if h]
                if hinweise:
                    result = f"{result}\n\n" + "\n".join(hinweise)
        return result

    def bericht(self) -> dict:
        """Der maschinell erzeugte Beleg zum Lauf.

        Bewusst nicht vom Modell formuliert: was hier steht, stammt aus der
        Buchfuehrung von call() und laesst sich gegen das Protokoll pruefen.
        """
        return {
            "schritte": len(self.protokoll),
            "fehlgeschlagen": sum(1 for e in self.protokoll if not e["ok"]),
            "geaendert": sorted({a["pfad"] for e in self.protokoll
                                 for a in e["dateien"]}),
            "belege": list(self.belege),
            "ungeprueft": sorted(self.unverified),
            "ohne_pruefmoeglichkeit": sorted(self.ungeprueft_sonstige),
            "verifiziert": self.verified,
        }

    # -------------------------------------------------------------- Werkzeuge

    async def _list_files(self, _args) -> str:
        files = self.ws.list_files()
        return "\n".join(files) if files else "(Projekt ist leer)"

    async def _glob(self, args) -> str:
        muster = str(args.get("pattern", "")).strip()
        if not muster:
            return "FEHLER: 'pattern' fehlt."
        treffer = [rel for rel in self.ws.list_files() if _passt(rel, muster)]
        return "\n".join(treffer) if treffer else f"Keine Datei passt auf '{muster}'."

    async def _read_file(self, args) -> str:
        path = str(args.get("path", "")).strip()
        text = self.ws.read(path)
        lines = text.splitlines()
        gesamt = len(lines)

        # Abschnittsweise lesen: eine 2000-Zeilen-Datei komplett in den Kontext
        # zu holen frisst bei einem kleinen Modell das halbe Fenster.
        offset = max(1, _als_zahl(args.get("offset"), 1))
        limit = _als_zahl(args.get("limit"), 0)
        if gesamt == 0:
            # Sonst schlaegt gleich die Offset-Pruefung zu ("Zeile 1 gibt es
            # nicht") und das Modell sucht den Fehler beim Pfad statt zu
            # begreifen, dass die Datei einfach leer ist.
            return f"{path} ist leer (0 Zeilen)."
        if offset > gesamt:
            # Sonst kaeme ein leerer Rumpf mit umgedrehtem Bereich heraus
            # ("Zeilen 20-19 von 10") - das liest sich wie eine leere Datei.
            return f"{path} hat nur {gesamt} Zeile(n); Zeile {offset} gibt es nicht."
        ausschnitt = lines[offset - 1:]
        if limit > 0:
            ausschnitt = ausschnitt[:limit]

        # Zeilennummern helfen dem Modell, sich auf Stellen zu beziehen.
        body = "\n".join(f"{i:4} | {line}"
                         for i, line in enumerate(ausschnitt, offset))
        if len(ausschnitt) < gesamt:
            ende = offset + len(ausschnitt) - 1
            body += f"\n… Zeilen {offset}–{ende} von {gesamt}."
        return body

    async def _write_file(self, args) -> str:
        path = str(args.get("path", "")).strip()
        content = args.get("content")
        if content is None:
            return "FEHLER: 'content' fehlt."
        written = self.ws.write(path, str(content))
        self.written.append(written)
        return f"Geschrieben: {written} ({len(str(content))} Zeichen)"

    async def _edit_file(self, args) -> str:
        """Ersetzt einen Ausschnitt statt der ganzen Datei.

        Fuer ein kleines Modell auf CPU ist das der wichtigste Unterschied:
        eine Drei-Zeilen-Aenderung kostet Sekunden statt Minuten, und die
        haeufigste Fehlerquelle faellt weg - beim Neuschreiben verliert ein
        7B-Modell zuverlaessig Teile der Datei.
        """
        path = str(args.get("path", "")).strip()
        alt = args.get("old_text")
        neu = args.get("new_text")
        if alt is None or neu is None:
            return "FEHLER: 'old_text' und 'new_text' werden beide gebraucht."
        alt, neu = str(alt), str(neu)
        if alt == neu:
            return "FEHLER: old_text und new_text sind identisch."
        if not alt:
            return "FEHLER: 'old_text' ist leer — dafür write_file benutzen."

        # ws.read zuerst: es setzt die Groessengrenze durch. Erst danach die
        # strikte Pruefung - sonst wuerde eine riesige, ungueltig kodierte
        # Datei komplett gelesen und dekodiert, nur um anschliessend an der
        # Groesse zu scheitern.
        text = self.ws.read(path)

        # ws.read ersetzt ungueltige Bytes durch U+FFFD. Beim Zurueckschreiben
        # waeren sie dauerhaft verloren - und zwar in der ganzen Datei, nicht
        # nur an der bearbeiteten Stelle. Ein Werkzeug, das einen Ausschnitt
        # aendern soll, darf den Rest nicht stillschweigend beschaedigen.
        if "�" in text:
            try:
                self.ws.resolve(path).read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                return (f"FEHLER: {path} ist nicht UTF-8. edit_file würde beim "
                        "Zurückschreiben Zeichen zerstören.")
        anzahl = text.count(alt)
        if anzahl == 0:
            return (f"FEHLER: Der Text kommt in {path} nicht vor. "
                    "Erst read_file aufrufen und exakt übernehmen — "
                    "Einrückung und Leerzeichen zählen mit.")
        alle = bool(args.get("replace_all"))
        if anzahl > 1 and not alle:
            return (f"FEHLER: Der Text kommt {anzahl}-mal in {path} vor. "
                    "Mehr Zeilen drumherum mitgeben, damit die Stelle "
                    "eindeutig ist — oder replace_all=true setzen.")

        geaendert = text.replace(alt, neu) if alle else text.replace(alt, neu, 1)
        written = self.ws.write(path, geaendert)
        self.written.append(written)
        wieviel = f"{anzahl} Stellen" if alle and anzahl > 1 else "1 Stelle"
        return (f"{path}: {wieviel} ersetzt "
                f"({len(text)} → {len(geaendert)} Zeichen).")

    async def _delete_file(self, args) -> str:
        path = str(args.get("path", "")).strip()
        weg = self.ws.delete(path)
        self.written.append(weg)
        return f"Gelöscht: {weg}"

    async def _move_file(self, args) -> str:
        quelle = str(args.get("source", "")).strip()
        ziel = str(args.get("destination", "")).strip()
        if not quelle or not ziel:
            return "FEHLER: 'source' und 'destination' werden beide gebraucht."
        neu = self.ws.move(quelle, ziel)
        self.written.extend([quelle, neu])
        return f"Verschoben: {quelle} → {neu}"

    async def _rename_symbol(self, args) -> str:
        import refactor
        path = str(args.get("path", "")).strip()
        alt = str(args.get("old_name", "")).strip()
        neu = str(args.get("new_name", "")).strip()
        if not alt or not neu:
            return "FEHLER: 'old_name' und 'new_name' werden beide gebraucht."
        if not alt.isidentifier() or not neu.isidentifier():
            return "FEHLER: Beide Namen müssen gültige Bezeichner sein."

        code = self.ws.read(path)
        aufgabe = refactor.MechanicalTask(
            kind="rename", params={"old": alt, "new": neu},
            label=f"{alt} → {neu}")
        ergebnis = await asyncio.to_thread(refactor.apply, aufgabe, code)
        if not ergebnis.changed:
            return f"{ergebnis.summary} Nichts geändert."
        written = self.ws.write(path, ergebnis.code)
        self.written.append(written)

        # Andere Dateien werden NICHT mit umbenannt - das muss dazugesagt
        # werden, sonst haelt das Modell die Aufgabe faelschlich fuer erledigt.
        hinweis = ""
        if self.index_builder is not None:
            index = await asyncio.to_thread(self.index_builder, self.ws)
            fremde = sorted({s.path for s in index.callers(alt) if s.path != path})
            if fremde:
                hinweis = (f" ACHTUNG: '{alt}' wird noch verwendet in "
                           f"{', '.join(fremde[:5])} — dort NICHT umbenannt.")
        return f"{ergebnis.summary}{hinweis}"

    async def _undo(self, _args) -> str:
        sha = await asyncio.to_thread(self.ws.git_revert)
        if sha is None:
            return ("FEHLER: Kein Rückwärtsgang möglich — das Projekt hat noch "
                    "keine Historie.")
        self.written.clear()
        # Der Stand ist wieder der zuletzt eingecheckte. Was vorher als
        # ungeprueft offen stand, gibt es nicht mehr - es waere falsch, den
        # Abschluss weiterhin daran zu hindern.
        self.unverified.clear()
        self.ungeprueft_sonstige.clear()
        self._hashes.clear()
        return (f"Projekt auf Commit {sha} zurückgesetzt. "
                "Alle Änderungen seitdem sind verworfen.")

    async def _affected_tests(self, args) -> str:
        roh = str(args.get("paths", "")).strip()
        if roh:
            geaendert = [p.strip() for p in roh.split(",") if p.strip()]
        else:
            # Der uebliche Fall: was dieser Lauf angefasst hat. Das Modell muss
            # sich nicht merken, welche Dateien das waren.
            geaendert = sorted(set(self.written))
        if not geaendert:
            return ("Noch nichts geändert — es gibt nichts einzugrenzen. "
                    "Gib 'paths' an, wenn du trotzdem wissen willst, welche "
                    "Tests eine bestimmte Datei erreichen.")

        dateien = await asyncio.to_thread(self._alle_python)
        return await asyncio.to_thread(testimpact.bericht, dateien, geaendert)

    def _alle_python(self) -> dict[str, str]:
        """Alle lesbaren Python-Dateien des Projekts, fuer den Importgraphen."""
        dateien: dict[str, str] = {}
        for rel in self.ws.list_files():
            if not rel.endswith(".py"):
                continue
            try:
                dateien[rel] = self.ws.read(rel)
            except Exception:
                continue          # unlesbar oder binaer - fuer den Graph egal
        return dateien

    async def _check_syntax(self, args) -> str:
        path = str(args.get("path", "")).strip()
        code = self.ws.read(path)
        try:
            ast.parse(code)
        except SyntaxError as exc:
            return f"SyntaxError in {path}, Zeile {exc.lineno}: {exc.msg}"
        return f"{path}: Syntax in Ordnung."

    async def _search(self, args) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return "FEHLER: 'query' fehlt."
        try:
            pattern = (re.compile(query, re.I) if args.get("regex")
                       else re.compile(re.escape(query), re.I))
        except re.error as exc:
            return f"FEHLER: Ungültiger regulärer Ausdruck: {exc}"

        nur = str(args.get("glob", "")).strip()
        umfeld = min(5, max(0, _als_zahl(args.get("context"), 0)))
        # In einen Thread: das Durchlesen aller Dateien ist blockierend, und
        # ein vom Modell gelieferter Ausdruck kann katastrophal zurueckspringen
        # (ReDoS). Beides wuerde sonst den ganzen Webserver anhalten - auch
        # /healthz und jede andere laufende Sitzung.
        return await asyncio.to_thread(self._suchen, pattern, nur, umfeld, query)

    def _suchen(self, pattern, nur: str, umfeld: int, query: str) -> str:
        """Der blockierende Teil von _search. Laeuft in einem Thread."""
        hits: list[str] = []
        for rel in self.ws.list_files():
            if nur and not _passt(rel, nur):
                continue
            try:
                text = self.ws.read(rel)
            except Exception:
                continue
            zeilen = text.splitlines()
            for number, line in enumerate(zeilen, 1):
                if not pattern.search(line):
                    continue
                if umfeld:
                    von, bis = max(0, number - 1 - umfeld), number + umfeld
                    block = "\n".join(f"{rel}:{i}: {zeilen[i - 1][:160]}"
                                      for i in range(von + 1, min(bis, len(zeilen)) + 1))
                    hits.append(block + "\n--")
                else:
                    hits.append(f"{rel}:{number}: {line.strip()[:160]}")
                if len(hits) >= MAX_SEARCH_HITS:
                    return "\n".join(hits) + "\n… weitere Treffer ausgelassen"
        return "\n".join(hits) if hits else f"Keine Treffer für '{query}'."

    async def _symbol_info(self, args) -> str:
        name = str(args.get("name", "")).strip()
        if not name:
            return "FEHLER: 'name' fehlt."
        if self.index_builder is None:
            return "Kein Index verfügbar."
        index = await asyncio.to_thread(self.index_builder, self.ws)
        treffer = index.find(name)
        if not treffer:
            return f"Kein Symbol namens '{name}' gefunden."

        teile = []
        for sym in treffer[:3]:
            teile.append(f"{sym.kind} {sym.qualname}  —  {sym.path}:{sym.line}\n"
                         f"  Signatur: {sym.signature}"
                         + (f"\n  Doc: {sym.doc}" if sym.doc else ""))
        aufrufer = sorted({s.qualname for s in index.callers(name)})
        gerufen = sorted(index.callees(name))
        betroffen = sorted({f"{s.path}:{s.line}" for s in index.impact(name)})
        teile.append("Aufrufer: " + (", ".join(aufrufer) or "keine"))
        teile.append("Ruft auf: " + (", ".join(gerufen) or "nichts"))
        teile.append("Bei Änderung betroffen: " + (", ".join(betroffen) or "nichts"))
        return "\n".join(teile)

    async def _outline(self, _args) -> str:
        if self.index_builder is None:
            return "Kein Index verfügbar."
        # Wie in _symbol_info: der Indexaufbau liest alle Dateien und ist
        # blockierend - im Event-Loop wuerde er jede andere Sitzung anhalten.
        index = await asyncio.to_thread(self.index_builder, self.ws)
        text = index.overview()
        return text or "(keine Symbole gefunden)"

    def _project_files(self) -> dict[str, str]:
        """Alle lesbaren Textdateien des Projekts fuer die Sandbox.

        Frueher ging nur der Inhalt EINER Datei in den Container. Ein Projekt
        aus mehreren Modulen war damit nicht ausfuehrbar - der Import lief ins
        Leere. Jetzt geht der ganze Stand mit, gedeckelt, damit ein grosses
        Verzeichnis den Container-Start nicht sprengt.
        """
        dateien: dict[str, str] = {}
        summe = 0
        for rel in self.ws.list_files():
            if len(dateien) >= MAX_SANDBOX_FILES or summe >= MAX_SANDBOX_BYTES:
                break
            try:
                inhalt = self.ws.read(rel)
            except Exception:
                continue          # Binaerdatei oder unlesbar - fuer den Lauf egal
            dateien[rel] = inhalt
            summe += len(inhalt)
        return dateien

    async def _run_python(self, args) -> str:
        path = str(args.get("path", "")).strip()
        if self.run_sandbox is None:
            return "FEHLER: Keine Sandbox verfügbar."
        if not self.ws.exists(path):
            return f"FEHLER: '{path}' gibt es nicht im Projekt."

        dateien = self._project_files()
        if path not in dateien:
            # Kann passieren, wenn der Deckel vorher greift.
            dateien[path] = self.ws.read(path)

        exit_code, output = await self.run_sandbox(dateien, path)
        status = "erfolgreich" if exit_code == 0 else f"Exit-Code {exit_code}"
        mit = f" ({len(dateien)} Datei(en) im Container)" if len(dateien) > 1 else ""
        return f"Lauf {status}{mit}.\nAusgabe:\n{output or '(keine)'}"

    async def _run_command(self, args) -> str:
        """Beliebiger Befehl in derselben abgeschotteten Sandbox.

        Bewusst OHNE Shell: die Zeile wird in Argumente zerlegt, '&&' und '|'
        sind damit gewoehnliche Zeichen und keine Verkettung. Der Container
        arbeitet ausserdem auf einer Kopie - was dort geschrieben wird, wirkt
        nicht auf das echte Projekt.
        """
        befehl = str(args.get("command", "")).strip()
        if not befehl:
            return "FEHLER: 'command' fehlt."
        if self.run_sandbox is None:
            return "FEHLER: Keine Sandbox verfügbar."
        try:
            teile = shlex.split(befehl)
        except ValueError as exc:
            return f"FEHLER: Befehl nicht lesbar ({exc})."
        if not teile:
            return "FEHLER: Leerer Befehl."

        exit_code, output = await self.run_sandbox(
            self._project_files(), None, command=teile)
        status = "erfolgreich" if exit_code == 0 else f"Exit-Code {exit_code}"
        return f"Befehl {status}.\nAusgabe:\n{output or '(keine)'}"

    async def _finish(self, args) -> str:
        """Abschluss - aber nur gegen Beleg.

        Der haeufigste stille Fehlschlag eines Agenten ist nicht der Absturz,
        sondern die Erfolgsmeldung ueber ungepruefte Arbeit. Deshalb wird hier
        abgewiesen, solange seit der letzten Aenderung keine Pruefung
        bestanden wurde. Die Ablehnung geht als gewoehnliches Werkzeugergebnis
        zurueck; die Schleife laeuft weiter und das Modell kann nachliefern.

        Nach FINISH_BLOCK_LIMIT Ablehnungen darf der Lauf trotzdem enden -
        dann aber mit verified=False, und die Oberflaeche sagt das dazu. Ein
        Agent, der die Pruefung nicht hinbekommt, soll das melden duerfen; er
        soll es nur nicht als geprueft ausgeben.
        """
        summary = str(args.get("summary", "")).strip() or "Fertig."
        if self.unverified and self.finish_blocked < FINISH_BLOCK_LIMIT:
            self.finish_blocked += 1
            offen = ", ".join(sorted(self.unverified))
            rest = FINISH_BLOCK_LIMIT - self.finish_blocked
            return ("FEHLER: Abschluss abgelehnt — seit der letzten Änderung "
                    f"ist keine Prüfung bestanden worden. Ungeprüft: {offen}. "
                    "Rufe check_syntax auf diesen Dateien auf und lass den "
                    "Code mit run_python laufen, dann finish erneut. "
                    f"(Noch {rest} Ablehnung(en), danach wird der Lauf als "
                    "unbelegt beendet.)")
        self.finished = summary
        self.verified = not self.unverified
        return summary

    # ------------------------------------------------------------ Konnektoren

    async def _fetch_url(self, args) -> str:
        url = str(args.get("url", "")).strip()
        if not url:
            return "FEHLER: 'url' fehlt."
        text = await asyncio.to_thread(connectors.fetch, url)
        self.fetched.append(url)
        return text

    async def _git_push(self, args) -> str:
        ergebnis = await asyncio.to_thread(
            connectors.git_push, self.ws,
            str(args.get("branch", "")).strip(),
            str(args.get("message", "")).strip(),
            str(args.get("projekt", "")).strip())
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
