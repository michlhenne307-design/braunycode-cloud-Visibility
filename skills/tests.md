---
name: tests
beschreibung: Tests schreiben und wirklich laufen lassen
ausloeser: test, tests, testen, testfall, testfälle, pytest, unittest, abdeckung
werkzeuge: list_files, glob, read_file, search, outline, edit_file, write_file, check_syntax, affected_tests, run_python, run_command, run_gates, finish, git_push
---
1. Lies zuerst den Code, den du testen sollst. Rate nie, was er tut.
2. Schreibe die Tests in eine eigene Datei `test_<modul>.py`. Nur
   Standardbibliothek — kein pytest, kein Netzwerk.
   Aufbau: eine Funktion `main()`, die jeden Fall prüft, das Ergebnis mit
   `print` meldet und am Ende die Anzahl der Fehlschläge ausgibt.
3. Teste, was schiefgehen kann, nicht nur den geraden Weg: leere Eingabe,
   Null, negative Zahlen, falscher Typ, Grenzwerte.
4. Führe die Testdatei mit `run_python` aus. Ein Test, der nicht gelaufen ist,
   ist kein Test. Für `unittest` geht auch `run_command`.
5. Schlägt ein Test fehl, entscheide bewusst: ist der Test falsch oder der
   Code? Repariere das Richtige — mit `edit_file`, nicht durch Neuschreiben
   der ganzen Datei — und führe erneut aus.
6. Melde in `finish`, wie viele Fälle laufen und welche Grenzfälle abgedeckt
   sind. Behaupte keine Abdeckung, die du nicht geprüft hast.
