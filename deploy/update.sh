#!/usr/bin/env bash
#
# BraunyCode aktualisieren - ein Befehl, egal in welchem Zustand die Maschine
# gerade ist.
#
#   braunycode-update            (nach der ersten Installation)
#   bash update.sh               (direkt)
#
# Warum es das gibt: Die Anleitung lautete vorher "cd ins Quellverzeichnis,
# git pull, Installer starten". Fehlt das Verzeichnis - weil ein frueherer
# Lauf es geloescht hat -, bricht die erste Anweisung ab und alles dahinter
# passiert stillschweigend nicht. Auf einem Telefon sieht man das leicht
# nicht: es kommt ja sofort wieder ein Prompt.
#
# Deshalb hier: nichts wird vorausgesetzt. Es wird frisch geholt.
set -euo pipefail

BRANCH="${BRAUNY_BRANCH:-claude/ki-firmensystem-iphone-v7yxiu}"
REPO="${BRAUNY_REPO:-https://github.com/michlhenne307-design/braunycode-cloud-visibility.git}"
SRC="${BRAUNY_SRC:-/opt/brauny-src}"
LOG="${BRAUNY_UPDATE_LOG:-/var/log/braunycode-update.log}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Bitte als root ausfuehren: sudo braunycode-update" >&2
  exit 1
fi

command -v git >/dev/null || { apt-get update -qq && apt-get install -y -qq git; }

echo "Hole $BRANCH …"
rm -rf "$SRC"
git clone --quiet --branch "$BRANCH" "$REPO" "$SRC"
SHA="$(git -C "$SRC" rev-parse --short HEAD)"

# Losgeloest vom Terminal starten: bricht die Verbindung weg - auf einem
# Telefon der Normalfall -, laeuft die Einrichtung trotzdem zu Ende.
: > "$LOG"
setsid nohup bash "$SRC/install.sh" >> "$LOG" 2>&1 < /dev/null &

cat <<TEXT

Stand $SHA wird eingespielt. Das laeuft im Hintergrund weiter, auch wenn du
die Verbindung schliesst.

  Zusehen:   tail -f $LOG
  Nachsehen: tail -20 $LOG
  Fertig,    wenn dort die Zeile mit ==== erscheint.

TEXT
