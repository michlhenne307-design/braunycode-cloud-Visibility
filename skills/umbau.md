---
name: umbau
beschreibung: Bestehenden Code umbauen, ohne Verhalten zu ändern
ausloeser: umbau, umbauen, refactor, refactoring, aufräumen, vereinfachen, umstrukturieren, zerlegen, extrahiere
werkzeuge: list_files, glob, read_file, search, outline, symbol_info, edit_file, write_file, move_file, rename_symbol, check_syntax, affected_tests, run_python, run_command, undo, finish
---
Ein Umbau ändert die Form, nicht das Verhalten. Wenn sich das Verhalten ändert,
ist es kein Umbau — dann sag das.

1. **Erst den Aufrufgraph.** `symbol_info` sagt dir für ein Symbol genau, wer
   es aufruft und was bei einer Änderung brechen könnte. `outline` gibt den
   Überblick über alles.
2. **Dann suchen.** `search` nach dem Namen, den du anfasst — auch in
   Zeichenketten und Kommentaren. Was dir dabei nicht auffällt, bricht später.
3. **Verhalten vorher festhalten.** Führe den Code aus und merke dir die
   Ausgabe. Das ist dein Vergleichsmaßstab.
4. **In kleinen Schritten ändern.** Eine Sache pro `edit_file`, danach
   `check_syntax`, dann ausführen. Fünf Änderungen auf einmal und ein Fehler
   heißt: du weißt nicht, welche es war.
5. **Verhalten nachher vergleichen.** Gleiche Ausgabe wie vorher? Dann war es
   ein Umbau. Andere Ausgabe? Dann hast du etwas kaputt gemacht oder das
   Verhalten geändert — beides muss in `finish` stehen.

**Zum Umbenennen `rename_symbol` benutzen, nicht `edit_file`.** Es arbeitet
über den Syntaxbaum: Zeichenketten, fremde Attribute (`obj.name`) und
Schlüsselwort-Argumente bleiben unangetastet, Formatierung und Kommentare
bleiben erhalten. Eine Textersetzung erwischt zwangsläufig auch das Falsche.

Geht etwas schief, setz mit `undo` auf den letzten Commit zurück, statt am
kaputten Stand weiterzuflicken.
