#!/usr/bin/env bash
#
# HTTPS fuer BraunyCode einschalten.
#
#   sudo bash enable-https.sh <name>.duckdns.org
#
# Setzt Caddy als Reverse-Proxy davor. Caddy holt sich das Zertifikat bei
# Let's Encrypt selbst und erneuert es selbst - es ist nichts nachzuhalten.
#
# WARUM EIN DOMAINNAME NOETIG IST
# Let's Encrypt stellt keine Zertifikate auf IP-Adressen aus. Naheliegend
# waeren die Dienste sslip.io oder nip.io, die jede IP als Namen aufloesen -
# aber die stehen NICHT auf der Public Suffix List. Let's Encrypt zaehlt
# deshalb ganz sslip.io als eine einzige Domain mit 50 Zertifikaten pro
# Woche, geteilt mit allen Nutzern weltweit. Das Limit ist dauerhaft
# ausgeschoepft, die Ausstellung wuerde meistens scheitern.
#
# duckdns.org steht auf der Liste. Jede Unterdomain bekommt dort ein eigenes
# Kontingent - deshalb dieser Weg.
#
# PORT 8000 BLEIBT OFFEN. Scheitert die Zertifikatsausstellung, kommst du
# weiter ueber http://<IP>:8000 heran. Ein Fehler hier sperrt dich nicht aus.
set -euo pipefail

DOMAIN="${1:-}"
BACKEND="${BRAUNY_BACKEND:-127.0.0.1:8000}"

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!!  %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mXX  %s\033[0m\n' "$*" >&2; exit 1; }

[ -n "$DOMAIN" ] || die "Aufruf: sudo bash enable-https.sh <name>.duckdns.org"
[ "$(id -u)" -eq 0 ] || die "Bitte mit sudo starten."

# Ein Name mit Punkt und ohne Schema - haeufigster Eingabefehler.
case "$DOMAIN" in
  http://*|https://*) die "Ohne http:// bitte, nur den Namen." ;;
  *.*) : ;;
  *) die "'$DOMAIN' sieht nicht wie ein Domainname aus." ;;
esac

step "Prüfe, ob $DOMAIN auf diesen Server zeigt"
SERVER_IP="$(curl -fsS --max-time 10 https://api.ipify.org || true)"
DOMAIN_IP="$(getent hosts "$DOMAIN" | awk '{print $1; exit}' || true)"
if [ -z "$DOMAIN_IP" ]; then
  die "$DOMAIN löst nicht auf. Bei DuckDNS eintragen und ein paar Minuten warten."
fi
if [ -n "$SERVER_IP" ] && [ "$DOMAIN_IP" != "$SERVER_IP" ]; then
  # Kein Abbruch: hinter NAT oder mit mehreren Adressen kann das abweichen,
  # ohne dass etwas kaputt ist. Aber es ist der haeufigste Grund fuer ein
  # scheiterndes Zertifikat, also deutlich sagen.
  warn "$DOMAIN zeigt auf $DOMAIN_IP, dieser Server ist $SERVER_IP."
  warn "Wenn das nicht stimmt, scheitert die Zertifikatsausstellung."
fi

step "Caddy installieren"
if ! command -v caddy >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  echo "deb [signed-by=/usr/share/keyrings/caddy-stable-archive-keyring.gpg] \
https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main" \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq
  apt-get install -y -qq caddy
fi

step "Caddy für $DOMAIN einrichten"
cat > /etc/caddy/Caddyfile <<EOF
# Von BraunyCode enable-https.sh erzeugt.
$DOMAIN {
    encode zstd gzip

    # reverse_proxy reicht WebSocket-Upgrades von sich aus durch - fuer
    # /ws/agent ist also nichts Zusaetzliches noetig.
    reverse_proxy $BACKEND

    log {
        output file /var/log/caddy/braunycode.log
        format console
    }
}
EOF
mkdir -p /var/log/caddy
chown -R caddy:caddy /var/log/caddy 2>/dev/null || true

caddy validate --config /etc/caddy/Caddyfile >/dev/null \
  || die "Caddyfile ist fehlerhaft."

systemctl enable caddy >/dev/null 2>&1 || true
systemctl restart caddy

step "Warte auf das Zertifikat (bis zu 90 Sekunden)"
ERFOLG=0
for _ in $(seq 1 30); do
  if curl -fsS --max-time 5 "https://$DOMAIN/healthz" >/dev/null 2>&1; then
    ERFOLG=1
    break
  fi
  sleep 3
done

if [ "$ERFOLG" -eq 1 ]; then
  cat <<EOF

============================================================
  HTTPS steht.

  Neue Adresse   https://$DOMAIN
  Alte Adresse   http://$SERVER_IP:8000  (bleibt als Rueckfall offen)

  Erst ueber HTTPS funktionieren auch der Service Worker
  (Offline-Huelle der App) und der Kopieren-Knopf.

  Tipp: Die App neu zum Home-Bildschirm hinzufuegen, damit sie
  die HTTPS-Adresse benutzt.
============================================================
EOF
else
  warn "Zertifikat noch nicht da. Das ist nicht zwingend ein Fehler -"
  warn "die Ausstellung kann laenger dauern. Pruefen mit:"
  warn "  journalctl -u caddy -n 50 --no-pager"
  warn ""
  warn "Bis dahin bleibt http://$SERVER_IP:8000 erreichbar."
fi
