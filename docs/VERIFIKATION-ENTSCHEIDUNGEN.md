# Verifikationsschichten — was gebaut wird und was nicht

Festgehalten, damit dieselbe Diskussion nicht wiederkehrt. Die Vorlage war ein
externer Architekturvorschlag mit einer Formal-Verification-Engine (SMT,
TLA+/Apalache, KLEE, Lean/Coq). Der Gedanke dahinter ist richtig; der Umfang
passt nicht auf die Maschine, für die BraunyCode gebaut ist.

Maßstab für jede Zeile unten: **8 GB RAM, davon ~4,7 GB Modell.** Was daneben
nicht mehr hineinpasst, wird nicht gebaut, egal wie gut es klingt.


## Der Fehler, den die Vorlage macht — und den wir nicht übernehmen

Der ursprüngliche Entwurf wollte "jeden Code automatisch nach TLA+ oder Coq
übersetzen (via LLM)". Damit ist der Beweis wertlos: die Übersetzung ist
geraten, und bewiesen wird dann etwas über die geratene Spezifikation, nicht
über die Absicht. Gewissheit lässt sich nicht aus einem ungeprüften Schritt
hochziehen.

Die Korrektur des zweiten Papiers ist richtig — ein **Verification Planner**,
der pro Eigenschaft das Verfahren wählt. Nur fällt der bei unserer Codeart
klein aus, weil fast alles in denselben Zweig läuft.


## Gebaut

Stand: elf Commits auf `claude/ki-firmensystem-iphone-v7yxiu`, 726 Tests.
**Nichts davon ist je gegen ein echtes Modell oder eine echte Sandbox
gelaufen** — das gilt für jede Zeile dieses Abschnitts.

| Was | Kern |
|-----|------|
| **Abschluss nur gegen Beleg** | `finish` wird abgewiesen, solange seit der letzten Änderung keine Prüfung bestanden wurde. Buchführung in `Toolbox.call()`, SHA-256 vorher/nachher. Nach zwei Ablehnungen endet der Lauf trotzdem — dann aber mit `verified=False` und den offenen Dateien beim Namen. |
| **Reichweite eines Belegs** | Ein grüner Lauf belegt nur, was er über den Importgraphen erreicht. `pytest test_a.py` sagt nichts über eine gleichzeitig geänderte `b.py`. |
| **Diagnostik** | Traceback, kopfloser SyntaxError, pytest und die eigenen `FEHLER:`-Zeilen werden zu einem Befund mit festen Feldern. Deterministisch, ohne Modell. |
| **Gegenbeispiele** | Hypothesis' minimierter Fall wird erkannt und als `PROPERTY` geführt — der Reparatur-Input, den die Vorlage vom SMT-Solver wollte. |
| **Testauswahl** | `affected_tests` nennt über den Importgraphen rückwärts die Tests, die eine Änderung erreichen. |
| **Bereitschafts-Gate** | Nennt der Auftrag eine Datei, die es nicht gibt, endet der Lauf **vor** der ersten Änderung mit einer konkreten Rückfrage. |
| **Abtastverhalten** | Temperatur 0 für Werkzeugaufrufe. Vorher setzte kein Pfad eine — der Lauf übernahm die Vorgabe des Anbieters, meist 0.8. |
| **Fehlergedächtnis** | Fingerabdruck → was damals half. SQLite, kein Quelltext, nur belegte Läufe schreiben. |
| **Sandbox-Image** | `pytest` und `hypothesis` im Container, der ohne Netz läuft. Scheitert der Bau, wird der Verlust benannt statt verschwiegen. |

### Was dabei an eigenen Fehlern auffiel

Der Wert dieser Schicht zeigt sich weniger an dem, was sie verhindert, als an
dem, was sie beim Bauen über sich selbst herausgefunden hat:

- `finish` galt als belegt, wenn **irgendein** Lauf grün war — auch über Code,
  den er nie berührt hat.
- Ein `SyntaxError` beim Parsen hat keinen `Traceback`-Kopf und wurde
  übersehen. Ausgerechnet der häufigste Fehler nach einer Änderung.
- `affected_tests` stand in keiner Skill-Allowlist und wäre überall still
  gesperrt gewesen. Dieselbe Fehlerklasse gab es hier schon einmal.
- Die erste Fassung der Skill-Zusicherung war zu grob und mahnte `doku` an,
  das bewusst nichts ausführen darf.
- `int(SEED)` bei jedem Aufruf hätte ein Tippfehler in einer Umgebungsvariable
  zu einem Absturz mitten im Lauf gemacht.

Jeder dieser Punkte wäre still danebengegangen.


## Offen — und warum es hier nicht weitergeht

| # | Was | Was fehlt |
|---|-----|-----------|
| 1 | **Differenzverifikation** mit Korpus statt fester Zufallszahl | braucht Ausführung, also die Sandbox |
| 2 | **Mutationstest** | bewusst nicht im Sandbox-Image: `mutmut` zieht einen Terminal-UI-Stapel mit, 17 Pakete statt 6. Gehört außerhalb der Schleife. |
| 3 | **Patch-Vergleich** — unserer gegen den des Nutzers | braucht echte Korrekturen aus dem Betrieb |

Alles Weitere hängt an der Maschine. Weiterzubauen hieße, Schichten auf einen
Grund zu stapeln, der nie getragen hat — genau der Fehler, vor dem das
Ausgangspapier in seinem letzten Abschnitt warnt.


## Der wichtigste Befund

**Gegenbeispiel-getriebene Reparatur braucht keinen SMT-Solver.**

`hypothesis` schrumpft einen Fehlschlag auf den kleinsten Eingabewert, der ihn
noch auslöst. Das ist genau das Artefakt, das die Vorlage vom Solver wollte —
nur direkt am echten Code, ohne Übersetzungsschritt und damit ohne die Stelle,
an der die Vorlage rät.

    @given(st.integers(min_value=0), st.integers(min_value=0))
    def test_kontostand(guthaben, betrag):
        assert abheben(guthaben, betrag) >= 0

    Falsifying example: guthaben=0, betrag=1

Z3 (41,1 MB, MIT) bleibt sinnvoll für einzelne kritische Funktionen mit von
Hand geschriebener Bedingung. Als Ergänzung, nicht als Fundament.


## Nicht gebaut — und warum

| Vorschlag | Grund |
|-----------|-------|
| TLA+ / Apalache | braucht eine JVM. Passt nicht neben das Modell. Und wir schreiben keine verteilten Protokolle. |
| KLEE | arbeitet auf LLVM-Zwischencode, also C/C++. Unser Code ist Python und JavaScript. Dazu Pfadexplosion — "100 % aller Pfade" gibt es nicht. |
| Lean / Coq / Idris | Beweise schreibt ein Mensch. Automatisch erzeugte Beweise sind wieder geraten. |
| WASM- / MicroVM-Ausführungsebene | MicroVMs brauchen KVM; auf einem gemieteten VPS ist verschachtelte Virtualisierung meist abgeschaltet. Der gehärtete rootless Container bleibt. |
| Strategy Search mit sechs Planern (MCTS u. a.) | MCTS braucht einen Simulator mit Belohnungssignal. "Architektur wählen" lässt sich nicht ausrollen. |
| Online-DPO / RLHF | Feintuning im Betrieb braucht GPU-Speicher jenseits der Zielmaschine. Gedächtnis statt Gewichtsänderung. |
| Bazel / Nix erzwingen | vorhandene Projektkonventionen werden respektiert. |
| 10.000 erzeugte Tests je Build | mehr Tests sind nicht bessere Tests. Gemessen wird Wirksamkeit, nicht Anzahl. |

Wird ein Punkt daraus doch gebraucht, kommt er einzeln und mit der Ansage, was
er wirklich leistet — nicht als Schicht, die niemand betritt.


## Sprachregelung

Kein "100 %", kein "absolute Isolation", kein "bewiesen sicher". Eine
symbolische oder stichprobenartige Analyse ist ein **Beleglieferant**, kein
Vollständigkeitsorakel. Berichtet wird, was tatsächlich abgedeckt wurde:

    Abgedeckte Zweige: 87,2 %
    Ungelöste Zweige:  14
    Zeitüberschreitungen: 3
    Status: TEILWEISE BELEGT

Ein "vollständig verifiziert" gibt es in diesem System nicht.
