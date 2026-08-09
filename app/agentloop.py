"""Die Agentenschleife - der Unterschied zwischen Generator und Agent.

Bisher: einmal Code raten, ausfuehren, fertig. Das ist ein Generator.
Ein Agent arbeitet wie ein Mensch am Rechner: schauen, lesen, aendern,
ausfuehren, Ergebnis pruefen, weitermachen. Das Modell entscheidet in jedem
Schritt selbst, welches Werkzeug dran ist; die Schleife hier fuehrt es aus
und reicht das Ergebnis zurueck.

Die Schleife ist bewusst misstrauisch: kleine Modelle drehen sich im Kreis,
reden statt zu handeln und melden Erfolg, ohne etwas ausgefuehrt zu haben.
Alle drei Faelle werden erkannt und benannt statt beschoenigt.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import provider
import tools

# Wie viele Werkzeugrunden ein Auftrag hoechstens bekommt. Jede Runde ist ein
# Modellaufruf - auf CPU sind das schnell Minuten, deshalb ein enger Deckel.
MAX_STEPS = max(1, int(os.environ.get("BRAUNY_MAX_STEPS", "12")))
# Derselbe Aufruf so oft hintereinander -> das Modell haengt fest.
REPEAT_LIMIT = 3
# Nachrichten, die im Kontext bleiben. Ein 7B-Fenster ist schnell voll.
MAX_HISTORY = int(os.environ.get("BRAUNY_MAX_HISTORY", "40"))

SYSTEM_PROMPT = (
    "Du bist BraunyCode, ein Programmier-Agent. Du arbeitest an einem echten "
    "Projektverzeichnis und veraenderst es ausschliesslich ueber die "
    "bereitgestellten Werkzeuge.\n\n"
    "Arbeitsweise:\n"
    "1. Verschaffe dir einen Ueberblick (list_files, glob, outline, "
    "read_file), bevor du etwas aenderst.\n"
    "2. Aendere mit edit_file: nur den Ausschnitt angeben, der sich aendert. "
    "write_file ist NUR fuer neue Dateien oder vollstaendigen Ersatz.\n"
    "3. Pruefe mit check_syntax, dann mit run_python oder run_command.\n"
    "4. Erst wenn die Aufgabe erledigt ist, rufe finish mit einer kurzen "
    "Zusammenfassung auf.\n\n"
    "Regeln:\n"
    "- Pro Antwort genau ein Werkzeugaufruf, kein Fliesstext daneben.\n"
    "- Rate nie den Inhalt einer Datei, lies sie.\n"
    "- Fuer edit_file muss old_text exakt so in der Datei stehen, mit "
    "Einrueckung. Ist die Stelle mehrdeutig, gib mehr Zeilen drumherum mit.\n"
    "- Zum Umbenennen einer Funktion oder Klasse nimm rename_symbol - das "
    "arbeitet ueber den Syntaxbaum und ist genauer als eine Textersetzung.\n"
    "- Hast du etwas zerschossen, setz mit undo auf den letzten Commit "
    "zurueck, statt weiter daran herumzuflicken.\n"
    "- Wiederhole keinen Aufruf, der schon dasselbe Ergebnis geliefert hat.\n"
    "- Der Code laeuft ohne Netzwerk, ohne Eingabe (kein input()) und nur mit "
    "der Standardbibliothek. Er muss von selbst terminieren.\n"
    "- Behaupte in finish nichts, was du nicht ausgefuehrt hast."
)

NUDGE = (
    "Das war Text, kein Werkzeugaufruf. Rufe jetzt genau ein Werkzeug auf. "
    "Wenn du keines mehr brauchst, rufe finish auf."
)


def _summarize(name: str, arguments: dict) -> str:
    """Einzeiler fuer die Oberflaeche: read_file(path=app/main.py)."""
    parts = []
    for key, value in (arguments or {}).items():
        text = str(value).replace("\n", " ").strip()
        if len(text) > 60:
            text = text[:60] + "…"
        parts.append(f"{key}={text}")
    return f"{name}({', '.join(parts)})"


def _assistant_message(reply, calls) -> dict:
    """Der Aufruf muss im Verlauf stehen, bevor sein Ergebnis folgt.

    OpenAI-kompatible APIs weisen ein 'tool'-Ergebnis ohne den zugehoerigen
    Aufruf davor ab. Ollama ist toleranter, aber das Format ist dasselbe.
    """
    return {
        "role": "assistant",
        "content": reply.text or "",
        "tool_calls": [
            {
                "id": call.call_id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, default=str),
                },
            }
            for call in calls
        ],
    }


def trim(messages: list[dict], limit: int = MAX_HISTORY) -> list[dict]:
    """Haelt den Verlauf kurz, ohne Aufruf und Ergebnis zu trennen.

    Systemanweisung und Aufgabe bleiben immer stehen - ohne sie vergisst das
    Modell nach ein paar Runden, woran es ueberhaupt arbeitet.
    """
    if len(messages) <= limit:
        return messages
    head, tail = messages[:2], messages[2:]
    drop = len(messages) - limit
    # Bis zur naechsten Assistenten-Nachricht weiterschneiden: ein 'tool' ohne
    # den Aufruf davor ist ein ungueltiger Verlauf.
    while drop < len(tail) and tail[drop].get("role") != "assistant":
        drop += 1
    return head + tail[drop:]


async def run_tool_agent(send, task, *, chat_fn, toolbox, max_steps=MAX_STEPS,
                         context="", skill=None):
    """Laesst das Modell mit Werkzeugen am Projekt arbeiten.

    chat_fn(messages, schema) -> provider.Reply ist hineingereicht, damit die
    Schleife ohne echtes Modell getestet werden kann. skill ist optionales
    Verfahrenswissen, das vor die Aufgabe gestellt wird.

    Rueckgabe:
      "ok"       - das Modell hat finish gemeldet
      "failed"   - Schrittgrenze oder Modellfehler, 'done' wurde gesendet
      "no-tools" - das Modell kann keine Werkzeuge; KEIN 'done' gesendet,
                   der Aufrufer soll auf den einfachen Weg wechseln
    """
    start = time.monotonic()

    def elapsed():
        return round(time.monotonic() - start, 1)

    system = SYSTEM_PROMPT
    if skill is not None:
        # Der Skill schraenkt die Werkzeuge ggf. ein - das muss VOR dem
        # Schema passieren, sonst sieht das Modell Werkzeuge, die es nicht
        # benutzen darf. Idempotent, falls der Aufrufer es schon getan hat.
        unbekannt = toolbox.restrict(skill.werkzeuge)
        if unbekannt:
            await send("error", f"Skill „{skill.name}“ nennt unbekannte "
                                f"Werkzeuge: {', '.join(unbekannt)} — ignoriert.")
        system = f"{SYSTEM_PROMPT}\n\n{skill.prompt()}"

    head = f"Bestehendes Projekt:\n{context}\n\n" if context else ""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"{head}Aufgabe: {task}"},
    ]
    schema = toolbox.schema()
    repeats: dict[str, int] = {}
    idle_rounds = 0
    executed = False   # wurde run_python jemals erfolgreich ausgefuehrt

    for step in range(1, max_steps + 1):
        try:
            reply = await chat_fn(messages, schema)
        except Exception as exc:
            await send("error", f"Modellaufruf fehlgeschlagen: "
                                f"{type(exc).__name__}: {exc}")
            await send("done", "Abgebrochen.", ok=False, exit=-1,
                       attempts=step, seconds=elapsed())
            return "failed"

        calls = list(reply.tool_calls)
        if not calls:
            # Kleine Modelle koennen oft keine echten Werkzeugaufrufe, geben
            # aber passendes JSON aus. Das gilt als Aufruf.
            fallback = tools.parse_fallback_call(reply.text)
            if fallback:
                calls = [provider.ToolCall(name=fallback[0], arguments=fallback[1])]

        if not calls:
            idle_rounds += 1
            if idle_rounds == 1 and step == 1:
                # Erste Runde ohne Werkzeug: das Modell beherrscht sie
                # vermutlich nicht. Nicht elf weitere Runden verschwenden.
                await send("status", "Das Modell hat kein Werkzeug aufgerufen.")
                return "no-tools"
            if idle_rounds >= 2:
                text = (reply.text or "").strip()
                await send("done",
                           text[:400] or "Beendet, ohne ein Werkzeug zu benutzen.",
                           ok=False, exit=-1, attempts=step, seconds=elapsed())
                return "failed"
            messages.append({"role": "assistant", "content": reply.text or ""})
            messages.append({"role": "user", "content": NUDGE})
            continue

        idle_rounds = 0
        for position, call in enumerate(calls):
            # Ollama liefert keine Aufruf-ID; die Zuordnung im Verlauf braucht
            # aber eine, sonst bricht der OpenAI-Weg.
            call.call_id = call.call_id or f"call_{step}_{position}"

        messages.append(_assistant_message(reply, calls))

        for call in calls:
            await send("tool", _summarize(call.name, call.arguments), step=step)
            result = await toolbox.call(call.name, call.arguments)
            messages.append(tools.format_result(call.name, result, call.call_id))

            failed = result.startswith("FEHLER")
            if call.name == "write_file" and not failed:
                await send("code", str(call.arguments.get("content", "")),
                           lang="python", attempt=step,
                           path=str(call.arguments.get("path", "")))
            elif call.name in ("edit_file", "rename_symbol") and not failed:
                # Nach einer Teiländerung den neuen Gesamtstand zeigen -
                # sonst sieht die Oberfläche nur den Ausschnitt.
                ziel = str(call.arguments.get("path", ""))
                try:
                    await send("code", toolbox.ws.read(ziel), lang="python",
                               attempt=step, path=ziel)
                except Exception:
                    pass
            elif call.name in ("run_python", "run_command"):
                for line in result.splitlines():
                    await send("sandbox", line)
                executed = executed or result.startswith(
                    ("Lauf erfolgreich", "Befehl erfolgreich"))
            elif call.name in ("fetch_url", "git_push") and not failed:
                # Schritte nach draussen gehoeren sichtbar ins Protokoll.
                await send("status", result.splitlines()[0] if result else call.name)
            elif failed:
                await send("error", result)

            if toolbox.finished is not None:
                return await _finish(send, task, toolbox, step, elapsed(), executed)

            fingerprint = f"{call.name}:{json.dumps(call.arguments, sort_keys=True, default=str)}"
            repeats[fingerprint] = repeats.get(fingerprint, 0) + 1
            if repeats[fingerprint] >= REPEAT_LIMIT:
                messages.append({"role": "user", "content":
                    f"Du hast '{call.name}' mit denselben Argumenten "
                    f"{repeats[fingerprint]}-mal aufgerufen. Das Ergebnis "
                    "aendert sich nicht. Mach etwas anderes oder rufe finish auf."})
                repeats[fingerprint] = 0

        messages = trim(messages)

    await send("done", f"Schrittgrenze von {max_steps} Runden erreicht, ohne dass "
                       "der Agent fertig gemeldet hat.",
               ok=False, exit=-1, attempts=max_steps, seconds=elapsed())
    return "failed"


async def _finish(send, task, toolbox, step, seconds, executed) -> str:
    """Schliesst den Lauf ab: Aenderungen sichern, ehrlich melden."""
    summary = toolbox.finished or "Fertig."
    changed = sorted(set(toolbox.written))

    commit = ""
    ws = getattr(toolbox, "ws", None)
    if ws is not None and changed:
        sha = await asyncio.to_thread(ws.git_commit, f"Agent: {task[:60]}")
        if sha:
            commit = f" Commit {sha}."

    await send("status", "Geänderte Dateien: " +
               (", ".join(changed) if changed else "keine"))
    if getattr(toolbox, "pushed", None):
        await send("status", "Nach außen übertragen: " +
                   "; ".join(toolbox.pushed))
    if not executed:
        # Das Modell behauptet Erfolg, ohne den Code laufen gelassen zu haben.
        # Das gehoert dazugesagt, statt es als geprueft zu verkaufen.
        await send("error", "Hinweis: Der Agent hat den Code nicht ausgeführt. "
                            "Die Zusammenfassung ist seine eigene Einschätzung, "
                            "kein Testergebnis.")

    await send("done", f"{summary}{commit}", ok=True, exit=0,
               attempts=step, seconds=seconds, verified=executed)
    return "ok"
