#!/usr/bin/env bash
#
# BraunyCode Cloud - Installer fuer Ubuntu 24.04 (Oracle Cloud Ampere A1 / arm64)
#
#   bash install.sh
#
# Idempotent: kann gefahrlos mehrfach laufen.
set -euo pipefail

BRAUNY_HOME="${BRAUNY_HOME:-$HOME/braunycode}"
BRAUNY_MODEL="${BRAUNY_MODEL:-llama3.1:8b}"
BRAUNY_PORT="${BRAUNY_PORT:-8000}"
SANDBOX_IMAGE="${BRAUNY_SANDBOX_IMAGE:-python:3.11-slim}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!!  %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mXX  %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "Bitte NICHT als root starten. Als Benutzer 'ubuntu' ausfuehren."
command -v sudo >/dev/null || die "sudo wird benoetigt."

# ---------------------------------------------------------------- 1. apt
step "Warte auf cloud-init und den apt-Lock"
# Frisch gebootete Cloud-Images laufen minutenlang mit unattended-upgrades.
# Ohne dieses Warten scheitert jedes apt mit "Could not get lock".
sudo cloud-init status --wait >/dev/null 2>&1 || true
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

step "Sandbox-Image vorladen ($SANDBOX_IMAGE)"
sudo docker pull -q "$SANDBOX_IMAGE"

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

step "Modell laden: $BRAUNY_MODEL (mehrere GB, das dauert)"
ollama pull "$BRAUNY_MODEL"

# ---------------------------------------------------------------- 4. App
step "Anwendung nach $BRAUNY_HOME kopieren"
mkdir -p "$BRAUNY_HOME"
cp -r "$SRC_DIR/app" "$BRAUNY_HOME/"
cp "$SRC_DIR/requirements.txt" "$BRAUNY_HOME/"

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
  step "Zugangs-Token erzeugen"
  TOKEN="$(head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 32)"
  cat > "$ENV_FILE" <<EOF
BRAUNY_TOKEN=$TOKEN
BRAUNY_MODEL=$BRAUNY_MODEL
BRAUNY_SANDBOX_IMAGE=$SANDBOX_IMAGE
BRAUNY_SANDBOX_TIMEOUT=60
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

# ---------------------------------------------------------------- 7. Firewall
step "Lokale Firewall fuer Port $BRAUNY_PORT oeffnen"
# Oracle-Ubuntu-Images bringen iptables-Regeln mit, die alles ausser Port 22
# verwerfen. Ohne diese Regel ist der Port trotz offener Security List dicht.
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
  BraunyCode Cloud v1.0.0 laeuft.

  Adresse   http://$IP:$BRAUNY_PORT
  Token     $BRAUNY_TOKEN
  Modell    $BRAUNY_MODEL

  NOCH ZU TUN in der Oracle Console:
    Networking > Virtual Cloud Networks > dein VCN
      > Security Lists > Default Security List
      > Add Ingress Rule
        Source CIDR      0.0.0.0/0
        IP Protocol      TCP
        Destination Port $BRAUNY_PORT

  Status    sudo systemctl status braunycode
  Logs      journalctl -u braunycode -f
  Health    curl -s localhost:$BRAUNY_PORT/healthz

  Docker ohne sudo nutzen: einmal aus- und wieder einloggen.
============================================================
EOF
