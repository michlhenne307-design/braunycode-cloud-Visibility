---
name: neubau
beschreibung: Etwas Neues von Grund auf bauen, in kleinen lauffähigen Schritten
ausloeser: baue, bau, erstelle, schreibe, entwickle, programmiere, neu, anlegen, projekt, programm, skript, werkzeug, anwendung, konsolenprogramm, verwaltung, rechner, spiel
werkzeuge: list_files, read_file, outline, write_file, edit_file, check_syntax, affected_tests, run_python, run_command, finish
---
1. Schau zuerst mit `list_files`, was schon da ist. Ein neues Programm in ein
   bestehendes Projekt zu setzen ist etwas anderes als in ein leeres.
2. Baue **klein und lauffähig**, nicht groß und fertig. Erst eine Datei mit
   einer einzigen Funktion, die wirklich läuft — dann die nächste. Ein
   vollständiges Programm, das nie ausgeführt wurde, ist kein Ergebnis.
3. Halte dich an die Standardbibliothek. Jede zusätzliche Bibliothek ist eine
   Annahme darüber, was auf der Zielmaschine liegt, und liegt meistens daneben.
4. Nach **jeder** geschriebenen Datei: `check_syntax`. Das kostet
   Millisekunden und fängt den häufigsten Fehler ab, bevor ein Container
   startet.
5. Baue einen Einstiegspunkt, den man wirklich starten kann:

       if __name__ == "__main__":
           main()

   und führe ihn mit `run_python` aus. Was nicht gestartet wurde, ist nicht
   belegt.
6. Braucht das Programm Eingaben, dann lass es auch ohne sie funktionieren —
   ein `input()` in der Sandbox bekommt nichts und blockiert. Nimm
   Kommandozeilenargumente oder feste Beispielwerte für den Testlauf.
7. Schreibe zu jeder Kernfunktion einen kleinen Test in `test_<modul>.py` und
   führe ihn aus. Ohne Testlauf ist „funktioniert" eine Behauptung.
8. Melde in `finish` genau: welche Dateien entstanden sind, was du ausgeführt
   hast und was dabei herauskam. Nenne, was noch fehlt, statt es zu verschweigen.
