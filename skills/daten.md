---
name: daten
beschreibung: Dateien und Daten einlesen, umformen und auswerten
ausloeser: csv, json, datei einlesen, daten, tabelle, auswerten, umwandeln, konvertieren, parsen, datenimport, datenexport, statistik, zählen, sortieren
werkzeuge: list_files, glob, read_file, search, write_file, edit_file, check_syntax, affected_tests, run_python, run_command, finish
---
1. Sieh dir die echten Daten an, bevor du Code schreibst: die ersten Zeilen
   mit `read_file`. Ein Trennzeichen, eine Kopfzeile oder ein Datumsformat zu
   raten, kostet später mehr als das Nachsehen jetzt.
2. Nimm `csv` und `json` aus der Standardbibliothek. Kein pandas — es liegt
   auf der Zielmaschine nicht, und für ein paar tausend Zeilen braucht es das
   auch nicht.
3. Rechne mit kaputten Zeilen. Echte Daten haben leere Felder, doppelte
   Kopfzeilen, Kommas im Text und falsche Kodierung. Entscheide bewusst:
   überspringen, melden oder abbrechen — und schreib die Entscheidung in den
   Code, nicht in den Kopf.
4. Öffne Textdateien immer mit `encoding="utf-8"` und fang `UnicodeDecodeError`
   ab. Die Vorgabe des Systems ist nicht überall dieselbe.
5. Schreib **nie** über die Eingabedatei. Ergebnis in eine neue Datei, damit
   ein Fehlversuch die Ausgangsdaten nicht zerstört.
6. Führe die Auswertung mit `run_python` wirklich aus und lass sie die Zahlen
   ausgeben, die sie gefunden hat — Zeilen gelesen, Zeilen übersprungen,
   Ergebnis. Eine Auswertung ohne Zahlen ist nicht überprüfbar.
7. Melde in `finish` die Zahlen, nicht den Eindruck: wie viele Zeilen
   verarbeitet, wie viele verworfen und warum.
