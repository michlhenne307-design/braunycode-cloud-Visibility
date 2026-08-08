# BraunyCode Cloud v1.3.0

Cloudbasierter KI-Coding-Agent, bedienbar vom iPhone. Läuft komplett auf einem
eigenen Server — kein API-Key, keine laufenden Kosten.

Der Agent nimmt einen Auftrag entgegen, plant die Umsetzung, generiert Code und
führt ihn in einer abgeschotteten Docker-Sandbox aus. Schlägt der Lauf fehl,
liest der Agent den Fehler und schreibt den Code neu — bis zu dreimal. Der
gesamte Ablauf wird live per WebSocket ins Browserfenster gestreamt.

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
