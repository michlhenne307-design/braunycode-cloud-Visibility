# Einrichtung vom iPhone aus

Vom leeren Oracle-Account bis zum laufenden Agenten. Du brauchst nur Safari
und Termius.

---

## 1. Termius installieren

Termius aus dem App Store laden. Das ist dein Terminal. Noch nichts weiter tun.

---

## 2. Server bei Oracle anlegen

Safari → `oracle.com/cloud/free` → **Start for free** → registrieren.

Zur Verifizierung wird eine Kreditkarte abgefragt. Oracle bucht eine kleine
Autorisierung (~1 €) und storniert sie wieder. Solange du im Always-Free-Rahmen
bleibst, entstehen keine Kosten.

Dann: **Compute → Instances → Create instance**

| Feld | Wert |
|---|---|
| Name | `brauny-server` |
| Image | Ubuntu 24.04 |
| Shape | Change shape → **Ampere** → `VM.Standard.A1.Flex` |
| OCPUs / RAM | 4 / 24 GB |

**SSH-Key — mach das so, nicht anders:**

Wechsle kurz zu Termius → *Keychain* → **+** → *Generate key* → Typ ED25519 →
den **öffentlichen** Schlüssel kopieren. Zurück in Oracle bei *Add SSH keys*
auf **Paste public keys** und einfügen.

> Der von Oracle angebotene Weg („Generate a key pair", `.key`-Datei
> herunterladen) funktioniert auf dem iPhone schlecht — die Datei landet in
> „Dateien" und muss umständlich in Termius importiert werden. Der Weg über
> Termius spart dir das.

**Create** klicken, zwei Minuten warten, dann die **Public IP** notieren.

### Wenn „Out of host capacity" erscheint

Das ist bei Ampere A1 im Free Tier der Normalfall, kein Fehler von dir. Die
Kapazität in den kostenlosen Regionen ist meist ausgebucht. Möglichkeiten:

- Zu anderen Tageszeiten erneut versuchen, ggf. über mehrere Tage.
- Eine andere Availability Domain wählen, falls deine Region mehrere hat.
- Auf **Pay As You Go** upgraden. Das erhöht die Chance deutlich und bleibt
  kostenlos, solange du innerhalb der Always-Free-Grenzen bleibst — die
  Ressourcen werden weiterhin als „Always Free" abgerechnet.

Die kostenlosen AMD-Instanzen (1 GB RAM) sind **keine** Alternative: darauf
läuft das Modell nicht.

---

## 3. Verbinden

Termius → **New Host**

- Hostname: die Public IP
- Username: `ubuntu`
- Key: der eben erzeugte Schlüssel

**Connect.** Die schwarze Konsole erscheint.

---

## 4. Installieren

Einmal einfügen, Enter:

```bash
git clone https://github.com/michlhenne307-design/braunycode-cloud-Visibility.git brauny && cd brauny && bash install.sh
```

> Bei einem privaten Repository fragt `git clone` nach Zugangsdaten und will
> statt des Passworts einen Personal Access Token. Am einfachsten ist es, das
> Repository auf GitHub kurz auf **Public** zu stellen — der Code enthält keine
> Geheimnisse, das Zugangs-Token wird erst auf dem Server erzeugt.

Der Installer läuft 10–20 Minuten und erledigt:

1. Wartet cloud-init und den apt-Lock ab
2. Systemupdate ohne interaktive Rückfragen
3. Docker + `python:3.11-slim` als Sandbox-Image
4. Ollama + Modell `qwen2.5-coder:7b` (~4,7 GB)
5. Python-venv (nötig wegen PEP 668 auf Ubuntu 24.04)
6. PWA-Icons, falls sie fehlen
7. Zufälliges Zugangs-Token, systemd-Dienst
8. Lokale iptables-Regel für Port 8000

Am Ende stehen **Adresse und Token** im Terminal. Token notieren.

---

## 5. Port in der Oracle Console freigeben

Diesen Schritt kann der Installer nicht übernehmen — er passiert in der
Weboberfläche, nicht auf dem Server.

**Networking → Virtual Cloud Networks →** dein VCN **→ Security Lists →
Default Security List → Add Ingress Rule**

| Feld | Wert |
|---|---|
| Source CIDR | `0.0.0.0/0` |
| IP Protocol | TCP |
| Destination Port Range | `8000` |

> Warum zweimal Firewall? Oracle filtert auf Netzwerkebene (Security List) —
> und das Ubuntu-Image bringt zusätzlich eigene iptables-Regeln mit, die alles
> außer Port 22 verwerfen. Beide müssen offen sein. Um die zweite kümmert sich
> der Installer.

---

## 6. Öffnen

Safari → `http://<DEINE-IP>:8000`

Token eintragen, Auftrag eingeben, **Agent starten**. Plan, Code und
Sandbox-Ausgabe laufen live durch.

Gute erste Aufträge:

- `Berechne die ersten 50 Primzahlen und gib sie aus`
- `Simuliere 10000 Würfelwürfe und zeige die Verteilung`
- `Sortiere eine Liste mit Mergesort und zeige jeden Schritt`

Der Dienst läuft ab jetzt dauerhaft und startet nach einem Reboot automatisch.
Termius kannst du schließen — tmux brauchst du nicht.

---

## 7. Als App auf den Home-Bildschirm

In Safari auf der geöffneten Seite: **Teilen-Symbol** (Quadrat mit Pfeil nach
oben) → **Zum Home-Bildschirm** → **Hinzufügen**.

Ab jetzt liegt BraunyCode als eigenes Icon auf dem Home-Bildschirm und startet
im Vollbild ohne Safari-Leiste. Das Token bleibt gespeichert.

> Über HTTP registriert Safari keinen Service Worker — die App läuft trotzdem,
> nur ohne Offline-Hülle. Auch der Kopieren-Knopf im Code-Reiter braucht einen
> sicheren Kontext. Wer beides will, nimmt den SSH-Tunnel unten oder setzt
> einen TLS-Proxy davor.

---

## Wenn etwas nicht geht

| Symptom | Ursache und Behebung |
|---|---|
| Safari: „Server nicht erreichbar" | Ingress-Regel aus Schritt 5 fehlt. Gegenprobe auf dem Server: `curl -s localhost:8000/healthz` — antwortet das, liegt es sicher an der Firewall. |
| Meldung „model not found“ | Modell fehlt. `ollama pull qwen2.5-coder:7b`, dann `sudo systemctl restart braunycode` |
| Meldung „permission denied … docker.sock“ | Docker-Gruppe. `sudo systemctl restart braunycode`, sonst einmal aus- und einloggen. |
| Dienst startet nicht | `journalctl -u braunycode -n 50 --no-pager` |
| „Versuch 2/3“ erscheint | Normal — der Agent hat einen Fehler erkannt und repariert den Code selbst. |
| Antworten sehr langsam | Normal. Ohne GPU rechnet das Modell auf der CPU, ein Lauf dauert Minuten. |
| `externally-managed-environment` | Es wurde `pip3 install` ohne venv benutzt. Der Installer macht das richtig — nutze ihn statt manueller Installation. |
| Statuspunkt oben ist rot | `curl -s localhost:8000/healthz` nennt Ollama bzw. Docker im Klartext. |
| Kopieren-Knopf tut nichts | Braucht HTTPS. Über den SSH-Tunnel (`http://localhost:8000`) geht es, weil localhost als sicher gilt. |
| App-Icon fehlt nach dem Hinzufügen | Seite neu laden und erneut hinzufügen; Safari holt das Manifest sonst aus dem Cache. |
| Alles neu aufsetzen | `bash install.sh` erneut ausführen. Bestehendes Token bleibt erhalten. |

### Sicherer Zugriff ohne offenen Port

Statt Schritt 5 kannst du auch per SSH tunneln. Dann ist der Dienst aus dem
Internet gar nicht erreichbar:

```bash
ssh -L 8000:localhost:8000 ubuntu@<DEINE-IP>
```

Anschließend `http://localhost:8000` auf dem Gerät öffnen, das den Tunnel hält.
Auf dem iPhone unterstützt Termius Port-Weiterleitung in den Host-Einstellungen.
