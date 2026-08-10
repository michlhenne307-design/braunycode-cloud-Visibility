#!/usr/bin/env bash
# Alle Pruefungen an einer Stelle - damit niemand die zweite vergisst.
#
#   bash pruefen.sh
#
# test_smoke.py prueft die Teile fuer sich.
# test_e2e.py laesst die ganze Werkzeugschleife gegen einen nachgebauten
# Ollama laufen. Beide Abstuerze aus dem Betrieb sassen zwischen zwei Modulen,
# nicht in einem - der zweite Lauf ist deshalb kein Luxus.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

fehler=0
for datei in test_smoke.py test_e2e.py; do
  printf '\n\033[1;34m==> %s\033[0m\n' "$datei"
  # Ausgabe erst vollstaendig einsammeln, DANN filtern.
  #
  # Vorher stand hier 'python3 ... | grep ... || true' mit einer Abfrage von
  # PIPESTATUS danach. Das '|| true' setzt PIPESTATUS aber zurueck: der
  # Exit-Code des Testlaufs war anschliessend immer 0. Das Skript hat deshalb
  # "Alles gruen" gemeldet, waehrend test_e2e.py rot war - der schlimmste
  # Fehler, den ausgerechnet ein Pruefskript haben kann.
  ausgabe="$(python3 "$datei" 2>&1)"
  status=$?
  printf '%s\n' "$ausgabe" | grep -E '^  FAIL|^=== ' || true
  [ "$status" -eq 0 ] || fehler=1
done

if [ "$fehler" -ne 0 ]; then
  printf '\n\033[1;31mXX  Es ist etwas fehlgeschlagen.\033[0m\n'
  exit 1
fi
printf '\n\033[1;32m==> Alles gruen.\033[0m\n'
