#!/usr/bin/env bash
#
# BraunyCode Cloud - Installer fuer Ubuntu 24.04 auf x86_64 oder arm64.
#
#   bash install.sh
#
# Idempotent: kann gefahrlos mehrfach laufen.
set -euo pipefail

BRAUNY_PORT="${BRAUNY_PORT:-8000}"
SANDBOX_IMAGE="${BRAUNY_SANDBOX_IMAGE:-python:3.11-slim}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRAUNY_USER="${BRAUNY_USER:-brauny}"

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!!  %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mXX  %s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 0. Benutzer
# Die grossen Anbieter geben unterschiedliche Erstzugaenge: Oracle und AWS
# legen einen unprivilegierten Benutzer an ('ubuntu'), Contabo und Hetzner
# liefern nur root. Der Agent darf aber nicht als root laufen - er fuehrt
# fremden Code aus, und ein Fehlgriff waere dann ein Fehlgriff am ganzen
# System. Statt den Lauf abzubrechen legen wir den Benutzer selbst an und
# starten uns als dieser neu.
if [ "$(id -u)" -eq 0 ]; then
  # Ohne diese Pruefung wuerde BRAUNY_USER=root sich selbst endlos neu starten.
  [ "$BRAUNY_USER" != "root" ] \
    || die "BRAUNY_USER darf nicht 'root' sein - der Agent laeuft unprivilegiert."
  step "Als root gestartet - unprivilegierten Benutzer '$BRAUNY_USER' einrichten"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq sudo rsync
  if ! id -u "$BRAUNY_USER" >/dev/null 2>&1; then
    useradd -m -s /bin/bash "$BRAUNY_USER"
  fi
  usermod -aG sudo "$BRAUNY_USER"
  # Ohne passwortloses sudo bliebe dieses Skript bei der ersten Paketinstallation
  # auf eine Passwortabfrage stehen - und das Konto hat gar kein Passwort. Das
  # ist dieselbe Regel, die Ubuntu-Cloud-Images fuer 'ubuntu' mitbringen.
  printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$BRAUNY_USER" > "/etc/sudoers.d/90-$BRAUNY_USER"
  chmod 0440 "/etc/sudoers.d/90-$BRAUNY_USER"
  visudo -cf "/etc/sudoers.d/90-$BRAUNY_USER" >/dev/null \
    || die "sudoers-Datei fehlerhaft - abgebrochen, bevor sudo kaputtgeht."

  # Den SSH-Zugang des Erstbenutzers uebernehmen, damit man nach der
  # Installation ohne root weiterarbeiten kann.
  if [ -f /root/.ssh/authorized_keys ]; then
    install -d -m 700 -o "$BRAUNY_USER" -g "$BRAUNY_USER" "/home/$BRAUNY_USER/.ssh"
    install -m 600 -o "$BRAUNY_USER" -g "$BRAUNY_USER" \
      /root/.ssh/authorized_keys "/home/$BRAUNY_USER/.ssh/authorized_keys"
  fi

  ZIEL="/home/$BRAUNY_USER/braunycode-src"
  rsync -a --delete "$SRC_DIR/" "$ZIEL/"
  chown -R "$BRAUNY_USER:$BRAUNY_USER" "$ZIEL"
  # sudo raeumt die Umgebung ab. Was der Aufrufer bewusst gesetzt hat, muss
  # deshalb ausdruecklich mitgegeben werden - sonst laeuft der zweite Durchgang
  # mit anderen Vorgaben als der erste, und niemand sieht warum.
  FORWARD=()
  for v in BRAUNY_MODEL BRAUNY_HOME BRAUNY_PORT BRAUNY_TOKEN \
           BRAUNY_SANDBOX_IMAGE BRAUNY_SKIP_CLOUDINIT_WAIT; do
    [ -n "${!v:-}" ] && FORWARD+=("$v=${!v}")
  done
  step "Neustart als '$BRAUNY_USER'"
  exec sudo -u "$BRAUNY_USER" -H env ${FORWARD[@]+"${FORWARD[@]}"} \
       bash "$ZIEL/install.sh" "$@"
fi

command -v sudo >/dev/null || die "sudo wird benoetigt."
BRAUNY_HOME="${BRAUNY_HOME:-$HOME/braunycode}"

# ---------------------------------------------------------------- 0b. Modell
# Ein zu grosses Modell laedt minutenlang und faellt dann beim ersten Aufruf
# in den Swap oder wird vom OOM-Killer beendet. Deshalb wird die Vorgabe am
# tatsaechlich vorhandenen Arbeitsspeicher gewaehlt statt geraten. Die Zahlen
# sind die Downloadgroessen laut ollama, nicht der Bedarf zur Laufzeit - der
# liegt darueber, daher der Abstand zur jeweiligen Schwelle.
if [ -z "${BRAUNY_MODEL:-}" ]; then
  RAM_KB="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
  if   [ "$RAM_KB" -ge 22000000 ]; then BRAUNY_MODEL="qwen3-coder:30b"    # 19 GB
  elif [ "$RAM_KB" -ge 12000000 ]; then BRAUNY_MODEL="qwen2.5-coder:14b"  #  9 GB
  else                                  BRAUNY_MODEL="qwen2.5-coder:7b"   # 4,7 GB
  fi
  step "Modell nach Arbeitsspeicher gewaehlt: $BRAUNY_MODEL ($((RAM_KB/1024)) MB RAM)"
fi

# ---------------------------------------------------------------- 1. apt
step "Warte auf cloud-init und den apt-Lock"
# Frisch gebootete Cloud-Images laufen minutenlang mit unattended-upgrades.
# Ohne dieses Warten scheitert jedes apt mit "Could not get lock".
#
# ACHTUNG: Laeuft dieses Skript SELBST aus cloud-init heraus (unbeaufsichtigte
# Einrichtung), wartet 'status --wait' auf das Ende von cloud-init - und
# cloud-init wartet auf uns. Das ist ein Deadlock, deshalb der Schalter.
if [ "${BRAUNY_SKIP_CLOUDINIT_WAIT:-0}" != "1" ]; then
  sudo cloud-init status --wait >/dev/null 2>&1 || true
fi
for _ in $(seq 1 120); do
  sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || break
  sleep 5
done

step "System aktualisieren"
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
sudo -E apt-get update -qq
sudo -E apt-get -y -qq -o Dpkg::Options::=--force-confold upgrade
sudo -E apt-get install -y -qq python3-venv python3-pip curl git tmux ca-certificates

# ---------------------------------------------------------------- 2. Docker
if command -v docker >/dev/null 2>&1; then
  step "Docker ist bereits installiert"
else
  step "Docker installieren"
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  sudo sh /tmp/get-docker.sh
  rm -f /tmp/get-docker.sh
fi
sudo usermod -aG docker "$USER"
sudo systemctl enable --now docker

# Der Sandbox-Container laeuft ohne Netzwerk - was nicht im Image liegt, kann
# er nicht nachinstallieren. Ohne Testlaeufer bliebe dem Agenten als Beleg nur
# "die Datei parst", und das ist der schwaechste aller Belege.
#
# Scheitert der Bau (kein Netz zu PyPI, Spiegel nicht erreichbar), wird das
# Basisimage benutzt und der Verlust ausdruecklich benannt. Die Einrichtung
# daran scheitern zu lassen waere unverhaeltnismaessig - der Agent
# funktioniert, er kann nur weniger belegen.
if [ -z "${BRAUNY_SANDBOX_IMAGE:-}" ] && [ -f "$SRC_DIR/deploy/sandbox.Dockerfile" ]; then
  step "Sandbox-Image bauen (mit pytest und hypothesis)"
  if sudo docker build -q -t braunycode-sandbox:1 \
       -f "$SRC_DIR/deploy/sandbox.Dockerfile" "$SRC_DIR" >/dev/null; then
    SANDBOX_IMAGE="braunycode-sandbox:1"
  else
    warn "Bau des Sandbox-Images fehlgeschlagen — es wird $SANDBOX_IMAGE"
    warn "benutzt. Der Agent kann dann keine Tests in der Sandbox ausfuehren"
    warn "und belegt Aenderungen nur ueber Syntax und einfache Laeufe."
    sudo docker pull -q "$SANDBOX_IMAGE"
  fi
else
  step "Sandbox-Image vorladen ($SANDBOX_IMAGE)"
  sudo docker pull -q "$SANDBOX_IMAGE"
fi

# ---------------------------------------------------------------- 3. Ollama
if command -v ollama >/dev/null 2>&1; then
  step "Ollama ist bereits installiert"
else
  step "Ollama installieren"
  curl -fsSL https://ollama.com/install.sh | sh
fi
sudo systemctl enable --now ollama 2>/dev/null || true

step "Warte auf den Ollama-Dienst"
for _ in $(seq 1 60); do
  curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1 \
  || die "Ollama antwortet nicht auf Port 11434 (journalctl -u ollama)."

# Auslagerungsdatei. Ein 30B-Modell belegt 19 GB auf einer 24-GB-Maschine -
# daneben liegen noch System, Docker und die Anwendung. Ohne Swap beendet der
# OOM-Killer im Zweifel ollama mitten in einer Antwort, und der Lauf bricht
# ohne erkennbaren Grund ab. Swap ist hier kein Ersatz fuer RAM, sondern ein
# Puffer gegen genau diesen Abbruch.
if [ "$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo)" -lt 1000000 ]; then
  step "Auslagerungsdatei (8 GB) anlegen"
  if sudo fallocate -l 8G /swapfile 2>/dev/null || \
     sudo dd if=/dev/zero of=/swapfile bs=1M count=8192 status=none; then
    sudo chmod 600 /swapfile
    sudo mkswap -q /swapfile >/dev/null
    sudo swapon /swapfile
    grep -q '^/swapfile' /etc/fstab \
      || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  else
    warn "Auslagerungsdatei konnte nicht angelegt werden - weiter ohne."
  fi
fi

step "Modell laden: $BRAUNY_MODEL (mehrere GB, das dauert)"
ollama pull "$BRAUNY_MODEL"

# ---------------------------------------------------------------- 4. App
step "Anwendung nach $BRAUNY_HOME kopieren"
mkdir -p "$BRAUNY_HOME"
cp -r "$SRC_DIR/app" "$BRAUNY_HOME/"
cp "$SRC_DIR/requirements.txt" "$BRAUNY_HOME/"

# Skills: vorhandene Dateien NICHT ueberschreiben. Wer einen Skill angepasst
# oder einen eigenen dazugelegt hat, soll ihn nach einer Neuinstallation
# wiederfinden.
mkdir -p "$BRAUNY_HOME/skills"
for skill in "$SRC_DIR"/skills/*.md; do
  [ -e "$skill" ] || continue
  ziel="$BRAUNY_HOME/skills/$(basename "$skill")"
  if [ -e "$ziel" ]; then
    echo "   behalte vorhandenen Skill: $(basename "$skill")"
  else
    cp "$skill" "$ziel"
  fi
done

if [ ! -f "$BRAUNY_HOME/app/static/icons/icon-512.png" ]; then
  step "PWA-Icons erzeugen"
  python3 "$SRC_DIR/scripts/make_icons.py"
  cp -r "$SRC_DIR/app/static/icons" "$BRAUNY_HOME/app/static/"
fi

step "Python-Umgebung anlegen"
# Ubuntu 24.04 ist PEP-668-"externally managed": ein globales pip3 install
# bricht mit error: externally-managed-environment ab. Deshalb venv.
[ -d "$BRAUNY_HOME/venv" ] || python3 -m venv "$BRAUNY_HOME/venv"
"$BRAUNY_HOME/venv/bin/pip" install --quiet --upgrade pip
"$BRAUNY_HOME/venv/bin/pip" install --quiet -r "$BRAUNY_HOME/requirements.txt"

# ---------------------------------------------------------------- 5. Config
ENV_FILE="$BRAUNY_HOME/brauny.env"
if [ -f "$ENV_FILE" ]; then
  step "Bestehende Konfiguration behalten ($ENV_FILE)"
else
  # Ein vorgegebenes Token ist der einzige Weg, die Installation ohne SSH
  # durchlaufen zu lassen: bei einer unbeaufsichtigten Einrichtung koennte
  # sonst niemand das erzeugte Token je lesen.
  if [ -n "${BRAUNY_TOKEN:-}" ]; then
    step "Vorgegebenes Zugangs-Token uebernehmen"
    TOKEN="$BRAUNY_TOKEN"
    [ "${#TOKEN}" -ge 12 ] || die "BRAUNY_TOKEN ist zu kurz (mindestens 12 Zeichen)."
  else
    step "Zugangs-Token erzeugen"
    TOKEN="$(head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 32)"
  fi
  cat > "$ENV_FILE" <<EOF
BRAUNY_TOKEN=$TOKEN
BRAUNY_MODEL=$BRAUNY_MODEL
BRAUNY_SANDBOX_IMAGE=$SANDBOX_IMAGE
BRAUNY_SANDBOX_TIMEOUT=60
BRAUNY_MAX_ATTEMPTS=3
BRAUNY_MAX_CONCURRENT=2
BRAUNY_ASK_TIMEOUT=300
BRAUNY_WORKSPACE=$BRAUNY_HOME/workspace

# Arbeitsweise: auto = Werkzeugschleife mit Rueckfall auf den einfachen Weg,
# tools = nur Werkzeuge, oneshot = nur planen/schreiben/ausfuehren.
BRAUNY_AGENT=auto
BRAUNY_MAX_STEPS=12

# Modellquelle. ollama = lokal und kostenlos.
# Fuer ein staerkeres Modell ueber eine OpenAI-kompatible API:
#   BRAUNY_PROVIDER=openai
#   BRAUNY_API_BASE=https://<anbieter>/v1
#   BRAUNY_API_KEY=<schluessel>
#   BRAUNY_MODEL=<modellname des anbieters>
# Diese Datei hat Rechte 600 - der Schluessel bleibt lokal. Nach Aenderungen:
#   sudo systemctl restart braunycode
BRAUNY_PROVIDER=ollama
BRAUNY_API_BASE=
BRAUNY_API_KEY=

# Skills: Verfahrenswissen als Markdown. Eigene Dateien einfach dazulegen.
BRAUNY_SKILLS=$BRAUNY_HOME/skills

# Konnektoren - der einzige Weg des Agenten nach draussen. Standard: zu.
#
# Webseiten lesen. Interne Adressen und Cloud-Metadaten (169.254.169.254)
# sind auch dann gesperrt - das ist nicht abschaltbar.
BRAUNY_FETCH=0
#
# Ergebnisse auf ein Git-Repository schieben. Fuer GitHub ein Fine-grained
# Token mit Contents:write NUR fuer dieses eine Repository erzeugen:
#   BRAUNY_GIT_REMOTE=https://github.com/<nutzer>/<repo>.git
#   BRAUNY_GIT_TOKEN=<token>
# Der Agent pusht nur auf Branches mit dem Praefix unten, nie auf main.
BRAUNY_GIT_REMOTE=
BRAUNY_GIT_TOKEN=
BRAUNY_GIT_BRANCH_PREFIX=brauny/
EOF
fi
chmod 600 "$ENV_FILE"

# ---------------------------------------------------------------- 6. systemd
step "systemd-Dienst einrichten"
# Eigenes TMPDIR statt PrivateTmp: die Projektverzeichnisse werden per
# bind-mount in Docker gereicht, und der Docker-Daemon sieht einen privaten
# /tmp-Namespace nicht - der Mount waere leer.
TMP_DIR="$BRAUNY_HOME/tmp"
mkdir -p "$TMP_DIR"

# systemd statt tmux: ueberlebt Reboots und startet nach Abstuerzen neu.
sudo tee /etc/systemd/system/braunycode.service >/dev/null <<EOF
[Unit]
Description=BraunyCode Cloud Agent
After=network-online.target docker.service ollama.service
Wants=network-online.target
Requires=docker.service

[Service]
Type=simple
User=$USER
Group=docker
WorkingDirectory=$BRAUNY_HOME/app
Environment=TMPDIR=$TMP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$BRAUNY_HOME/venv/bin/uvicorn main:app --app-dir $BRAUNY_HOME/app --host 0.0.0.0 --port $BRAUNY_PORT
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable braunycode
sudo systemctl restart braunycode

# ---------------------------------------------------------------- 6b. Update
# Damit die naechste Aktualisierung ein Wort ist statt einer langen Zeile, die
# man auf einem Telefon abtippt und dabei verunstaltet.
if [ -f "$SRC_DIR/deploy/update.sh" ]; then
  step "Aktualisierungsbefehl einrichten (braunycode-update)"
  sudo install -m 0755 "$SRC_DIR/deploy/update.sh" /usr/local/bin/braunycode-update
fi

# ---------------------------------------------------------------- 7. Firewall
step "Lokale Firewall fuer Port $BRAUNY_PORT oeffnen"
# Manche Ubuntu-Images (Oracle) bringen iptables-Regeln mit, die alles ausser
# Port 22 verwerfen. Ohne diese Regel ist der Port dann dicht, obwohl beim
# Anbieter alles offen aussieht. Auf Images ohne solche Regeln (Contabo,
# Hetzner) ist die Zeile wirkungslos - sie schadet aber auch nicht.
if ! sudo iptables -C INPUT -p tcp --dport "$BRAUNY_PORT" -j ACCEPT 2>/dev/null; then
  sudo iptables -I INPUT 1 -p tcp --dport "$BRAUNY_PORT" -j ACCEPT
fi
sudo netfilter-persistent save >/dev/null 2>&1 \
  || warn "netfilter-persistent fehlt - Regel ist nach dem naechsten Reboot weg."

# ---------------------------------------------------------------- fertig
source "$ENV_FILE"
IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo '<SERVER-IP>')"

cat <<EOF

============================================================
  BraunyCode Cloud laeuft.

  Adresse   http://$IP:$BRAUNY_PORT
  Token     $BRAUNY_TOKEN
  Modell    $BRAUNY_MODEL
  Projekt   $BRAUNY_HOME/workspace
  Skills    $BRAUNY_HOME/skills (eigene .md einfach dazulegen)

  FALLS DIE ADRESSE NICHT ANTWORTET:
    Der Port ist lokal offen. Bleibt er von aussen dicht, filtert der
    Anbieter davor. Bei Oracle Cloud:
      Networking > Virtual Cloud Networks > dein VCN
        > Security Lists > Default Security List > Add Ingress Rule
          Source CIDR 0.0.0.0/0 | TCP | Destination Port $BRAUNY_PORT
    Contabo und Hetzner haben ab Werk keine solche Filterung.

  ALS APP AUFS HANDY (jeder Browser):
    Adresse oeffnen > Menue- bzw. Teilen-Symbol >
    "Zum Home-Bildschirm" / "App installieren".
    Startet dann im Vollbild ohne Browserleiste.

  Aktualisieren  sudo braunycode-update

  Status    sudo systemctl status braunycode
  Logs      journalctl -u braunycode -f
  Health    curl -s localhost:$BRAUNY_PORT/healthz

  Docker ohne sudo nutzen: einmal aus- und wieder einloggen.
============================================================
EOF
