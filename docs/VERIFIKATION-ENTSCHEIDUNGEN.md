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

### Stufe 1 — Abschluss nur gegen Beleg (fertig)

`finish` wird abgewiesen, solange seit der letzten Änderung keine Prüfung
bestanden wurde. Buchführung in `Toolbox.call()`: `unverified`, `protokoll`
mit SHA-256 vorher/nachher, `belege`. Nach zwei Ablehnungen endet der Lauf
trotzdem, dann aber mit `verified=False`.

Das ist der Kern von "Evidence over Confidence" und der Grund, warum die
Schichten darunter überhaupt Sinn ergeben.


## Als Nächstes, in dieser Reihenfolge

| # | Was | Warum zuerst |
|---|-----|--------------|
| 1 | **Diagnostic Engine** — deterministischer Parser: Compiler-, Linter- und Testausgabe zu einem einheitlichen Finding (`category`, `file`, `line`, `symbol`, `severity`) | gemeinsame Fehlersprache; ohne sie reden die anderen Schichten aneinander vorbei. Bewusst ohne Modell. |
| 2 | **Diff → Abhängigkeitsgraph → betroffene Tests** | macht die Beleg-Sperre erst scharf: nicht mehr "Syntax ok", sondern "die Tests, die diesen Code berühren, laufen" |
| 3 | **Eigenschaftsbasierte Tests** (`hypothesis`, 1,4 MB) | liefert **minimierte Gegenbeispiele** ohne Spezifikationssprache |
| 4 | **Counterexample-getriebene Reparatur** | der Reparaturschritt bekommt das Gegenbeispiel als Vorgabe statt "versuch mal was" |
| 5 | **Mutationstest** (`mutmut`, 0,1 MB) | misst, ob die Tests überhaupt etwas fangen — sonst ist Grün bedeutungslos |
| 6 | **Differenzverifikation** mit Korpus (Regression, Golden Files, generiert) | statt einer festen Zahl Zufallseingaben |
| 7 | **InferencePolicy** — deterministisch fürs Planen und Extrahieren, kontrollierte Vielfalt für Kandidaten | genauer als eine pauschale Temperatur-0-Regel |
| 8 | **Bereitschafts-Gate** — Rückfrage nur bei entscheidungsrelevanter Lücke | nicht fünf Fragen aus Prinzip |
| 9 | **Patch-Vergleich** — unser Patch gegen den des Nutzers | das beste verfügbare Lernsignal |


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
