# BraunyCode Cloud v1.0.0

Cloudbasierter KI-Coding-Agent, bedienbar vom iPhone. Läuft komplett auf einem
eigenen Server — kein API-Key, keine laufenden Kosten.

Der Agent nimmt einen Auftrag entgegen, plant die Umsetzung, generiert Code und
führt ihn in einer abgeschotteten Docker-Sandbox aus. Der gesamte Ablauf wird
live per WebSocket ins Browserfenster gestreamt.

## Aufbau

```
Safari (iPhone)  ──WebSocket──▶  FastAPI  ──▶  Ollama (llama3.1:8b, lokal)
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
| `app/static/index.html` | Bedienoberfläche, für iPhone optimiert |
| `systemd/braunycode.service` | Referenz-Unit (wird vom Installer geschrieben) |
| `test_smoke.py` | Tests ohne Docker/Ollama-Abhängigkeit |
| `docs/SETUP-iPhone.md` | Schritt-für-Schritt vom leeren Oracle-Account bis zum Betrieb |

## Voraussetzungen

- Ubuntu 24.04, arm64 oder x86_64
- 4 CPU-Kerne, mindestens 8 GB RAM (empfohlen 24 GB für llama3.1:8b)
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
| `BRAUNY_MODEL` | `llama3.1:8b` | Ollama-Modell |
| `BRAUNY_SANDBOX_TIMEOUT` | `60` | Sekunden bis zum Abbruch |
| `BRAUNY_SANDBOX_MEM` | `512m` | RAM-Limit der Sandbox |
| `BRAUNY_SANDBOX_CPUS` | `1.0` | CPU-Limit der Sandbox |

Nach Änderungen: `sudo systemctl restart braunycode`

## Betrieb

```bash
sudo systemctl status braunycode      # Status
journalctl -u braunycode -f           # Logs live
curl -s localhost:8000/healthz        # Ollama + Docker prüfen
```

## Sicherheit

Das System führt vom Modell generierten Code aus. Die Absicherung:

- **Zugangs-Token** — ohne gültiges Token wird die WebSocket-Verbindung
  geschlossen. Vergleich per `hmac.compare_digest`.
- **Sandbox** — eigener Container je Lauf, `network_disabled`, non-root
  (uid 65534), alle Capabilities entfernt, `no-new-privileges`, RAM-, CPU-
  und PID-Limit, Timeout, danach zwangsweise entfernt.
- **Keine Secrets im Repo** — das Token wird auf dem Server erzeugt.

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
Log-Streaming samt Timeout, HTTP-Routen und die Token-Prüfung ab.
Docker und Ollama werden dafür nicht benötigt.

## Was das System leistet — und was nicht

`llama3.1:8b` auf 4 ARM-Kernen ohne GPU schafft grob 5–10 Token/s. Ein
Durchlauf dauert also einige Minuten. Die Code-Qualität eines 8B-Modells
reicht für abgegrenzte Aufgaben — Algorithmen, Datenverarbeitung, kleine
Skripte. Mehrdateiige Anwendungen oder Frontend-Frameworks liegen außerhalb
dessen, was hier realistisch herauskommt: der Agent erzeugt bewusst genau
eine `main.py` ohne externe Pakete.

Für stärkere Ergebnisse ein größeres Modell setzen, sofern der RAM reicht:

```bash
ollama pull qwen2.5-coder:14b
sed -i 's/^BRAUNY_MODEL=.*/BRAUNY_MODEL=qwen2.5-coder:14b/' ~/braunycode/brauny.env
sudo systemctl restart braunycode
```
