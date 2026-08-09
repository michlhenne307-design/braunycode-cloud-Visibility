---
name: bugfix
beschreibung: Fehler erst reproduzieren, dann beheben, dann nachweisen
ausloeser: fehler, bug, kaputt, absturz, exception, traceback, funktioniert, behebe, repariere, stimmt
werkzeuge: list_files, read_file, write_file, search, outline, run_python, finish
---
Die Reihenfolge ist der ganze Punkt. Halte sie ein.

1. **Reproduzieren.** Führe den betroffenen Code mit `run_python` aus und sieh
   dir den echten Fehler an. Ohne reproduzierten Fehler weißt du nicht, ob du
   das richtige Problem löst.
2. **Verstehen.** Lies die Stelle aus dem Traceback. Nutze `outline`, um zu
   sehen, wer die Funktion aufruft — der Fehler kann beim Aufrufer liegen.
3. **Ursache beheben, nicht das Symptom.** Ein `try/except`, das den Fehler
   verschluckt, ist keine Lösung. Wenn du das Symptom bewusst behandelst,
   sag es ausdrücklich dazu.
4. **Nachweisen.** Führe erneut aus. Der Fehler muss weg sein.
5. **Nichts anderes kaputt machen.** Läuft noch etwas anderes im Projekt,
   führe es ebenfalls aus.
6. In `finish`: was war die Ursache, was hast du geändert, womit ist es
   belegt. Wenn du den Fehler nicht reproduzieren konntest, sag genau das —
   dann ist die Änderung eine Vermutung, keine Behebung.
