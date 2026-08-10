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
from dataclasses import dataclass, field, replace

# Kategorien. Absichtlich wenige: sie sollen ueber Jahre gleich bleiben, weil
# das Fehlergedaechtnis darauf zeigt. Feiner unterscheiden kann man spaeter
# ueber 'typ', ohne alte Eintraege zu entwerten.
SYNTAX = "SYNTAX"
TYPE = "TYPE"
NAME = "NAME"
IMPORT = "IMPORT"
ASSERTION = "ASSERTION"
# Eine widerlegte Eigenschaft ist etwas anderes als eine fehlgeschlagene
# Zusicherung: sie sagt, dass der Code fuer eine ganze Klasse von Eingaben
# falsch ist, und liefert den kleinsten Fall dazu mit. Das verdient eine
# eigene Kategorie, weil das Fehlergedaechtnis darauf zeigt.
PROPERTY = "PROPERTY"
RUNTIME = "RUNTIME"
# Stilfragen und ungenutzte Namen. Getrennt von den echten Fehlern, weil sie
# einen Lauf nicht kaputt machen - sie aber in dieselbe Kategorie zu werfen
# hiesse, dem Modell eine unsortierte Importliste als Absturz zu verkaufen.
STIL = "STYLE"
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

# pytest fasst am Ende zusammen: 'FAILED datei.py::test_name - Meldung'.
# Das ist die verlaesslichste Zeile der ganzen Ausgabe, weil sie unabhaengig
# vom Formatierungsstil der Fehlerdarstellung immer gleich aussieht.
_PYTEST_FAILED = re.compile(
    r"^FAILED\s+(?P<datei>[^\s:]+)::(?P<test>[\w\[\]\.\- ]+?)"
    r"(?:\s+-\s+(?P<text>.*))?$")
# 'datei.py:9: AssertionError' - liefert die Zeilennummer nach.
_PYTEST_ORT = re.compile(r"^(?P<datei>[\w./\\-]+\.py):(?P<zeile>\d+): (?P<typ>\w+(?:Error|Exception))")

# Hypothesis nennt den kleinsten ausloesenden Fall. Zwei Schreibweisen je nach
# Version, und unter pytest steht ein 'E' davor.
_GEGENBEISPIEL = re.compile(
    r"^(?:E\s+)?(?:Failing test case|Falsifying example):\s*(?P<test>\w+)\($")
_WERKZEUGFEHLER = re.compile(r"^FEHLER: (?:(?P<typ>\w+Error|\w+Exception): )?(?P<text>.*)$")

# ruff, kurzes Format: 'datei.py:1:8: F401 [*] `os` imported but unused'.
# Bei kaputter Syntax steht dort statt eines Regelcodes 'invalid-syntax'.
# --- JavaScript und TypeScript ---------------------------------------------
#
# Ohne diese Muster war jede Ausgabe von tsc, eslint, jest oder vitest fuer
# den Agenten nur eine Textwand. Er konnte den Fehler nicht benennen, also
# auch nicht gezielt reparieren - und wich auf Python aus, wo er wenigstens
# etwas erkannte.

# tsc:  app/page.tsx(12,5): error TS2322: Type 'string' is not assignable ...
_TSC = re.compile(
    r"^(?P<datei>[^\s(][^(]*?)\((?P<zeile>\d+),(?P<spalte>\d+)\):\s+"
    r"(?P<art>error|warning)\s+(?P<code>TS\d+):\s+(?P<text>.+)$")

# eslint (stylish):  Zeile Spalte  error  Text  regel/name
# Der Dateiname steht als eigene Zeile davor.
_ESLINT_DATEI = re.compile(r"^(?P<datei>(?:/|\.{0,2}/)?[\w./\\-]+\.(?:[jt]sx?|mjs|cjs))$")
_ESLINT_ZEILE = re.compile(
    r"^\s*(?P<zeile>\d+):(?P<spalte>\d+)\s+(?P<art>error|warning)\s+"
    r"(?P<text>.+?)(?:\s\s+(?P<regel>[\w@/-]+))?$")

# jest und vitest melden DENSELBEN Fehlschlag mehrfach: einmal in der Liste
# ('✕ name (3 ms)'), einmal als Ueberschrift des Details ('● name') und
# einmal in der Zusammenfassung. Ungefiltert waeren das drei Befunde fuer
# einen kaputten Test - der Agent wuerde dreimal dieselbe Stelle reparieren.
_TESTFALL = re.compile(r"^\s*(?:●|✕|×)\s+(?P<name>.+?)\s*$")
# Dateikopf: jest schreibt 'FAIL src/x.test.js' ohne Namen, vitest haengt
# den Testnamen mit '>' an.
_FAIL_KOPF = re.compile(
    r"^\s*(?:❯\s*)?FAIL\s+(?P<datei>[\w./\\-]+\.(?:[jt]sx?|mjs|cjs))"
    r"\s*(?:[>›]\s*(?P<name>.*))?$")
# Die Laufzeit haengt nur an einer der beiden Schreibweisen - ohne sie
# abzuschneiden waeren '✕ addiert (3 ms)' und '● addiert' zwei Tests.
_DAUER = re.compile(r"\s*\((?:<\s*)?\d+(?:[.,]\d+)?\s*(?:ms|s|m)\)\s*$")
# Ortsangabe darunter:  at Object.<anonymous> (src/x.test.ts:12:5)
_JS_ORT = re.compile(
    r"(?:at\s.*?\(|^\s*at\s)(?P<datei>[\w./\\-]+\.(?:[jt]sx?|mjs|cjs)):"
    r"(?P<zeile>\d+):(?P<spalte>\d+)")

_RUFF = re.compile(
    r"^(?P<datei>[\w./\\-]+\.py):(?P<zeile>\d+):(?P<spalte>\d+): "
    # Der Doppelpunkt nach dem Code ist OPTIONAL: bei Regelcodes steht keiner
    # ('F401 [*] ...'), bei 'invalid-syntax:' schon. Ohne diese Kleinigkeit
    # faellt ausgerechnet der Syntaxfehler durch - der wichtigste Befund.
    r"(?P<code>[A-Z]+\d+|invalid-syntax)(?: \[\*\])?:? (?P<text>.+)$")

# mypy: 'datei.py:9: error: Text  [return-value]', Spalte optional.
_MYPY = re.compile(
    r"^(?P<datei>[\w./\\-]+\.py):(?P<zeile>\d+)(?::(?P<spalte>\d+))?: "
    r"(?P<schwere>error|warning|note): (?P<text>.+?)"
    r"(?:\s+\[(?P<code>[\w-]+)\])?$")

# ruff-Regelcodes, die echte Fehler sind - nicht Stil.
_RUFF_NAME = ("F821", "F822", "F823")

# eslint-Regeln, die ein echtes Namensproblem melden. Bewusst kurz: der Rest
# der ueber 200 Kernregeln ist Formatierung oder Projektvorliebe, und die
# Schwere steht ohnehin getrennt daneben.
_ESLINT_NAME = ("no-undef", "no-const-assign", "no-redeclare", "no-dupe-keys",
                "no-dupe-args", "no-dupe-class-members", "no-func-assign",
                "no-class-assign", "no-import-assign")
_ESLINT_IMPORT = ("import/no-unresolved", "import/named", "import/default",
                  "import/namespace")


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
    # Der kleinste Eingabewert, der den Fehler ausloest - falls einer bekannt
    # ist. Das ist der Unterschied zwischen "irgendwo stimmt etwas nicht" und
    # einer Reparatur mit konkretem Ziel.
    gegenbeispiel: str | None = None
    # Der Schritt, in dem der Befund entstanden ist. Erst damit laesst sich
    # spaeter sagen, WAS danach passiert ist und ihn behoben hat.
    schritt: int | None = None
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
        zeile = f"{kopf} {ort} — {self.nachricht}"
        if self.gegenbeispiel:
            zeile += f"\n    Gegenbeispiel: {self.gegenbeispiel}"
        return zeile

    def to_dict(self) -> dict:
        return {"kategorie": self.kategorie, "schwere": self.schwere,
                "typ": self.typ, "datei": self.datei, "zeile": self.zeile,
                "symbol": self.symbol, "nachricht": self.nachricht,
                "gegenbeispiel": self.gegenbeispiel, "quelle": self.quelle}


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


def _pytest(text: str, quelle: str) -> list[Finding]:
    """Die Zusammenfassungszeilen von pytest.

    'FAILED datei.py::test_name - Meldung' ist die verlaesslichste Zeile der
    ganzen Ausgabe: sie sieht unabhaengig vom Formatierungsstil der
    Fehlerdarstellung immer gleich aus. Die Zeilennummer wird aus den
    'datei.py:9: AssertionError'-Zeilen nachgereicht, sofern vorhanden.
    """
    orte: dict[str, tuple[int, str]] = {}
    for zeile in text.splitlines():
        treffer = _PYTEST_ORT.match(zeile.strip())
        if treffer:
            orte[_rel(treffer.group("datei"))] = (int(treffer.group("zeile")),
                                                  treffer.group("typ"))

    befunde: list[Finding] = []
    for zeile in text.splitlines():
        treffer = _PYTEST_FAILED.match(zeile.strip())
        if not treffer:
            continue
        datei = _rel(treffer.group("datei"))
        zeile_nr, typ = orte.get(datei, (None, "AssertionError"))
        befunde.append(Finding(
            kategorie=kategorie_fuer(typ), schwere=FEHLER, typ=typ,
            nachricht=(treffer.group("text") or "").strip() or "Test fehlgeschlagen",
            datei=datei, zeile=zeile_nr, symbol=treffer.group("test").strip(),
            quelle=quelle, roh=zeile.strip()))
    return befunde


def _gegenbeispiele(text: str) -> dict[str, str]:
    """Testname -> kleinster ausloesender Fall, wie Hypothesis ihn meldet.

    Der Block sieht so aus (unter pytest mit 'E' davor):

        Failing test case: test_nie_negativ(
            g=0,
            b=1,
        )

    Genau dieser Wert ist es, den ein SMT-Solver als Gegenbeispiel liefern
    wuerde - hier faellt er ohne Spezifikationssprache und ohne
    Uebersetzungsschritt an.
    """
    gefunden: dict[str, str] = {}
    zeilen = text.splitlines()
    for i, zeile in enumerate(zeilen):
        treffer = _GEGENBEISPIEL.match(zeile.strip())
        if not treffer:
            continue
        teile = []
        for weiter in zeilen[i + 1:]:
            roh = weiter.strip()
            if roh.startswith("E "):
                roh = roh[2:].strip()
            elif roh == "E":
                continue
            if roh.startswith(")"):
                break
            if roh:
                teile.append(roh.rstrip(","))
        if teile:
            gefunden[treffer.group("test")] = ", ".join(teile)
    return gefunden


def _anhaengen(befunde: list[Finding], gegenbeispiele: dict[str, str]) -> list[Finding]:
    """Gegenbeispiel dem passenden Befund zuordnen.

    Ueber den Testnamen, wenn er bekannt ist - sonst an den letzten Befund,
    denn Hypothesis meldet den Fall direkt zur ausloesenden Ausnahme. Ein
    Befund MIT Gegenbeispiel wird zu PROPERTY: nicht 'eine Zusicherung ist
    einmal gescheitert', sondern 'diese Eigenschaft gilt nicht'.
    """
    if not gegenbeispiele or not befunde:
        return befunde
    ergebnis = []
    offen = dict(gegenbeispiele)
    for f in befunde:
        fall = offen.pop(f.symbol, None) if f.symbol else None
        ergebnis.append(f if fall is None else replace(
            f, gegenbeispiel=fall, kategorie=PROPERTY))
    if offen and ergebnis and ergebnis[-1].gegenbeispiel is None:
        letzter = ergebnis[-1]
        ergebnis[-1] = replace(letzter, gegenbeispiel=next(iter(offen.values())),
                               kategorie=PROPERTY)
    return ergebnis


def _ruff_kategorie(code: str) -> tuple[str, str]:
    """ruff-Code -> (Kategorie, Schwere).

    Ein nicht aufgeloester Name ist ein Fehler, eine unsortierte Importliste
    nicht. Beides als ERROR zu melden wuerde das Modell dazu bringen, Stil zu
    reparieren, waehrend der eigentliche Fehler stehen bleibt.
    """
    if code == "invalid-syntax" or code.startswith("E9"):
        return SYNTAX, FEHLER
    if code in _RUFF_NAME:
        return NAME, FEHLER
    return STIL, WARNUNG


def _eslint_kategorie(regel: str) -> str:
    """eslint-Regel -> Kategorie.

    Die Kategorie sagt, WELCHER ART der Befund ist; wie schlimm er ist, steht
    getrennt davon in 'schwere'. Eine unbekannte Regel bleibt deshalb STYLE,
    auch wenn das Projekt sie auf 'error' gestellt hat - sonst wuerde eine
    Anfuehrungszeichen-Regel so aussehen wie ein undefinierter Name.
    """
    if regel in _ESLINT_IMPORT:
        return IMPORT
    # '@typescript-eslint/no-undef' traegt denselben Regelnamen wie das
    # Original, nur mit Praefix.
    if regel.rsplit("/", 1)[-1] in _ESLINT_NAME:
        return NAME
    return STIL


def _test_merken(tests: dict[str, Finding], name: str, datei: str,
                 quelle: str, roh: str) -> None:
    """Einen Testfehlschlag ablegen - je Testnamen genau einmal.

    Der erste Treffer gewinnt, eine spaeter bekannte Datei wird nachgetragen:
    vitest nennt sie im Kopf, jest erst im Stapelauszug.
    """
    vorher = tests.get(name)
    if vorher is None:
        tests[name] = Finding(
            kategorie=ASSERTION, schwere=FEHLER, typ="TestFailure",
            nachricht=f"Test fehlgeschlagen: {name}",
            datei=_rel(datei) if datei else None, symbol=name,
            quelle=quelle, roh=roh)
    elif vorher.datei is None and datei:
        tests[name] = replace(vorher, datei=_rel(datei))


def _javascript(text: str, quelle: str) -> list[Finding]:
    """Befunde aus tsc, eslint, jest und vitest.

    Aufgeteilt wie bei Python: ein Typfehler ist ein Typfehler, eine
    Stilregel ist Stil. Das Modell soll nicht anfangen, Einrueckungen zu
    reparieren, waehrend der echte Fehler stehen bleibt.
    """
    befunde: list[Finding] = []
    # Testfehlschlaege ueber den Namen zusammenfassen, weil beide Werkzeuge
    # denselben Fehlschlag mehrfach ausgeben.
    tests: dict[str, Finding] = {}
    testdatei = ""      # zuletzt genannte Testdatei, fuer die '●'-Zeilen
    lintdatei = ""      # Dateikopf von eslint
    koepfe: list[str] = []   # 'FAIL datei' ohne Testnamen

    for zeile in text.splitlines():
        blank = zeile.rstrip()
        if not blank.strip():
            continue

        treffer = _TSC.match(blank.strip())
        if treffer:
            code = treffer.group("code")
            # TS1xxx sind Parse-Fehler, alles darueber sind Typfehler.
            art = SYNTAX if code.startswith("TS1") else TYPE
            befunde.append(Finding(
                kategorie=art, typ=code,
                datei=_rel(treffer.group("datei").strip()),
                zeile=int(treffer.group("zeile")),
                nachricht=treffer.group("text").strip(),
                schwere=FEHLER if treffer.group("art") == "error" else WARNUNG,
                quelle=quelle, roh=blank.strip()))
            continue

        treffer = _FAIL_KOPF.match(blank)
        if treffer:
            testdatei = treffer.group("datei")
            name = (treffer.group("name") or "").strip()
            if name:
                _test_merken(tests, name, testdatei, quelle, blank.strip())
            else:
                # jest nennt hier nur die Datei; der Testname kommt weiter
                # unten mit '●'. Als Notnagel merken, falls er ausbleibt.
                koepfe.append(testdatei)
            continue

        treffer = _TESTFALL.match(blank)
        if treffer:
            name = _DAUER.sub("", treffer.group("name")).strip()
            if len(name) > 2:
                _test_merken(tests, name, testdatei, quelle, blank.strip())
            continue

        # eslint nennt die Datei in einer eigenen Zeile und die Befunde darunter.
        treffer = _ESLINT_DATEI.match(blank.strip())
        if treffer:
            lintdatei = treffer.group("datei")
            continue

        treffer = _ESLINT_ZEILE.match(blank)
        if treffer and lintdatei:
            regel = (treffer.group("regel") or "").strip()
            fehler = treffer.group("art") == "error"
            befunde.append(Finding(
                # Eine ungenutzte Variable ist Stil, ein undefinierter Name
                # ein echter Fehler - dieselbe Trennung wie bei ruff.
                kategorie=_eslint_kategorie(regel),
                typ=regel or "eslint",
                datei=_rel(lintdatei), zeile=int(treffer.group("zeile")),
                nachricht=treffer.group("text").strip(),
                schwere=FEHLER if fehler else WARNUNG,
                quelle=quelle, roh=blank.strip()))
            continue

    if tests:
        befunde.extend(tests.values())
    elif koepfe:
        # Nur der Dateikopf war zu sehen. 'In dieser Datei ist ein Test
        # kaputt' ist duenn, aber deutlich mehr als gar kein Befund.
        for datei in dict.fromkeys(koepfe):
            befunde.append(Finding(
                kategorie=ASSERTION, schwere=FEHLER, typ="TestFailure",
                nachricht=f"Test fehlgeschlagen in {datei}",
                datei=_rel(datei), quelle=quelle, roh=f"FAIL {datei}"))

    # Ort nachtragen, wo einer fehlt: bei jest steht die Zeile erst im
    # Stapelauszug darunter. Finding ist eingefroren, also ersetzen statt
    # zuweisen.
    ort = _JS_ORT.search(text) if befunde else None
    if ort is None:
        return befunde
    return [befund if (befund.datei and befund.zeile is not None) else replace(
        befund,
        datei=befund.datei or _rel(ort.group("datei")),
        zeile=befund.zeile if befund.zeile is not None
              else int(ort.group("zeile")))
        for befund in befunde]


def _linter(text: str, quelle: str) -> list[Finding]:
    """Befunde von ruff und mypy.

    Beide schreiben zeilenweise und maschinenlesbar - deshalb braucht es hier
    keinen Modellaufruf und keine Heuristik, nur zwei Muster.
    """
    befunde: list[Finding] = []
    for zeile in text.splitlines():
        blank = zeile.strip()
        if not blank:
            continue

        treffer = _RUFF.match(blank)
        if treffer:
            code = treffer.group("code")
            kategorie, schwere = _ruff_kategorie(code)
            befunde.append(Finding(
                kategorie=kategorie, schwere=schwere, typ=code,
                nachricht=treffer.group("text").strip(),
                datei=_rel(treffer.group("datei")),
                zeile=int(treffer.group("zeile")),
                quelle=quelle, roh=blank))
            continue

        treffer = _MYPY.match(blank)
        if treffer:
            # 'note:' sind Zusatzzeilen unter einem Fehler, keine eigenen
            # Befunde. Sie mitzuzaehlen blaehte die Liste auf, ohne etwas
            # Neues zu sagen.
            if treffer.group("schwere") == "note":
                continue
            befunde.append(Finding(
                kategorie=TYPE, schwere=(FEHLER if treffer.group("schwere") == "error"
                                         else WARNUNG),
                typ=treffer.group("code") or "mypy",
                nachricht=treffer.group("text").strip(),
                datei=_rel(treffer.group("datei")),
                zeile=int(treffer.group("zeile")),
                quelle=quelle, roh=blank))
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

    faelle = _gegenbeispiele(text)

    befunde = _tracebacks(text, quelle)
    if befunde:
        return _anhaengen(befunde, faelle)

    # pytest formatiert eigen und ohne 'Traceback'-Kopf.
    befunde = _pytest(text, quelle)
    if befunde:
        return _anhaengen(befunde, faelle)

    # Erst wenn kein Traceback da ist - sonst wuerde ein SyntaxError INNERHALB
    # eines Tracebacks (kaputtes Modul beim Import) doppelt gezaehlt.
    befunde = _kopfloser_syntaxfehler(text, quelle)
    if befunde:
        return befunde

    # tsc, eslint, jest und vitest - dieselbe Rolle wie ruff/mypy/pytest,
    # nur fuer die andere Haelfte der Welt.
    befunde = _javascript(text, quelle)
    if befunde:
        return _anhaengen(befunde, faelle)

    # ruff und mypy schreiben zeilenweise und ohne Traceback.
    befunde = _linter(text, quelle)
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
