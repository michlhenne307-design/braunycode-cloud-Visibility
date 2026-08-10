---
name: bugfix
beschreibung: Fehler erst reproduzieren, dann beheben, dann nachweisen
ausloeser: fehler, bug, kaputt, absturz, abgestürzt, stürzt, exception, traceback, funktioniert nicht, behebe, repariere, stimmt, error, fehlermeldung, geht nicht, klappt nicht, schlägt fehl, wirft, warum nicht
werkzeuge: list_files, glob, read_file, search, outline, symbol_info, edit_file, write_file, check_syntax, affected_tests, run_python, run_command, undo, finish, git_push
---
Die Reihenfolge ist der ganze Punkt. Halte sie ein.

1. **Reproduzieren.** Führe den betroffenen Code mit `run_python` aus und sieh
   dir den echten Fehler an. Ohne reproduzierten Fehler weißt du nicht, ob du
   das richtige Problem löst.
2. **Verstehen.** Lies die Stelle aus dem Traceback. `symbol_info` zeigt dir,
   wer die Funktion aufruft — der Fehler kann beim Aufrufer liegen.
3. **Ursache beheben, nicht das Symptom.** Ändere gezielt mit `edit_file`,
   nicht durch Neuschreiben der ganzen Datei. Ein `try/except`, das den Fehler
   verschluckt, ist keine Lösung. Wenn du das Symptom bewusst behandelst,
   sag es ausdrücklich dazu.
4. **Nachweisen.** `check_syntax`, dann erneut ausführen. Der Fehler muss weg
   sein. Hast du es schlimmer gemacht, setz mit `undo` zurück und fang neu an.
5. **Nichts anderes kaputt machen.** Läuft noch etwas anderes im Projekt,
   führe es ebenfalls aus.
6. In `finish`: was war die Ursache, was hast du geändert, womit ist es
   belegt. Wenn du den Fehler nicht reproduzieren konntest, sag genau das —
   dann ist die Änderung eine Vermutung, keine Behebung.
