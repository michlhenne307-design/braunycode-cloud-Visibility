---
name: doku
beschreibung: README und Docstrings schreiben, die stimmen
ausloeser: doku, dokumentation, readme, docstring, docstrings, kommentare, erkläre, beschreibe
werkzeuge: list_files, glob, read_file, search, outline, symbol_info, edit_file, write_file, finish
---
Dokumentation, die etwas anderes behauptet als der Code tut, ist schlimmer als
keine. Deshalb: erst lesen, dann schreiben.

1. Lies den Code, den du beschreibst — vollständig, nicht nur die Signatur.
   `symbol_info` liefert Signatur, Fundstelle und Aufrufer auf einen Schlag.
   Ändere einzelne Docstrings mit `edit_file`, nicht durch Neuschreiben der
   ganzen Datei.
2. Ein Docstring sagt, **warum** es die Funktion gibt und was der Aufrufer
   wissen muss: Randbedingungen, Fehlerfälle, Einheiten. Er wiederholt nicht
   den Funktionsnamen in Prosa.
   Schlecht: „Berechnet die Summe." bei `berechne_summe`.
   Gut: „Summiert die Beträge. Leere Liste ergibt 0, negative Beträge sind
   erlaubt."
3. Eine README beantwortet in dieser Reihenfolge: Was ist das? Wie starte ich
   es? Was kann es nicht?
4. Schreibe kein Beispiel hin, das du nicht geprüft hast. Wenn du ein Beispiel
   angibst, muss es genau so laufen.
5. Grenzen gehören dazu. Was das Programm nicht kann, ist für den Leser oft
   wichtiger als was es kann.

Hinweis: Fehlende Docstrings maschinell ergänzen kann BraunyCode
deterministisch über den Syntaxbaum. Hier geht es um Inhalt, nicht um Form.
