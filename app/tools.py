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
import fnmatch
import json
import re
import shlex

import connectors

MAX_OUTPUT = 4000        # Zeichen, die ein Werkzeugergebnis zurueckgeben darf
MAX_SEARCH_HITS = 25
# Deckel fuer das, was in die Sandbox kopiert wird. Ein grosses
# Projektverzeichnis wuerde sonst jeden Container-Start ausbremsen.
MAX_SANDBOX_FILES = 60
MAX_SANDBOX_BYTES = 400_000

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

        # ws.read ersetzt ungueltige Bytes durch U+FFFD. Beim Zurueckschreiben
        # waeren sie dauerhaft verloren - und zwar in der ganzen Datei, nicht
        # nur an der bearbeiteten Stelle. Ein Werkzeug, das einen Ausschnitt
        # aendern soll, darf den Rest nicht stillschweigend beschaedigen.
        roh = self.ws.resolve(path)
        if roh.is_file():
            try:
                roh.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return (f"FEHLER: {path} ist nicht UTF-8. edit_file würde beim "
                        "Zurückschreiben Zeichen zerstören.")

        text = self.ws.read(path)
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
        return (f"Projekt auf Commit {sha} zurückgesetzt. "
                "Alle Änderungen seitdem sind verworfen.")

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
        index = self.index_builder(self.ws)
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
