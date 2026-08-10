---
name: erklaeren
beschreibung: Bestehenden Code lesen und verständlich erklären, ohne ihn zu ändern
ausloeser: erkläre, erklär, erklären, verstehe, verstehen, was macht, wie funktioniert, überblick, zusammenfassung, wofür, warum, lies, analysiere, durchblick
werkzeuge: list_files, glob, read_file, search, outline, symbol_info, affected_tests, finish
---
1. Du änderst hier **nichts**. Kein `write_file`, kein `edit_file`. Wer
   erklären soll und dabei umbaut, hat die Frage nicht beantwortet.
2. Verschaff dir erst die Form: `list_files`, dann `outline` auf die größten
   Dateien. Der Aufbau erklärt oft mehr als jede einzelne Zeile.
3. Lies dann gezielt. `search` nach dem Begriff, um den es geht, `symbol_info`
   für eine bestimmte Funktion. Ganze Dateien zu lesen, nur um „alles zu
   kennen", verbrennt Zeit und bringt selten mehr Verständnis.
4. Sag, was der Code **tut**, nicht was er heißt. „`verarbeite_daten` liest
   die CSV zeilenweise und wirft Zeilen ohne Datum weg" ist eine Erklärung;
   „`verarbeite_daten` verarbeitet Daten" ist keine.
5. Nenne, was dir auffällt, aber trenne es sauber: erst was da ist, dann was
   dir daran verdächtig vorkommt. Vermische Beschreibung nicht mit Urteil.
6. Wenn du etwas nicht sicher weißt, schreib das hin. „Vermutlich für X, im
   Code steht dazu nichts" ist brauchbar; eine erfundene Begründung ist es nicht.
7. Antworte in `finish` in ganzen Sätzen, ohne Fachjargon, den die Frage nicht
   selbst benutzt hat.
