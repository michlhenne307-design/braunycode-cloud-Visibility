# BraunyCode Cloud v1.5.0

Cloudbasierter KI-Coding-Agent, bedienbar vom iPhone. Läuft komplett auf einem
eigenen Server — kein API-Key, keine laufenden Kosten.

Der Agent arbeitet an einem Projektverzeichnis mit Git-Historie. Mechanische
Änderungen erledigt er **ohne Modell** direkt über den Syntaxbaum; alles andere
plant und generiert er, führt es in einer abgeschotteten Docker-Sandbox aus und
repariert es bei Fehlern selbst. Der Ablauf wird live per WebSocket gestreamt.

## Aufbau

```
Browser (iPhone) ──WebSocket──▶  FastAPI  ──▶  Ollama (qwen2.5-coder:7b, lokal)
                                    │
                                    └────────▶  Docker-Sandbox
                                               kein Netz · 512 MB · 1 CPU
                                               non-root · 60 s Timeout
```

| Datei | Zweck |
|---|---|
| `install.sh` | Vollständige Server-Einrichtung, idempotent |
| `app/main.py` | FastAPI-Backend, WebSocket-Agent, Auth |
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
| `docs/SETUP-iPhone.md` | Schritt-für-Schritt vom leeren Oracle-Account bis zum Betrieb |

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

Danach muss noch **in der Oracle Console** eine Ingress-Regel für TCP 8000
angelegt werden — Details in [`docs/SETUP-iPhone.md`](docs/SETUP-iPhone.md).

### Konfiguration

Der Installer schreibt `~/braunycode/brauny.env` (Modus 600, nicht im Repo):

| Variable | Standard | Bedeutung |
|---|---|---|
| `BRAUNY_TOKEN` | zufällig erzeugt | Zugangs-Token für die Oberfläche |
| `BRAUNY_MODEL` | `qwen2.5-coder:7b` | Ollama-Modell |
| `BRAUNY_MAX_ATTEMPTS` | `3` | Versuche inkl. erstem Wurf; `1` schaltet die Selbstkorrektur ab |
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
  Ollama und Docker erreichbar sind.
- **Verlauf** der letzten 20 Läufe, lokal im Gerät. Antippen übernimmt den
  Auftrag erneut.
- **Stoppen** bricht einen laufenden Auftrag ab; der Server räumt den
  Container auf.
- **Offline** bleibt die Hülle nutzbar; Läufe brauchen natürlich den Server.

Zwei Einschränkungen über HTTP, in **jedem** Browser gleich: ohne HTTPS wird
**kein Service Worker** registriert — die Seite funktioniert, nur ohne
Offline-Hülle. Und der Kopieren-Knopf braucht ebenfalls einen sicheren
Kontext. Beides löst ein TLS-Proxy oder der SSH-Tunnel weiter unten.

> **Hinweis zu iPhone und iPad:** Dort benutzen *alle* Browser dieselbe
> Engine (WebKit) — Chrome, Firefox und Edge sind andere Oberflächen um
> denselben Kern. Was die Seite kann und was nicht, ist auf iOS deshalb in
> jedem Browser identisch. Nimm also ruhig den, den du magst.

## Betrieb

```bash
sudo systemctl status braunycode      # Status
journalctl -u braunycode -f           # Logs live
curl -s localhost:8000/healthz        # Ollama + Docker prüfen
```

## Zwei Wege: deterministisch und generativ

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

**Der generative Weg.** Alles andere — „baue mir X", „schreib einen
Algorithmus für Y" — geht wie bisher an das Modell.

Die Erkennung ist bewusst konservativ: Sie ist ein Mustervergleich auf gängige
Formulierungen, keine Absichtserkennung. Passt kein Muster, oder ist die
Zieldatei nicht eindeutig, nimmt der Auftrag den normalen Weg. Ein falsch
erkannter Umbau wäre schlimmer als ein verpasster.

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

## Selbstkorrektur

Der eigentliche Trick, der ein kleines Modell brauchbar macht: Scheitert der Code in
der Sandbox — Absturz, falscher Exit-Code oder ein Traceback in der Ausgabe —
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
python3 -m venv venv && venv/bin/pip install -r requirements.txt httpx
venv/bin/python test_smoke.py
```

Deckt Code-Extraktion, ollama-Antwortformate, Sandbox-Verzeichnisse,
Log-Streaming samt Timeout, HTTP-Routen, PWA-Auslieferung, Frontend-Verdrahtung,
das WebSocket-Protokoll, die komplette Selbstkorrektur-Schleife (Erfolg im
ersten Anlauf, Reparatur nach Fehler, Aufgabe nach erschöpften Versuchen,
Traceback-Erkennung bei Exit 0) sowie die Härtung ab: Auth-Bremse mit
Zeitfenster, Abweisung bei vollem Kontingent, Aufräumen verwaister Container
und das Zeitlimit für Modellaufrufe. Docker und Ollama werden dafür nicht
benötigt.

## Was das System leistet — und was nicht

Standardmodell ist **`qwen2.5-coder:7b`** — ein auf Code spezialisiertes Modell.
Gegenüber einem gleich großen Allzweckmodell trifft es bei Programmieraufgaben
deutlich besser, ist mit ~4,7 GB etwas kleiner und läuft auf CPU einen Tick
schneller. Wie alle Ollama-Modelle kostenlos.

Auf 4 ARM-Kernen ohne GPU sind grob 5–10 Token/s realistisch. Ein Durchlauf
dauert also einige Minuten — bei einer Reparatur entsprechend länger. Die
Qualität reicht für abgegrenzte Aufgaben: Algorithmen, Datenverarbeitung,
kleine Skripte. Mehrdateiige Anwendungen oder Frontend-Frameworks liegen
außerhalb dessen, was hier realistisch herauskommt: der Agent erzeugt bewusst
genau eine `main.py` ohne externe Pakete.

### Anderes Modell setzen

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
