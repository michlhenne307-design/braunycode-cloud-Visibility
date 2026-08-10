---
name: web
beschreibung: JavaScript- und TypeScript-Projekte ändern und dabei wirklich prüfen
ausloeser: react, next, nextjs, typescript, javascript, tsx, jsx, komponente, component, frontend, oberfläche, seite, page, button, css, tailwind, npm, node, vite, webseite, website, styling, layout, nav, navigation
werkzeuge: list_files, glob, read_file, search, outline, edit_file, write_file, check_syntax, run_command, undo, finish, git_push
---
0. **Das hier ist kein Python-Projekt.** Kein `run_python`, kein `import`,
   keine `.py`-Datei. Wenn du gerade `python -c "import ..."` tippen willst,
   ist das der Moment, in dem du dich verlaufen hast — genau das ist hier
   schon passiert und hat einen ganzen Lauf gekostet.
1. Sieh dir `package.json` an. Sie sagt dir alles Wichtige: welches Framework,
   welche Skripte es gibt, welche Pakete da sind. **Erfinde keine Bibliothek,
   die nicht darin steht.**
2. Schau dir **eine** bestehende Komponente an, bevor du eine neue schreibst.
   Übernimm deren Stil: gleiche Klassen, gleiche Struktur, gleiche
   Importschreibweise. Ein Bauteil, das aussieht wie die anderen, ist besser
   als ein hübscheres, das herausfällt.
3. Ändere mit `edit_file`, nicht durch Neuschreiben der ganzen Datei. In einer
   Komponente steckt oft mehr, als in den ersten Zeilen sichtbar ist.
4. Nach **jeder** Änderung: `check_syntax` auf die Datei. Das versteht
   `.ts`, `.tsx`, `.js`, `.jsx` und `.json` und nennt Zeile und Spalte.
5. Belege, was du kannst — in dieser Reihenfolge, je nachdem was das Projekt
   hergibt:

       run_command("npx tsc --noEmit")        Typen, wenn tsconfig.json da ist
       run_command("npm test")                wenn ein test-Skript existiert
       run_command("npm run lint")            wenn ein lint-Skript existiert
       run_command("npm run build")           der stärkste Beleg, aber langsam

   Fehlt `node_modules`, schlagen die alle fehl. Das ist **kein Grund zu
   behaupten, es sei in Ordnung** — dann ist die Syntax dein Beleg und du
   sagst genau das.
6. Fasse `node_modules`, `.next`, `dist` und `build` nicht an. Das ist
   Erzeugtes, kein Quelltext.
7. Melde in `finish` ehrlich: welche Dateien geändert, welcher Befehl
   ausgeführt, was dabei herauskam — und was du nicht prüfen konntest.
