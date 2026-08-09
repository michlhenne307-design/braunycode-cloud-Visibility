---
name: umbau
beschreibung: Bestehenden Code umbauen, ohne Verhalten zu ändern
ausloeser: umbau, umbauen, refactor, refactoring, aufräumen, vereinfachen, umstrukturieren, zerlegen, extrahiere
werkzeuge: list_files, read_file, write_file, search, outline, run_python, finish
---
Ein Umbau ändert die Form, nicht das Verhalten. Wenn sich das Verhalten ändert,
ist es kein Umbau — dann sag das.

1. **Erst den Aufrufgraph.** `outline` zeigt dir, wer was aufruft. Änderst du
   eine Signatur, musst du jeden Aufrufer mitziehen.
2. **Dann suchen.** `search` nach dem Namen, den du anfasst — auch in
   Zeichenketten und Kommentaren. Was dir dabei nicht auffällt, bricht später.
3. **Verhalten vorher festhalten.** Führe den Code aus und merke dir die
   Ausgabe. Das ist dein Vergleichsmaßstab.
4. **In kleinen Schritten ändern.** Eine Sache pro `write_file`, danach
   ausführen. Fünf Änderungen auf einmal und ein Fehler heißt: du weißt nicht,
   welche es war.
5. **Verhalten nachher vergleichen.** Gleiche Ausgabe wie vorher? Dann war es
   ein Umbau. Andere Ausgabe? Dann hast du etwas kaputt gemacht oder das
   Verhalten geändert — beides muss in `finish` stehen.

Hinweis: Reines Umbenennen, Docstrings ergänzen und tote Importe entfernen
macht BraunyCode deterministisch über den Syntaxbaum, ganz ohne dich. Wenn die
Aufgabe genau das ist, bist du hier falsch.
