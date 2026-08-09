# BraunyCode Cloud v1.7.0

Ein KI-Coding-Agent, der auf **deinem** Server an einem echten
Projektverzeichnis arbeitet — bedienbar vom Handy wie vom Rechner.

Er ist aufgebaut wie die großen Agenten: das Modell bekommt **Werkzeuge**
(Dateien auflisten, lesen, schreiben, suchen, ausführen) und entscheidet in
jedem Schritt selbst, was als Nächstes dran ist. **Skills** geben ihm für
wiederkehrende Aufgaben das passende Verfahren vor, **Konnektoren** lassen ihn
kontrolliert nach außen. Mechanische Änderungen erledigt er ganz **ohne
Modell** über den Syntaxbaum. Jede Änderung landet im Git-Verlauf. Der Ablauf
wird live per WebSocket gestreamt.

## Aufbau

```
Browser (Handy/Rechner) ──WebSocket──▶  FastAPI
                                          │
                                          ├─▶  Modell
                                          │    lokal (Ollama) oder
                                          │    OpenAI-kompatible API
                                          │
                                          ├─▶  Skills (Verfahrenswissen)
                                          │    tests · bugfix · umbau
                                          │    review · doku
                                          │
                                          ├─▶  Werkzeuge am Projekt
                                          │    list · glob · read · search
                                          │    outline · symbol_info
                                          │    edit · write · delete · move
                                          │    rename_symbol · check_syntax
                                          │    run_python · run_command
                                          │    undo · finish
                                          │
                                          ├─▶  Konnektoren (aus per Standard)
                                          │    fetch_url · git_push
                                          │
                                          └─▶  Docker-Sandbox
                                               kein Netz · 512 MB · 1 CPU
                                               non-root · 60 s Timeout
```

| Datei | Zweck |
|---|---|
| `install.sh` | Vollständige Server-Einrichtung, idempotent |
| `app/main.py` | FastAPI-Backend, Wegwahl, WebSocket, Auth |
| `app/agentloop.py` | Werkzeugschleife: Modell entscheidet, Werkzeug handelt |
| `app/tools.py` | Werkzeuge, Schema und Freigaben |
| `app/skills.py` | Skills laden und zur Aufgabe passend auswählen |
| `app/connectors.py` | Weg nach draußen: Webseiten lesen, Git-Push — mit Schutz |
| `app/provider.py` | Modellanbindung: lokal oder OpenAI-kompatible API |
| `skills/*.md` | Mitgelieferte Skills, editierbar, eigene dazulegbar |
| `app/sandbox.py` | Gehärtete Docker-Ausführung, Log-Streaming |
| `app/refactor.py` | Deterministische Umbauten über den Syntaxbaum, ohne Modell |
| `app/workspace.py` | Projektverzeichnis mit Pfadschutz und Git-Historie |
| `app/codeindex.py` | Symbol-Index und Aufrufgraph über das Projekt |
| `app/static/index.html` | PWA-Oberfläche, für iPhone optimiert |
| `app/static/app.css` | Design-Tokens (shadcn-Schema) und Layout |
| `app/static/app.js` | WebSocket-Client, Reiter, Verlauf, Service-Worker |
| `app/static/manifest.json` | PWA-Manifest |
| `app/static/sw.js` | Service Worker, cacht nur die statische Hülle |
| `scripts/make_icons.py` | erzeugt die Icons, reine Standardbibliothek |
| `systemd/braunycode.service` | Referenz-Unit (wird vom Installer geschrieben) |
| `test_smoke.py` | Tests ohne Docker/Ollama-Abhängigkeit |
| `deploy/hetzner-cloud-init.yaml` | Unbeaufsichtigte Einrichtung — ganz ohne SSH |
| `deploy/enable-https.sh` | Caddy davor, Zertifikat von Let's Encrypt |
| `docs/SETUP-Hetzner.md` | Schritt für Schritt, komplett vom Handy machbar |
| `docs/SETUP-iPhone.md` | Derselbe Weg über den Oracle Free Tier |

## Voraussetzungen

- Ubuntu 24.04, arm64 oder x86_64
- 4 CPU-Kerne, mindestens 8 GB RAM (empfohlen 24 GB)
- ~15 GB freier Speicher

Getestet als Zielumgebung: Oracle Cloud Free Tier, `VM.Standard.A1.Flex`,
4 OCPUs / 24 GB RAM.

## Installation

```bash
git clone https://github.com/michlhenne307-design/braunycode-cloud-Visibility.git brauny
cd brauny
bash install.sh
```

Der Installer richtet alles ein und gibt am Ende Adresse und Zugangs-Token aus.
Dauer: 10–20 Minuten, überwiegend Modell-Download.

### Ohne Rechner, nur mit dem Handy

Wenn du keinen Rechner für SSH hast: Ein Hetzner-Server kann die komplette
Installation beim ersten Start **von allein** durchziehen. Du fügst beim
Erstellen [`deploy/hetzner-cloud-init.yaml`](deploy/hetzner-cloud-init.yaml)
ein, änderst darin eine Zeile (dein Passwort) und öffnest nach ~20 Minuten die
Adresse. Kein SSH, keine Kommandozeile.

Schritt für Schritt: [`docs/SETUP-Hetzner.md`](docs/SETUP-Hetzner.md)

### Oracle Cloud Free Tier

Kostenlos, aber mit zwei bekannten Hürden — der Kartenprüfung bei der
Anmeldung und der oft ausgebuchten ARM-Kapazität. Danach muss noch eine
Ingress-Regel für TCP 8000 angelegt werden:
[`docs/SETUP-iPhone.md`](docs/SETUP-iPhone.md).

### Konfiguration

Der Installer schreibt `~/braunycode/brauny.env` (Modus 600, nicht im Repo):

| Variable | Standard | Bedeutung |
|---|---|---|
| `BRAUNY_TOKEN` | zufällig erzeugt | Zugangs-Token für die Oberfläche |
| `BRAUNY_MODEL` | `qwen2.5-coder:7b` | Modellname (bei einer API der Name des Anbieters) |
| `BRAUNY_PROVIDER` | `ollama` | `ollama` oder `openai` (jede OpenAI-kompatible API) |
| `BRAUNY_API_BASE` | leer | Basis-URL der API, z. B. `https://…/v1` |
| `BRAUNY_API_KEY` | leer | Schlüssel für die API, bleibt in der 600er-Datei |
| `BRAUNY_AGENT` | `auto` | `auto`, `tools` oder `oneshot` — siehe unten |
| `BRAUNY_MAX_STEPS` | `12` | Werkzeugrunden pro Auftrag |
| `BRAUNY_SKILLS` | `~/braunycode/skills` | Verzeichnis mit den Skill-Dateien |
| `BRAUNY_FETCH` | `0` | `1` erlaubt dem Agenten, Webseiten zu lesen |
| `BRAUNY_FETCH_TRUST_ENV` | `0` | `1` beachtet Proxy-Variablen — schwächt den Rebinding-Schutz |
| `BRAUNY_GIT_REMOTE` | leer | Repository, auf das gepusht werden darf |
| `BRAUNY_GIT_TOKEN` | leer | Token dafür, bleibt in der 600er-Datei |
| `BRAUNY_GIT_BRANCH_PREFIX` | `brauny/` | Präfix aller vom Agenten erzeugten Branches |
| `BRAUNY_MAX_ATTEMPTS` | `3` | Versuche im Einmalwurf; `1` schaltet die Selbstkorrektur ab |
| `BRAUNY_MAX_CONCURRENT` | `2` | gleichzeitig laufende Aufträge |
| `BRAUNY_ASK_TIMEOUT` | `300` | Sekunden, die ein Modellaufruf höchstens dauern darf |
| `BRAUNY_AUTH_MAX_FAILS` | `5` | Fehlversuche bis zur Sperre |
| `BRAUNY_AUTH_WINDOW` | `300` | Sekunden, über die Fehlversuche gezählt werden |
| `BRAUNY_SANDBOX_TIMEOUT` | `60` | Sekunden bis zum Abbruch |
| `BRAUNY_SANDBOX_MEM` | `512m` | RAM-Limit der Sandbox |
| `BRAUNY_SANDBOX_CPUS` | `1.0` | CPU-Limit der Sandbox |
| `BRAUNY_WORKSPACE` | `~/braunycode/workspace` | Projektverzeichnis des Agenten |

Nach Änderungen: `sudo systemctl restart braunycode`

## Als App auf dem iPhone

Die Oberfläche ist eine installierbare PWA und läuft in jedem Browser —
Chrome, Safari, Firefox, Edge, am Handy wie am Rechner. Adresse öffnen →
Teilen- bzw. Menü-Symbol → **Zum Home-Bildschirm** → Hinzufügen. Danach
startet sie im Vollbild ohne Browserleiste, mit eigenem Icon und dunkler
Statusleiste.

Was die Oberfläche kann:

- **Protokoll, Code und Ausgabe getrennt.** Der generierte Code steht in einem
  eigenen Reiter mit Kopieren-Knopf statt mitten im Log.
- **Statusanzeige** oben, gespeist aus `/healthz` — zeigt vor dem Start, ob
  Modell und Docker erreichbar sind.
- **Verlauf** der letzten 20 Läufe, lokal im Gerät. Antippen übernimmt den
  Auftrag erneut.
- **Stoppen** bricht einen laufenden Auftrag ab; der Server räumt den
  Container auf.
- **Offline** bleibt die Hülle nutzbar; Läufe brauchen natürlich den Server.

Zwei Einschränkungen über HTTP, in **jedem** Browser gleich: ohne HTTPS wird
**kein Service Worker** registriert — die Seite funktioniert, nur ohne
Offline-Hülle. Und der Kopieren-Knopf braucht ebenfalls einen sicheren
Kontext.

Beides löst [`deploy/enable-https.sh`](deploy/enable-https.sh): kostenloser
Name bei DuckDNS, Caddy davor, Zertifikat von Let's Encrypt — und weil damit
auch das Passwort verschlüsselt übertragen wird, ist es mehr als Kosmetik.

```bash
sudo bash deploy/enable-https.sh <name>.duckdns.org
```

Naheliegend wären Dienste wie `sslip.io`, die jede IP als Namen auflösen und
den DuckDNS-Schritt sparen würden. Die stehen aber **nicht** auf der Public
Suffix List: Let's Encrypt zählt dort die gesamte Domain als eine einzige mit
50 Zertifikaten pro Woche, geteilt mit allen Nutzern weltweit — das Limit ist
praktisch dauernd ausgeschöpft. `duckdns.org` steht auf der Liste, dort
bekommt jede Unterdomain ihr eigenes Kontingent.

> **Hinweis zu iPhone und iPad:** Dort benutzen *alle* Browser dieselbe
> Engine (WebKit) — Chrome, Firefox und Edge sind andere Oberflächen um
> denselben Kern. Was die Seite kann und was nicht, ist auf iOS deshalb in
> jedem Browser identisch. Nimm also ruhig den, den du magst.

## Betrieb

```bash
sudo systemctl status braunycode      # Status
journalctl -u braunycode -f           # Logs live
curl -s localhost:8000/healthz        # Modell + Docker prüfen
```

## Der Agent: Werkzeuge statt einmal raten

Ein Code-Generator rät einmal eine Datei und ist fertig. Ein **Agent** arbeitet
wie ein Mensch am Rechner: schauen, lesen, ändern, ausführen, Ergebnis prüfen,
weitermachen. Genau das macht BraunyCode seit v1.6.0 — das Modell bekommt
Werkzeuge und entscheidet in jeder Runde selbst, welches dran ist.

**Lesen und finden**

| Werkzeug | Was es tut |
|---|---|
| `list_files` | alle Dateien im Projekt auflisten |
| `glob` | Dateien über ein Muster finden (`*.py`, `src/*.md`) |
| `read_file` | Datei lesen, mit Zeilennummern — `offset`/`limit` für Ausschnitte |
| `search` | Text suchen: regulärer Ausdruck, Datei-Filter, Kontextzeilen |
| `outline` | Funktionen, Klassen und Aufrufgraph zeigen |
| `symbol_info` | Signatur, Fundstelle, Aufrufer, Aufgerufenes, Auswirkung einer Änderung |

**Ändern**

| Werkzeug | Was es tut |
|---|---|
| `edit_file` | **Textstelle ersetzen** — der Normalweg für Änderungen |
| `write_file` | Datei komplett neu schreiben (neue Dateien, vollständiger Ersatz) |
| `delete_file` | Datei löschen |
| `move_file` | verschieben oder umbenennen |
| `rename_symbol` | Symbol über den Syntaxbaum umbenennen — formaterhaltend |

**Prüfen und ausführen**

| Werkzeug | Was es tut |
|---|---|
| `check_syntax` | Syntaxprüfung ohne Ausführung — kostet nichts |
| `run_python` | Datei in der Sandbox ausführen — **das ganze Projekt kommt mit**, Importe funktionieren |
| `run_command` | beliebiger Befehl in der Sandbox, z. B. `python -m unittest` |

**Zurück und Schluss**

| Werkzeug | Was es tut |
|---|---|
| `undo` | Projekt auf den letzten Commit zurücksetzen |
| `finish` | Arbeit beenden und zusammenfassen |

### Warum `edit_file` der wichtigste davon ist

Ein 7B-Modell erzeugt auf CPU 5–10 Token pro Sekunde. Eine 200-Zeilen-Datei
neu zu schreiben dauert damit **Minuten**; drei Zeilen zu ersetzen dauert
Sekunden. Und der Neuschreib-Weg hat die unangenehmere Eigenschaft: kleine
Modelle verlieren dabei zuverlässig Teile der Datei. `edit_file` nimmt beides
weg.

Damit die Ersetzung nicht danebengreift, ist sie streng: kommt `old_text` gar
nicht vor, gibt es einen Fehler mit dem Hinweis, erst zu lesen. Kommt er
mehrfach vor, ebenfalls — mit der Zahl der Fundstellen und der Aufforderung,
mehr Kontext mitzugeben. Wer wirklich alle meint, setzt `replace_all`.

### `run_command` ist ungefährlicher als es klingt

Er läuft in derselben gehärteten Sandbox: kein Netz, non-root, alle
Capabilities entzogen, RAM- und CPU-Deckel, Zeitlimit. Und er arbeitet auf
einer **Kopie** des Projekts — was dort geschrieben wird, erreicht das echte
Verzeichnis nicht. Es gibt außerdem keine Shell: die Zeile wird in Argumente
zerlegt, `&&` und `|` sind gewöhnliche Zeichen statt Verkettung.

Alle Werkzeuge arbeiten ausschließlich innerhalb des Projektverzeichnisses;
die Pfadprüfung aus `workspace.py` lässt sich nicht umgehen. Ein Fehler wird
dem Modell **als Text zurückgegeben** statt zu werfen — so kann es reagieren,
statt dass der Lauf abbricht.

Damit sind zum ersten Mal Aufgaben über **mehrere Dateien und mehrere Runden**
möglich, statt nur eine `main.py` in einem Wurf.

Und zwar wirklich: `run_python` kopiert den **kompletten Projektstand** in den
Container und startet darin die angeforderte Datei. Schreibt der Agent
`main.py` und `helfer.py`, funktioniert der Import auch — vorher landete nur
eine einzige Datei im Container und es lief immer fest `main.py`, ein
mehrdateiiges Projekt war also gar nicht ausführbar. Unterverzeichnisse
bleiben erhalten, Pfade werden dabei gegen Ausbrüche geprüft; Dateizahl und
Gesamtgröße sind gedeckelt, damit ein großes Verzeichnis den Container-Start
nicht ausbremst.

## Skills — Verfahrenswissen als Textdatei

Ein Sprachmodell weiß, was pytest ist. Was es **nicht** weiß, ist wie hier
gearbeitet wird: erst reproduzieren, dann fixen, dann nachweisen. Genau dieses
Verfahrenswissen steckt in einer Skill-Datei — ein paar hundert Zeichen, die
vor die Aufgabe gestellt werden, wenn sie passt.

Für ein kleines Modell ist das der billigste Qualitätssprung, den es gibt:
kein Training, keine Einbettungen, kein zusätzlicher Modellaufruf. Nur Text,
der zur richtigen Zeit im Prompt steht.

| Skill | Greift bei | Kern |
|---|---|---|
| `tests` | test, testen, pytest, abdeckung | Grenzfälle testen, und die Tests wirklich ausführen |
| `bugfix` | fehler, bug, traceback, absturz | **Erst reproduzieren**, dann Ursache beheben, dann nachweisen |
| `umbau` | refactor, aufräumen, vereinfachen | Aufrufgraph zuerst, kleine Schritte, Verhalten vorher/nachher vergleichen |
| `review` | prüfe, durchsicht, schwachstellen | Melden statt ändern — **ohne Schreibrecht** |
| `doku` | readme, docstring, dokumentation | Erst lesen, dann schreiben; keine ungeprüften Beispiele |

Die Auswahl ist ein **wortweiser** Abgleich, kein Teilstring: „alphabet" löst
nicht den Auslöser „alpha" aus. Passt nichts, läuft der Auftrag ohne Skill —
ein falsch gewählter Skill lenkt das Modell in die falsche Richtung und wäre
schlimmer als gar keiner.

### Ein Skill kann Werkzeuge wegnehmen

Der `review`-Skill listet `write_file` bewusst nicht auf. Das ist keine
Ermahnung im Prompt, sondern eine echte Sperre: das Werkzeug taucht im Schema
gar nicht erst auf, und ein Aufruf würde abgewiesen. Was das Modell nicht
sieht, kann es nicht benutzen.

Umgekehrt geht es nicht — ein Skill kann **nichts** freischalten. Die Liste
wird immer mit dem geschnitten, was ohnehin erlaubt ist. `finish` bleibt immer
übrig, sonst könnte die Schleife nicht enden.

### Eigene Skills

Eine `.md` nach `~/braunycode/skills/` legen, Dienst neu starten:

```markdown
---
name: sql
beschreibung: Datenbankabfragen schreiben
ausloeser: sql, query, datenbank, tabelle
werkzeuge: read_file, write_file, run_python, finish
---
1. Erst das Schema ansehen, nie die Spaltennamen raten.
2. …
```

`werkzeuge` ist optional — fehlt die Zeile, bleiben alle erlaubt. Der
Installer überschreibt vorhandene Skill-Dateien **nicht**, deine Änderungen
überleben also eine Neuinstallation.

## Konnektoren — der kontrollierte Weg nach draußen

Alles andere in BraunyCode ist abgeschottet: die Sandbox hat kein Netz, die
Werkzeuge kommen nicht aus dem Projektverzeichnis heraus. Konnektoren öffnen
diese Grenze bewusst und eng. **Beide sind standardmäßig aus** — ohne
Konfiguration sieht das Modell sie nicht einmal.

### `git_push` — Ergebnisse vom Server holen

Das ist der Konnektor, der die Handy-Nutzung rund macht: Auftrag unterwegs
geben, und das Ergebnis liegt als Branch im Repository statt nur auf einer
VM, an die du gerade nicht drankommst.

```bash
BRAUNY_GIT_REMOTE=https://github.com/<nutzer>/<repo>.git
BRAUNY_GIT_TOKEN=<fine-grained token, Contents:write, nur dieses Repo>
```

Der Token steht **nur** in `brauny.env` (Rechte 600). Er wird über einen
`credential.helper` aus der Prozessumgebung gelesen — nie als
Kommandozeilenargument (dort läse ihn jedes `ps`) und nie in `.git/config`
(dort bliebe er auf der Platte). Sollte er trotzdem je in einer Ausgabe
auftauchen, ersetzt ihn ein letzter Filter durch `***`.

Gepusht wird ausschließlich auf `brauny/<name>` — **nie auf `main`**. Das
Präfix stammt aus `BRAUNY_GIT_BRANCH_PREFIX`; wer es leert, gibt diesen
Schutz auf.

### `fetch_url` — Webseiten lesen

```bash
BRAUNY_FETCH=1
```

Und hier die Stelle, an der ein naiver Konnektor gefährlich wird: Auf jeder
Cloud-Maschine liegt unter **`169.254.169.254`** der Metadaten-Dienst des
Anbieters — bei Oracle, AWS und Google gleichermaßen. Wer den abrufen kann,
bekommt die Zugangsdaten der Instanz. Ein Modell, das eine Adresse aus einer
Aufgabenbeschreibung übernimmt, ist genau dieser Angriffsweg.

Deshalb wird jede Adresse aufgelöst und **jede** resultierende IP geprüft,
bevor irgendetwas verbunden wird:

| Abgewiesen | Beispiel |
|---|---|
| Cloud-Metadaten | `169.254.169.254` |
| Loopback | `127.0.0.1`, `::1`, `localhost` |
| Private Netze | `10.x`, `172.16.x`, `192.168.x`, `fc00::` |
| Andere Schemata | `file://`, `gopher://` |
| Ungewöhnliche Ports | alles außer 80 und 443 |

Mehrere Adressen hinter einem Namen? Eine private reicht zum Sperren. Der
Trick `http://echte-seite.de@169.254.169.254/` greift nicht — geprüft wird der
echte Host, nicht die Benutzerinfo davor. Weiterleitungen werden **nicht**
automatisch verfolgt, weil das Ziel nach der Prüfung nach innen zeigen könnte;
stattdessen wird die Zieladresse gemeldet und beim nächsten Aufruf normal
mitgeprüft.

### Zweite Linie gegen DNS-Rebinding

Die Vorabprüfung allein reicht nicht: nach `check_url` löst die HTTP-Bibliothek
den Namen **selbst noch einmal** auf. Ändert sich die DNS-Antwort dazwischen
(oder liefert ein Round-Robin eine andere Adresse), zeigt die Verbindung
womöglich doch nach innen.

Deshalb wird nach dem Verbinden geprüft, mit **wem** tatsächlich gesprochen
wurde. Ist die Gegenstelle nicht öffentlich, wird die Antwort verworfen. Die
Verbindung selbst lässt sich so nicht verhindern — aber der Inhalt erreicht
das Modell nicht, und darauf kommt es an.

Restrisiko, ehrlich benannt: **Hinter einem Proxy entfällt diese zweite
Prüfung.** Dort ist die Gegenstelle der Proxy (oft `127.0.0.1`), die Prüfung
würde jede Anfrage verwerfen und den Konnektor unbrauchbar machen. In einem
solchen Netz ist der Schutz nur so gut wie die Vorabprüfung.

### Die Schleife ist misstrauisch

Kleine Modelle machen drei Dinge zuverlässig falsch. Alle drei werden erkannt
und benannt statt beschönigt:

- **Im Kreis drehen.** Derselbe Aufruf mit denselben Argumenten dreimal
  hintereinander → das Modell bekommt einen ausdrücklichen Hinweis.
- **Reden statt handeln.** Antwortet es zweimal nur mit Text, endet der Lauf.
  Passiert das schon in Runde 1, gilt das Modell als werkzeugunfähig und
  `auto` wechselt auf den Einmalwurf, statt elf Runden zu verschwenden.
- **Erfolg behaupten.** Meldet das Modell `finish`, ohne den Code je
  ausgeführt zu haben, steht das explizit im Ergebnis: „Der Agent hat den Code
  nicht ausgeführt. Die Zusammenfassung ist seine eigene Einschätzung, kein
  Testergebnis."

### Modelle ohne Werkzeugunterstützung

Nicht jedes kleine Modell beherrscht echte Tool-Calls. Zwei Auffangnetze:

1. Gibt das Modell stattdessen JSON aus (`{"tool": "read_file",
   "arguments": {…}}`), wird das als Aufruf gewertet.
2. Kommt gar nichts Brauchbares, wechselt `BRAUNY_AGENT=auto` auf den
   Einmalwurf — planen, schreiben, ausführen, reparieren.

`BRAUNY_AGENT=tools` erzwingt die Werkzeugschleife ohne Rückfall,
`BRAUNY_AGENT=oneshot` schaltet sie ganz ab.

## Drei Wege, vom billigsten zum teuersten

Nicht jede Änderung braucht ein Sprachmodell. Ein Teil der Alltagsarbeit ist
rein mechanisch — und dafür ist ein Modell das falsche Werkzeug: es kostet
Rechenzeit und kann danebenliegen.

**Der deterministische Weg.** Erkennt der Agent einen mechanischen Auftrag,
läuft er über den konkreten Syntaxbaum (`libcst`) statt über das Modell:

| Auftrag | Beispiel-Formulierung |
|---|---|
| Umbenennen | „benenne `calculate_total` in `sum_items` um" |
| Docstrings ergänzen | „füge Docstrings hinzu" |
| Tote Importe entfernen | „entferne ungenutzte Imports" |

Gemessen auf einer 245-Zeilen-Datei: **23–36 ms** — kein Modellaufruf, kein
Container, reproduzierbares Ergebnis. Der Umbau ist dabei präziser als ein
Modell es wäre: beim Umbenennen bleiben Zeichenketten (`"calculate_total"`),
Attributzugriffe (`obj.calculate_total()`) und Schlüsselwort-Argumente
(`f(calculate_total=1)`) unangetastet — sie gehören zu einer fremden
Schnittstelle. Formatierung und Kommentare bleiben erhalten.

**Der Werkzeugweg.** Alles andere — „baue mir X", „ändere Y in Z" — geht an
den Agenten mit seinen Werkzeugen (siehe oben).

**Der Einmalwurf.** Rückfallweg für Modelle ohne Werkzeugunterstützung:
planen, Code schreiben, ausführen, bei Fehler reparieren.

Die mechanische Erkennung ist bewusst konservativ: Sie ist ein Mustervergleich
auf gängige Formulierungen, keine Absichtserkennung. Passt kein Muster, oder
ist die Zieldatei nicht eindeutig, nimmt der Auftrag den normalen Weg. Ein
falsch erkannter Umbau wäre schlimmer als ein verpasster.

## Projektkontext statt Dokumentation

Ein Sprachmodell kennt React, Python und Design Patterns auswendig — das steht
in seinen Gewichten. Was es nicht kennen kann, ist **dieses** Projekt.

Genau das liefert `codeindex.py`, deterministisch über den Syntaxbaum:

- welche Funktionen, Klassen und Methoden es gibt, mit Signatur und Docstring
- wer wen aufruft (Aufrufgraph)
- was betroffen ist, wenn sich eine Signatur ändert

Vor jedem Modellaufruf bekommt der Agent eine kompakte Projektübersicht plus
den Quelltext der Stellen, um die es geht. Gemessen an einem Projekt mit
15 Dateien und 150 Symbolen:

| | |
|---|---|
| Index bauen | 7 ms |
| Kontext auswählen | 1 ms |
| Kontextgröße | ~820 Token |
| Ganzes Projekt roh | ~2.970 Token |

Das ist der Punkt: **nicht mehr Kontext, sondern der richtige.** Bei einem
kleinen Modell auf CPU kostet jeder überflüssige Token Rechenzeit.

Die Auswahl ist ein lexikalischer Abgleich zwischen Aufgabentext und
Bezeichnern — nachvollziehbar, ohne Einbettungen, ohne Rechenkosten. Es ist
keine Bedeutungserkennung, und das steht so auch im Code.

Der Aufrufgraph wird über Namen gebildet, nicht über aufgelöste Typen: zwei
gleichnamige Methoden in verschiedenen Klassen sind für den Index dasselbe
Ziel. Für „wer könnte betroffen sein" reicht das, eine Typanalyse ist es nicht.
Dasselbe gilt für `symbol_info`, das genau darauf aufsetzt.

### Zwei Grenzen des Werkzeugkastens

**16 Werkzeuge sind viel für ein kleines Modell.** Jedes steht im Prompt und
kostet Kontext, und je mehr Auswahl, desto eher greift ein 7B daneben. Wer das
merkt: ein Skill schränkt die Liste für seine Aufgabenart ein — genau dafür
gibt es das `werkzeuge`-Feld.

**`edit_file` arbeitet auf exaktem Text, nicht auf Zeilennummern.** Bei stark
wiederholtem Code muss das Modell genug Kontext mitgeben, damit die Stelle
eindeutig wird — sonst kommt eine Fehlermeldung statt einer falschen Änderung.
Das ist Absicht.

### Warnung bei unvollständigem Umbenennen

Ein Umbenennen ändert nur die Zieldatei. Verweist eine andere Datei noch auf
den alten Namen, meldet der Agent das ausdrücklich, statt einen sauberen
Erfolg zu behaupten:

```
ACHTUNG: 'alt_name' wird noch verwendet in andere.py:4 — dort nicht mit umbenannt.
```

## Projektverzeichnis

Der Agent arbeitet in `~/braunycode/workspace` — die Dateien bleiben liegen,
statt in einem Wegwerf-Verzeichnis zu verschwinden. Jede Änderung wird
committet, `git log` zeigt die Historie. Generierter Code landet erst dann im
Projekt, wenn er in der Sandbox nachweislich gelaufen ist.

Pfade werden gegen Ausbrüche geprüft: absolute Pfade, `..` und Symlinks, die
aus dem Projekt herauszeigen, werden abgewiesen.

## Selbstkorrektur (Einmalwurf)

Der Trick, der ein kleines Modell auf dem Rückfallweg brauchbar macht:
Scheitert der Code in der Sandbox — Absturz, falscher Exit-Code oder ein Traceback in der Ausgabe —
schickt der Agent dem Modell den gescheiterten Code samt echtem Fehler zurück
und lässt ihn neu schreiben. Das wiederholt sich, bis es läuft oder
`BRAUNY_MAX_ATTEMPTS` erreicht ist (Standard 3).

Jeder Versuch bekommt eine frische, wieder abgeschottete Sandbox. In der
Oberfläche siehst du das live: „Versuch 2/3", der Code-Reiter zeigt die
korrigierte Fassung, die Ausgabe wird für jeden Lauf neu befüllt.

Vor jedem Lauf prüft der Agent den Code per `ast.parse` auf Syntaxfehler —
den häufigsten Grund für einen Fehlschlag. Ist der Code schon syntaktisch
kaputt, startet gar kein Container: der Fehler geht direkt in die Reparatur,
was einen Container-Start spart und die Meldung präziser macht.

Ein häufiges Beispiel: das Modell schreibt `input()`, obwohl die Sandbox keine
Eingabe hat → `EOFError` → beim zweiten Versuch ersetzt es das durch einen festen
Wert und der Lauf gelingt.

## Sicherheit

Das System führt vom Modell generierten Code aus. Die Absicherung:

- **Zugangs-Token** — ohne gültiges Token wird die WebSocket-Verbindung
  geschlossen. Vergleich per `hmac.compare_digest`.
- **Sandbox** — eigener Container je Lauf, `network_disabled`, non-root
  (uid 65534), alle Capabilities entfernt, `no-new-privileges`, RAM-, CPU-
  und PID-Limit, Timeout, danach zwangsweise entfernt.
- **Keine Secrets im Repo** — das Token wird auf dem Server erzeugt.
- **Bremse gegen Token-Raten** — nach `BRAUNY_AUTH_MAX_FAILS` Fehlversuchen
  innerhalb von `BRAUNY_AUTH_WINDOW` Sekunden wird die Adresse abgewiesen.
  Ein erfolgreicher Login setzt den Zähler zurück.
- **Deckel auf gleichzeitige Läufe** — höchstens `BRAUNY_MAX_CONCURRENT`
  Aufträge gleichzeitig. Weitere werden sofort und verständlich abgewiesen,
  statt die Maschine mit Containern zu überladen.
- **Zeitlimit für Modellaufrufe** — ein hängendes Ollama blockiert die
  Verbindung nicht endlos (`BRAUNY_ASK_TIMEOUT`).
- **Aufräumen beim Start** — Sandbox-Container tragen ein Label; nach einem
  Absturz liegen gebliebene Container werden beim Dienststart entfernt.

Verbleibende Einschränkungen, die man kennen sollte:

- Die Verbindung läuft über **HTTP, nicht HTTPS**. Token und Prompts gehen
  unverschlüsselt über das Netz. Für den Betrieb über unvertraute Netze
  gehört ein Reverse-Proxy mit TLS davor oder ein SSH-Tunnel.
- Der Dienst hört auf `0.0.0.0`. Wer ihn nicht öffentlich braucht, lässt die
  Oracle-Ingress-Regel weg und tunnelt: `ssh -L 8000:localhost:8000 ubuntu@<IP>`
- Ein Container ist keine VM. Für ein Hobbyprojekt mit selbst gestellten
  Aufträgen ist die Abschottung angemessen, für nicht vertrauenswürdige
  Dritt-Eingaben nicht.

## Tests

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python test_smoke.py
```

**544 Fälle in 33 Abschnitten, ohne Docker und ohne Ollama.** Modell, Sandbox
und Werkzeugantworten werden gescriptet hineingereicht.

Abgedeckt sind unter anderem:

- **Konnektor-Schutz**: `169.254.169.254`, Loopback, alle privaten Netze,
  IPv6-Gegenstücke, fremde Schemata, ungewöhnliche Ports und der
  Benutzerinfo-Trick werden abgewiesen; gemischte Auflösung sperrt
- **Git-Push echt**: gegen ein lokales bare-Repo, inklusive zweitem Push auf
  denselben Branch; Token taucht in keiner Ausgabe auf
- **Skills**: wortweise Auswahl (kein Teilstring), `review` kann nicht
  schreiben, ein Skill kann keinen Konnektor freischalten, `finish` bleibt
- **Unbeaufsichtigte Einrichtung**: cloud-init ist gültiges YAML, der
  Passwort-Platzhalter steht wortgleich in Konfiguration *und* Abbruchprüfung,
  die Deadlock-Abschaltung ist auf beiden Seiten da, HTTPS-Fehlschlag kippt
  die Installation nicht, das Skript endet mit `exit 0`
- **Befunde einer Durchsicht**: Sandbox setzt `PYTHONPATH=/app` (sonst
  scheitert der Import bei verschachtelter Startdatei — gegen echtes Python
  nachgewiesen), ein Latin-1-Skill legt den Dienststart nicht lahm, `restrict`
  behält konfigurierte Konnektoren und macht bei Tippfehlern nicht
  handlungsunfähig, `edit_file` verweigert Nicht-UTF-8 statt es zu zerstören
- **Bearbeitungswerkzeuge**: `edit_file` lehnt fehlenden und mehrdeutigen
  Text ab statt danebenzugreifen, `rename_symbol` lässt Zeichenketten und
  fremde Attribute in Ruhe, `undo` stellt geänderte Dateien wieder her und
  entfernt neu angelegte, `run_command` wird in Argumente zerlegt statt an
  eine Shell gegeben
- **Skill-Allowlists**: jeder Skill nennt nur existierende Werkzeuge und
  erlaubt `finish`; `review` bekommt nichts Schreibendes
- **Mehrdateiige Projekte**: `run_python` liefert das importierte Modul
  wirklich mit in den Container, startet die angeforderte Datei statt fest
  `main.py`, Unterverzeichnisse bleiben erhalten, Deckel greift
- **Werkzeuge**: Pfadausbrüche (`..`, absolute Pfade, Symlinks) blockiert,
  fehlende Dateien und unbekannte Werkzeuge liefern Fehlertext statt Absturz,
  lange Ausgaben werden gekürzt
- **Werkzeugschleife**: Aufruf → Ergebnis → nächste Runde, Abbruch bei
  `finish`, Schrittgrenze hält, Wiederholungsbremse greift, Modellfehler
  beendet sauber, Verlaufskürzung trennt Aufruf und Ergebnis nicht
- **Wegwahl**: mechanisch schlägt Werkzeuge schlägt Einmalwurf; beim Wechsel
  auf den Rückfallweg kommt trotzdem genau ein `done`
- **Anbieter**: beide ollama-Antwortformate, API-Fehler nennen den Status,
  aber **nie** den Schlüssel
- **Härtung**: Auth-Bremse mit Zeitfenster, Abweisung bei vollem Kontingent,
  Aufräumen verwaister Container, Zeitlimit für Modellaufrufe

## Was das System leistet — und was nicht

**Was hier steht, ist die Maschinerie, nicht das Modell.** Werkzeuge,
Schleife, Sandbox, Index und Git-Verlauf sind gebaut und getestet. Wie gut die
Ergebnisse werden, entscheidet das Modell dahinter — und da gilt ohne
Beschönigung: ein 7B auf CPU ist nicht auf dem Niveau der großen Agenten.

Was das konkret heißt:

| | lokal, `qwen2.5-coder:7b` | starkes Modell über API |
|---|---|---|
| Kosten | 0 € | pro Token |
| Tempo | ~5–10 Token/s auf 4 ARM-Kernen, Minuten pro Runde | Sekunden |
| Werkzeugaufrufe | unzuverlässig, oft nur über den JSON-Notnagel | zuverlässig |
| Realistisch | abgegrenzte Skripte, Algorithmen, Datenverarbeitung | mehrere Dateien, mehrere Runden |

Beides läuft durch dieselbe Schleife. Der Wechsel ist eine Zeile in
`brauny.env`. Genau dafür gibt es `provider.py`: die Architektur soll nicht am
schwächsten Modell hängen.

**Gegen echtes Netz geprüft:** `fetch_url` holt eine echte Seite und gibt sie
als Text zurück, und der Aufruf des Cloud-Metadaten-Dienstes
`169.254.169.254` wird dabei tatsächlich abgewiesen — nicht nur im Test mit
gefälschter Namensauflösung.

**Nicht verifiziert** (Stand dieser Fassung): Es gab noch keinen
End-to-End-Lauf mit echtem Modell und echtem Docker. Die 544 Tests laufen
gegen gescriptete Modellantworten — sie belegen, dass die Schleife korrekt
arbeitet, nicht dass ein bestimmtes Modell gute Ergebnisse liefert. Ob die
Skills die Ergebnisse eines 7B-Modells **messbar** verbessern, ist nicht
gemessen; belegt ist nur, dass der richtige Skill ausgewählt wird und seine
Werkzeugsperre hält. `install.sh` ist syntaktisch geprüft, aber nicht auf
einem frischen Ubuntu 24.04 durchgelaufen. Die Zeitmessungen stammen von
x86_64, nicht arm64.

Ebenfalls unbelegt: die Peer-Prüfung gegen DNS-Rebinding wurde nur mit
gefälschten Antwortobjekten getestet, nicht gegen einen echten
Rebinding-Angriff.

Dass `fetch_url` Weiterleitungen **nicht** automatisch folgt, ist im Code so
umgesetzt, konnte hier aber nicht sauber nachgewiesen werden: die
Testumgebung hebt HTTP selbst auf HTTPS an und verfälscht genau diesen Fall.

Ebenso ungetestet: **cloud-init lief nie auf einem echten Server**, und
`enable-https.sh` hat **nie ein echtes Zertifikat geholt** — geprüft sind
Syntax, Caddyfile-Struktur und die Auflösbarkeit der Namen, nicht die
Ausstellung selbst.

### Stärkeres Modell über eine API

Wenn das lokale Modell nicht reicht, hängt BraunyCode an jede
OpenAI-kompatible API — der Server, die Werkzeuge, die Sandbox und die
Oberfläche bleiben dieselben:

```bash
# in ~/braunycode/brauny.env
BRAUNY_PROVIDER=openai
BRAUNY_API_BASE=https://<anbieter>/v1
BRAUNY_API_KEY=<dein-schlüssel>
BRAUNY_MODEL=<modellname des anbieters>
```

```bash
sudo systemctl restart braunycode
```

Der Schlüssel steht nur in dieser Datei (Rechte 600) und wird nie in
Fehlermeldungen, Logs oder `/healthz` ausgegeben — dafür gibt es einen Test.
`/healthz` fragt eine entfernte API bewusst **nicht** an: ein Health-Check
darf keine kostenpflichtigen Anfragen verbrauchen. Er meldet deshalb
„konfiguriert (nicht angefragt)" statt „ok".

### Anderes lokales Modell setzen

Alle Varianten sind gratis. Wenn der Arbeitsspeicher reicht, bringt die
14B-Variante nochmal spürbar bessere Ergebnisse — dafür langsamer:

```bash
ollama pull qwen2.5-coder:14b
sed -i 's/^BRAUNY_MODEL=.*/BRAUNY_MODEL=qwen2.5-coder:14b/' ~/braunycode/brauny.env
sudo systemctl restart braunycode
```

Auf schwächerer Hardware (unter 8 GB RAM) geht auch die kleine Variante:

```bash
ollama pull qwen2.5-coder:1.5b
sed -i 's/^BRAUNY_MODEL=.*/BRAUNY_MODEL=qwen2.5-coder:1.5b/' ~/braunycode/brauny.env
sudo systemctl restart braunycode
```

| Modell | Größe | RAM | Eignung |
|---|---|---|---|
| `qwen2.5-coder:1.5b` | ~1,0 GB | ~4 GB | Notlösung, einfachste Skripte |
| `qwen2.5-coder:7b` | ~4,7 GB | ~8 GB | **Standard**, guter Kompromiss |
| `qwen2.5-coder:14b` | ~9,0 GB | ~16 GB | beste Qualität, langsamer |
