"""Welche Tests eine Aenderung wirklich beruehrt.

Nach jeder Aenderung die ganze Testsuite zu fahren ist auf einer CPU-Maschine
teuer genug, dass es unterbleibt - und dann wird gar nicht geprueft. Billiger
und ehrlicher: erst die Tests, die den geaenderten Code tatsaechlich erreichen.

Grundlage ist der Importgraph, rueckwaerts gelesen. Aendert sich `pkg/kern.py`,
ist jede Datei betroffen, die ihn direkt oder ueber Zwischenschritte
importiert. Von denen sind die Testdateien das, was man laufen laesst.

Bewusst ohne Modell und ohne Ausfuehrung: nur der Syntaxbaum. Was hier
herauskommt, ist nachrechenbar.

Grenze, die man kennen muss: der Graph sieht nur statische Importe. Wer per
importlib nachlaedt, ueber Fixtures verdrahtet oder Testdaten aus einer Datei
zieht, taucht darin nicht auf. Deshalb ist die Auswahl ein *schnellerer erster
Durchgang*, kein Ersatz fuer den vollstaendigen Lauf vor dem Abschluss.
"""

from __future__ import annotations

import ast
import posixpath

# Muster, an denen eine Testdatei erkannt wird. Absichtlich die verbreiteten
# Konventionen und nicht mehr - wer eigene Namen benutzt, bekommt sonst still
# eine leere Auswahl.
def ist_test(rel: str) -> bool:
    name = posixpath.basename(rel)
    if not name.endswith(".py"):
        return False
    if name.startswith("test_") or name.endswith("_test.py"):
        return True
    return "tests/" in rel or rel.startswith("tests/")


def modulname(rel: str) -> str:
    """'pkg/mod.py' -> 'pkg.mod', 'pkg/__init__.py' -> 'pkg'."""
    ohne = rel[:-3] if rel.endswith(".py") else rel
    if ohne.endswith("/__init__"):
        ohne = ohne[: -len("/__init__")]
    return ohne.replace("/", ".")


def _paket(modul: str) -> str:
    """Das Paket, in dem ein Modul liegt - fuer relative Importe."""
    return modul.rsplit(".", 1)[0] if "." in modul else ""


def importe(quelle: str, rel: str) -> set[str]:
    """Alle Modulnamen, die diese Datei importiert.

    Relative Importe werden gegen das eigene Paket aufgeloest, sonst liefe
    `from . import kern` ins Leere und die Datei erschiene unabhaengig.
    Praefixe werden mitgenommen: `import a.b.c` haengt auch an `a` und `a.b`,
    denn deren `__init__.py` laeuft dabei mit.
    """
    try:
        baum = ast.parse(quelle)
    except SyntaxError:
        # Eine kaputte Datei darf den Graphen nicht verhindern. Sie ist ohnehin
        # gerade das Problem und wird von der Diagnostik gemeldet.
        return set()

    eigen = modulname(rel)
    treffer: set[str] = set()

    def mit_praefixen(punktiert: str) -> None:
        teile = punktiert.split(".")
        for i in range(1, len(teile) + 1):
            treffer.add(".".join(teile[:i]))

    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            for alias in knoten.names:
                mit_praefixen(alias.name)
        elif isinstance(knoten, ast.ImportFrom):
            if knoten.level:
                basis = eigen if quelle_ist_paket(rel) else _paket(eigen)
                for _ in range(knoten.level - 1):
                    basis = _paket(basis)
                wurzel = f"{basis}.{knoten.module}" if knoten.module else basis
                wurzel = wurzel.strip(".")
            else:
                wurzel = knoten.module or ""
            if not wurzel:
                continue
            mit_praefixen(wurzel)
            # `from pkg import mod` kann ein Modul meinen, nicht nur einen
            # Namen. Beide Lesarten aufnehmen; der Abgleich mit den echten
            # Projektmodulen wirft gleich weg, was es nicht gibt.
            for alias in knoten.names:
                if alias.name != "*":
                    treffer.add(f"{wurzel}.{alias.name}")
    return treffer


def quelle_ist_paket(rel: str) -> bool:
    return posixpath.basename(rel) == "__init__.py"


def _kanten(dateien: dict[str, str]) -> tuple[dict[str, set[str]], list[str]]:
    """Kanten des Importgraphen, dazu die Dateien, die nicht parsen.

    Die zweite Liste ist wichtig: eine kaputte Datei verliert ihre Kanten, und
    damit meldet die Auswahl ZU WENIGE Tests. Das still zu lassen waere genau
    der Fehler, gegen den dieses Modul gebaut ist.
    """
    python = {rel: quelle for rel, quelle in dateien.items()
              if rel.endswith(".py")}
    nach_datei = {modulname(rel): rel for rel in python}

    kanten: dict[str, set[str]] = {rel: set() for rel in python}
    unlesbar: list[str] = []
    for rel, quelle in python.items():
        try:
            ast.parse(quelle)
        except SyntaxError:
            unlesbar.append(rel)
            continue
        for modul in importe(quelle, rel):
            ziel = nach_datei.get(modul)
            if ziel is not None and ziel != rel:
                kanten[rel].add(ziel)
    return kanten, sorted(unlesbar)


def graph(dateien: dict[str, str]) -> dict[str, set[str]]:
    """Datei -> Menge der Projektdateien, die sie importiert.

    Alles, was nicht zum Projekt gehoert (Standardbibliothek, fremde Pakete),
    faellt heraus: davon geht keine Aenderung aus, die wir beobachten.
    """
    return _kanten(dateien)[0]


def betroffen(dateien: dict[str, str], geaendert) -> list[str]:
    """Alle Projektdateien, die von den geaenderten abhaengen - samt ihnen selbst.

    Rueckwaerts durch den Importgraphen, mit Merkliste gegen Importzyklen.
    """
    kanten = graph(dateien)
    rueckwaerts: dict[str, set[str]] = {rel: set() for rel in kanten}
    for rel, ziele in kanten.items():
        for ziel in ziele:
            rueckwaerts.setdefault(ziel, set()).add(rel)

    start = [rel for rel in geaendert if rel in kanten]
    gesehen: set[str] = set(start)
    arbeit = list(start)
    while arbeit:
        rel = arbeit.pop()
        for abhaengig in rueckwaerts.get(rel, ()):
            if abhaengig not in gesehen:
                gesehen.add(abhaengig)
                arbeit.append(abhaengig)
    return sorted(gesehen)


def abgedeckt(dateien: dict[str, str], startpunkte) -> set[str]:
    """Was ein Lauf ab diesen Einstiegspunkten tatsaechlich erreicht hat.

    Der Importgraph VORWAERTS - das Gegenstueck zu betroffen(). Laeuft
    'pytest test_a.py' durch, ist damit belegt, was test_a.py importiert, und
    sonst nichts. Eine gleichzeitig geaenderte Datei, die kein Test anfasst,
    bleibt ungeprueft, obwohl der Lauf gruen war.

    Genau daran haengt die Ehrlichkeit der Abschluss-Sperre: ein Beleg, der
    mehr abdeckt als der Lauf tatsaechlich beruehrt hat, ist kein Beleg,
    sondern eine Behauptung mit Testausgabe daneben.
    """
    kanten, _ = _kanten(dateien)
    start = [rel for rel in startpunkte if rel in kanten]
    gesehen: set[str] = set(start)
    arbeit = list(start)
    while arbeit:
        rel = arbeit.pop()
        for ziel in kanten.get(rel, ()):
            if ziel not in gesehen:
                gesehen.add(ziel)
                arbeit.append(ziel)
    return gesehen


def dateien_aus_befehl(befehl, bekannt) -> list[str]:
    """Die Projektdateien, die in einer Befehlszeile vorkommen.

    'python -m pytest test_a.py' -> ['test_a.py']. Nennt ein Befehl keine
    einzige Datei ('python -m pytest' ueber alles), ist die Liste leer - der
    Aufrufer muss dann entscheiden, und die richtige Entscheidung ist: der Lauf
    deckt alles ab.
    """
    bekannt = set(bekannt)
    treffer = []
    for teil in befehl if isinstance(befehl, (list, tuple)) else str(befehl).split():
        # Erst am '::' trennen, dann die Anfuehrungszeichen abziehen:
        # andersherum bliebe bei "'test_a.py'::test_f" ein Rest am Pfad haengen.
        wort = str(teil).split("::", 1)[0].strip().strip('"\'')
        if wort in bekannt and wort not in treffer:
            treffer.append(wort)
    return treffer


def betroffene_tests(dateien: dict[str, str], geaendert) -> list[str]:
    """Nur die Testdateien aus dem betroffenen Bereich."""
    return [rel for rel in betroffen(dateien, geaendert) if ist_test(rel)]


def bericht(dateien: dict[str, str], geaendert) -> str:
    """Antworttext des Werkzeugs - mit der Grenze, nicht ohne."""
    geaendert = sorted(geaendert)
    unbekannt = [rel for rel in geaendert if rel not in dateien]
    unlesbar = _kanten(dateien)[1]
    tests = betroffene_tests(dateien, geaendert)
    alle_tests = sorted(rel for rel in dateien if ist_test(rel))

    zeilen = [f"Geändert: {', '.join(geaendert) or '(nichts)'}"]
    if unbekannt:
        zeilen.append(f"Nicht im Projekt: {', '.join(unbekannt)}")
    if unlesbar:
        # Ohne diesen Hinweis wirkt eine zu kurze Liste wie ein Ergebnis.
        zeilen.append(f"ACHTUNG: {', '.join(unlesbar)} parst nicht — deren "
                      "Importe fehlen im Graphen, die Auswahl ist damit "
                      "unvollständig. Erst reparieren, dann hierauf verlassen.")
    if not alle_tests:
        zeilen.append("Das Projekt enthält keine erkennbaren Testdateien "
                      "(test_*.py, *_test.py oder unter tests/).")
        return "\n".join(zeilen)

    if tests:
        zeilen.append(f"Betroffene Tests ({len(tests)} von {len(alle_tests)}):")
        zeilen.extend(f"  {rel}" for rel in tests)
    else:
        zeilen.append("Kein Test erreicht diesen Code über einen Import. "
                      "Das ist entweder eine Lücke in der Abdeckung oder ein "
                      "Zeichen dafür, dass ein Test fehlt.")

    zeilen.append("Hinweis: Nur statische Importe. Was über importlib, "
                  "Fixtures oder Testdaten verdrahtet ist, steht hier nicht.")
    return "\n".join(zeilen)
