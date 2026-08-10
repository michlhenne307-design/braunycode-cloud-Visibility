#!/usr/bin/env bash
#
# Wie schnell ist die Maschine wirklich?
#
#   braunycode-messen
#
# Beantwortet die eine Frage, die ueber alles andere entscheidet: Wie viele
# Token pro Sekunde schafft dieses Modell auf dieser CPU - und wie lange
# braucht es, den Prompt ueberhaupt zu lesen?
#
# Warum getrennt gemessen wird: Ein Agentenschritt besteht aus zwei sehr
# verschiedenen Teilen. Erst wird der gesamte Prompt gelesen (Anweisung,
# Werkzeuge, bisheriger Verlauf), dann wird geantwortet. Ist das Lesen teuer,
# hilft ein kuerzerer Prompt - ein anderes Modell dagegen kaum. Ist das
# Antworten teuer, ist es umgekehrt. Wer nur "es ist langsam" weiss, aendert
# meist das Falsche.
set -uo pipefail

ENV_DATEI="${BRAUNY_ENV:-/home/brauny/braunycode/brauny.env}"
MODELL="${BRAUNY_MODEL:-$(grep -m1 '^BRAUNY_MODEL=' "$ENV_DATEI" 2>/dev/null | cut -d= -f2-)}"
MODELL="${MODELL:-qwen3-coder:30b}"
HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
case "$HOST" in http*) ;; *) HOST="http://$HOST" ;; esac

echo "Modell:  $MODELL"
echo "Ollama:  $HOST"
echo

lauf() {
  local titel="$1" prompt="$2"
  local antwort
  antwort="$(curl -sS --max-time 900 "$HOST/api/chat" -d "$(python3 -c '
import json, sys
print(json.dumps({"model": sys.argv[1], "stream": False, "keep_alive": "2h",
                  "messages": [{"role": "user", "content": sys.argv[2]}],
                  "options": {"temperature": 0, "num_predict": 64}}))
' "$MODELL" "$prompt")" 2>&1)" || { echo "  Fehlgeschlagen: $antwort"; return 1; }
  MESSUNG="$antwort" python3 - "$titel" <<'PY'
import json, os, sys
try:
    d = json.loads(os.environ["MESSUNG"])
except Exception:
    print(f"  {sys.argv[1]}: unlesbare Antwort"); raise SystemExit
if d.get("error"):
    print(f"  {sys.argv[1]}: {d['error']}"); raise SystemExit
pe, pd = d.get("prompt_eval_count", 0), d.get("prompt_eval_duration", 0) / 1e9
ec, ed = d.get("eval_count", 0), d.get("eval_duration", 0) / 1e9
ld = d.get("load_duration", 0) / 1e9
print(f"  {sys.argv[1]}")
print(f"    Prompt lesen : {pe:>6} Token in {pd:6.1f}s"
      + (f"  = {pe/pd:6.1f} Token/s" if pd > 0.05 else ""))
print(f"    Antworten    : {ec:>6} Token in {ed:6.1f}s"
      + (f"  = {ec/ed:6.1f} Token/s" if ed > 0.05 else ""))
if ld > 1:
    print(f"    Modell laden : {ld:6.1f}s   <- beim zweiten Lauf sollte das 0 sein")
PY
}

lauf "1. Kalt (Modell wird geladen)" "Antworte nur mit OK"
echo
# Ein langer Prompt zeigt, was das Lesen wirklich kostet - genau dieser Teil
# waechst mit jedem Werkzeug und jedem Schritt.
LANG="$(python3 -c 'print("Hier ist ein Stueck Beispielcode. " * 200)')"
lauf "2. Warm, langer Prompt (~1500 Token)" "$LANG Antworte nur mit OK"

cat <<'TEXT'

So liest du das:
  Antworten unter 3 Token/s  -> die Werkzeugschleife dauert Minuten je Schritt.
                                Dann lohnt ein kleineres Modell
                                (braunycode-modell lokal qwen2.5-coder:7b)
                                oder eine API (braunycode-modell deepseek KEY).
  "Modell laden" beim 2. Lauf ueber 0 -> keep_alive greift nicht, jeder Schritt
                                zahlt das Nachladen erneut.
TEXT
