#!/usr/bin/env bash
#
# Zwischen lokalem Modell und einer API umschalten.
#
#   braunycode-modell                      zeigt, was gerade laeuft
#   braunycode-modell lokal                zurueck zu ollama
#   braunycode-modell lokal qwen2.5-coder:7b   kleineres lokales Modell
#   braunycode-modell deepseek <SCHLUESSEL>    DeepSeek statt CPU
#   braunycode-modell api <basis> <modell> <schluessel>   beliebiger Anbieter
#
# Warum: Ein 30B-Modell auf 8 CPU-Kernen braucht Minuten je Schritt. Eine API
# antwortet in Sekunden. Das ist kein Widerspruch zum Selbstbetreiben - der
# Agent, die Werkzeuge, die Sandbox und alle Daten bleiben auf deinem Server.
# Nur das Denken wird ausgelagert, und zwar umschaltbar.
#
# WAS DABEI PASSIERT, ehrlich: Bei jedem Schritt gehen Auftrag, Dateinamen und
# Codeausschnitte an den Anbieter. Fuer fremden oder vertraulichen Code ist das
# die falsche Wahl. Und es kostet Geld pro Token - kein Guthaben, kein Lauf.
set -euo pipefail

ENV_DATEI="${BRAUNY_ENV:-/home/brauny/braunycode/brauny.env}"
[ -f "$ENV_DATEI" ] || { echo "Nicht gefunden: $ENV_DATEI" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo ausfuehren." >&2; exit 1; }

setze() {
  # Zeile ersetzen oder anhaengen - ohne die Datei sonst anzufassen.
  local schluessel="$1" wert="$2"
  if grep -q "^${schluessel}=" "$ENV_DATEI"; then
    sed -i "s|^${schluessel}=.*|${schluessel}=${wert}|" "$ENV_DATEI"
  else
    printf '%s=%s\n' "$schluessel" "$wert" >> "$ENV_DATEI"
  fi
}

zeigen() {
  echo "Anbieter: $(grep -m1 '^BRAUNY_PROVIDER=' "$ENV_DATEI" | cut -d= -f2- || echo ollama)"
  echo "Modell:   $(grep -m1 '^BRAUNY_MODEL=' "$ENV_DATEI" | cut -d= -f2-)"
  local basis
  basis="$(grep -m1 '^BRAUNY_API_BASE=' "$ENV_DATEI" | cut -d= -f2- || true)"
  [ -n "$basis" ] && echo "API:      $basis"
  # Den Schluessel NIE ausgeben - nur, ob einer da ist.
  if grep -q '^BRAUNY_API_KEY=.\+' "$ENV_DATEI"; then
    echo "Schlüssel: gesetzt"
  else
    echo "Schlüssel: keiner"
  fi
}

neustart() {
  chown brauny:brauny "$ENV_DATEI" 2>/dev/null || true
  chmod 600 "$ENV_DATEI"
  systemctl restart braunycode
  echo
  zeigen
  echo
  echo "Dienst neu gestartet."
}

case "${1:-zeigen}" in
  zeigen|status|"")
    zeigen
    ;;
  lokal|ollama)
    setze BRAUNY_PROVIDER ollama
    setze BRAUNY_MODEL "${2:-qwen3-coder:30b}"
    setze BRAUNY_API_BASE ""
    setze BRAUNY_API_KEY ""
    neustart
    ;;
  deepseek)
    [ -n "${2:-}" ] || { echo "Aufruf: braunycode-modell deepseek <SCHLUESSEL>" >&2; exit 1; }
    setze BRAUNY_PROVIDER openai
    setze BRAUNY_API_BASE "https://api.deepseek.com/v1"
    setze BRAUNY_MODEL "${3:-deepseek-chat}"
    setze BRAUNY_API_KEY "$2"
    neustart
    ;;
  api)
    [ -n "${4:-}" ] || {
      echo "Aufruf: braunycode-modell api <basis-url> <modell> <schluessel>" >&2; exit 1; }
    setze BRAUNY_PROVIDER openai
    setze BRAUNY_API_BASE "$2"
    setze BRAUNY_MODEL "$3"
    setze BRAUNY_API_KEY "$4"
    neustart
    ;;
  *)
    echo "Unbekannt: $1" >&2
    sed -n '3,12p' "$0" >&2
    exit 1
    ;;
esac
