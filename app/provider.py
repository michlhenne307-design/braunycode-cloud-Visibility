"""Anbindung an das Modell - lokal oder ueber eine API.

Der Agent soll nicht an ein Modell gekettet sein. Ein 7B auf CPU ist bequem
und kostenlos, taugt aber schlecht fuer Werkzeugaufrufe; ein starkes Modell
ueber eine API dreht das um. Beides laeuft hier durch dieselbe Schnittstelle,
umschaltbar ueber eine Zeile in der Konfiguration.

  BRAUNY_PROVIDER=ollama   (Standard, lokal, kostenlos)
  BRAUNY_PROVIDER=openai   (jede OpenAI-kompatible API: Gemini, Groq,
                            DeepSeek, OpenRouter, ...)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

PROVIDER = os.environ.get("BRAUNY_PROVIDER", "ollama").strip().lower()
# Schreibweisen, die denselben OpenAI-kompatiblen Weg meinen.
OPENAI_ALIASES = ("openai", "openai-kompatibel", "api")
MODEL = os.environ.get("BRAUNY_MODEL", "qwen2.5-coder:7b")
API_BASE = os.environ.get("BRAUNY_API_BASE", "").rstrip("/")
API_KEY = os.environ.get("BRAUNY_API_KEY", "")
TIMEOUT = int(os.environ.get("BRAUNY_ASK_TIMEOUT", "300"))


@dataclass
class ToolCall:
    name: str
    arguments: dict
    call_id: str = ""


@dataclass
class Reply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class ProviderError(RuntimeError):
    pass


def _parse_arguments(raw) -> dict:
    """Argumente kommen je nach Anbieter als dict oder als JSON-Text."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"wert": parsed}
        except json.JSONDecodeError:
            return {}
    return {}


# ------------------------------------------------------------------ Ollama

def _chat_ollama(messages, tools):
    import ollama

    kwargs = {"model": MODEL, "messages": messages}
    if tools:
        kwargs["tools"] = tools
    response = ollama.chat(**kwargs)

    message = response["message"] if isinstance(response, dict) else response.message
    text = (message.get("content") if isinstance(message, dict)
            else getattr(message, "content", "")) or ""
    raw_calls = (message.get("tool_calls") if isinstance(message, dict)
                 else getattr(message, "tool_calls", None)) or []

    calls = []
    for item in raw_calls:
        function = item["function"] if isinstance(item, dict) else item.function
        name = function["name"] if isinstance(function, dict) else function.name
        args = (function.get("arguments") if isinstance(function, dict)
                else getattr(function, "arguments", {}))
        calls.append(ToolCall(name=name, arguments=_parse_arguments(args)))
    return Reply(text=text, tool_calls=calls)


# ------------------------------------------------------------------ OpenAI-kompatibel

def _chat_openai(messages, tools):
    import httpx

    if not API_BASE:
        raise ProviderError("BRAUNY_API_BASE ist nicht gesetzt.")
    payload = {"model": MODEL, "messages": messages}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    with httpx.Client(timeout=TIMEOUT) as client:
        response = client.post(f"{API_BASE}/chat/completions",
                               json=payload, headers=headers)
    if response.status_code >= 400:
        # Den Schluessel niemals in die Fehlermeldung nehmen.
        raise ProviderError(
            f"API antwortete mit {response.status_code}: {response.text[:300]}")

    data = response.json()
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError) as exc:
        raise ProviderError(f"Unerwartete Antwortstruktur: {str(data)[:300]}") from exc

    calls = [
        ToolCall(name=item["function"]["name"],
                 arguments=_parse_arguments(item["function"].get("arguments")),
                 call_id=item.get("id", ""))
        for item in (message.get("tool_calls") or [])
    ]
    return Reply(text=message.get("content") or "", tool_calls=calls)


# ------------------------------------------------------------------ Fassade

def chat(messages, tools=None) -> Reply:
    """Blockierender Modellaufruf. Aufrufer legen ihn in einen Thread."""
    if PROVIDER == "ollama":
        return _chat_ollama(messages, tools)
    if PROVIDER in OPENAI_ALIASES:
        return _chat_openai(messages, tools)
    raise ProviderError(f"Unbekannter Anbieter: {PROVIDER!r}")


def health() -> tuple[bool, str]:
    """Leichte Pruefung fuer /healthz.

    Bei Ollama wird tatsaechlich nachgefragt - das ist lokal und gratis. Eine
    entfernte API wird NICHT angerufen: ein Health-Check darf keine Anfragen
    verbrauchen, die Geld kosten. Geprueft wird nur die Konfiguration, und
    genau so wird es auch gemeldet.
    """
    if PROVIDER == "ollama":
        try:
            import ollama
            ollama.list()
            return True, "ok"
        except Exception as exc:
            return False, f"fehler: {exc}"
    if PROVIDER in OPENAI_ALIASES:
        if not API_BASE:
            return False, "fehler: BRAUNY_API_BASE fehlt"
        return True, "konfiguriert (nicht angefragt)"
    return False, f"fehler: unbekannter Anbieter {PROVIDER!r}"


def describe() -> dict:
    """Fuer /healthz - ohne den Schluessel preiszugeben."""
    return {
        "anbieter": PROVIDER,
        "modell": MODEL,
        "api_base": API_BASE or None,
        "schluessel_gesetzt": bool(API_KEY),
    }
