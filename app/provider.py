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

# ---------------------------------------------------------- Abtastverhalten
#
# Bisher wurde gar keine Temperatur gesetzt - der Lauf uebernahm damit die
# Vorgabe des Anbieters, bei den meisten Modellen um 0.8. Fuer einen Agenten,
# der Werkzeuge mit exakten Argumenten aufrufen soll, ist das die falsche
# Einstellung: dieselbe Aufgabe erzeugt zweimal verschiedene Aufrufe, und ein
# Fehlschlag laesst sich nicht nachstellen.
#
# Eine pauschale Regel "immer 0" waere aber ebenso falsch. Wo mehrere
# Loesungsvorschlaege verglichen werden sollen, ist Vielfalt genau der Zweck.
# Deshalb zwei benannte Betriebsarten statt einer Zahl im Code.
DETERMINISTISCH = "deterministisch"
VIELFALT = "vielfalt"

TEMPERATUREN = {DETERMINISTISCH: 0.0, VIELFALT: 0.8}

# Ein fester Startwert macht auch die Reihenfolge gleicher
# Wahrscheinlichkeiten reproduzierbar. Bei Ollama ist er unbedenklich, weil
# lokal und bekannt. Fremde OpenAI-kompatible Endpunkte kennen 'seed' nicht
# alle und weisen unbekannte Felder teils zurueck - dort nur auf ausdrueckliche
# Ansage. Das ist der Unterschied zwischen "reproduzierbar" und "kaputt".
def _seed_lesen():
    """Einmal beim Laden auswerten, nicht bei jedem Aufruf.

    Ein Tippfehler in BRAUNY_SEED wuerde sonst jeden einzelnen Modellaufruf
    mit einem ValueError sprengen - und zwar mitten im Lauf, weit weg von der
    Ursache. Hier faellt er sofort auf und wird ignoriert statt weitergereicht.
    """
    roh = os.environ.get("BRAUNY_SEED", "").strip()
    if not roh:
        return None
    try:
        return int(roh)
    except ValueError:
        import logging
        logging.getLogger(__name__).warning(
            "BRAUNY_SEED=%r ist keine ganze Zahl — wird ignoriert.", roh)
        return None


SEED = _seed_lesen()


def _temperatur(policy: str) -> float:
    return TEMPERATUREN.get(policy, TEMPERATUREN[DETERMINISTISCH])


@dataclass
class ToolCall:
    name: str
    arguments: dict
    call_id: str = ""


@dataclass
class Reply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Was der Aufruf gekostet hat. Ohne Zahlen bleibt "es ist langsam" eine
    # Meinung; mit ihnen sieht man, ob die Zeit ins Einlesen des Prompts, ins
    # Erzeugen oder ins Nachladen des Modells geht - drei ganz verschiedene
    # Probleme mit drei ganz verschiedenen Loesungen.
    messung: dict = field(default_factory=dict)


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
#
# Hier wird bewusst NICHT das Python-Paket 'ollama' benutzt, sondern direkt
# dessen HTTP-Schnittstelle. Der Grund ist ein echter Abbruch aus dem Betrieb:
#
#   ValidationError: 1 validation error for Message
#   tool_calls.0.function.arguments
#     Input should be a valid dictionary
#     [type=dict_type, input_value='{}', input_type=str]
#
# Das Paket legt die Antwort in ein pydantic-Modell, das fuer 'arguments' ein
# dict verlangt. qwen3-coder liefert an dieser Stelle einen JSON-*Text*. Das
# ist bei Werkzeugaufrufen verbreitet - die OpenAI-Schnittstelle gibt sie
# sogar immer so zurueck, weshalb _parse_arguments beide Formen kennt.
#
# Nur kam _parse_arguments nie zum Zug: das Paket bricht schon beim Einlesen
# ab. Die Absicherung sass hinter einer Mauer. Ueber HTTP kommt die Antwort
# als gewoehnliches JSON an, und die Behandlung greift wieder.
#
# Der Preis dafuer ist gering: /api/chat ist ein einzelner POST, und wir
# bleiben unabhaengig davon, wie streng ein Client-Paket kuenftige Antworten
# typisiert.

def _ollama_host() -> str:
    """Adresse des Ollama-Dienstes, in der Schreibweise von ollama selbst.

    OLLAMA_HOST wird dort auch ohne Schema angegeben ('127.0.0.1:11434').
    """
    roh = os.environ.get("OLLAMA_HOST", "").strip() or "127.0.0.1:11434"
    if not roh.startswith(("http://", "https://")):
        roh = "http://" + roh
    return roh.rstrip("/")


OLLAMA_HOST = _ollama_host()

# Ein grosses Modell muss beim ersten Aufruf erst von der Platte in den
# Speicher - bei 19 GB dauert das Minuten, und die Erzeugung auf CPU danach
# ebenfalls. Ein knapper Zeitwert wuerde genau den ersten Lauf abwuergen, der
# ohnehin der schwierigste ist.
MODELL_TIMEOUT = float(os.environ.get("BRAUNY_MODEL_TIMEOUT", "900"))

# Wie lange Ollama das Modell nach einem Aufruf im Speicher behaelt. Die
# Vorgabe dort sind fuenf Minuten. In einer Werkzeugschleife vergeht zwischen
# zwei Modellaufrufen aber leicht mehr - ein Test laeuft, ein Container
# startet -, und dann werden 19 GB neu von der Platte geladen. Auf dieser
# Maschine ist das der teuerste einzelne Posten ueberhaupt.
KEEP_ALIVE = os.environ.get("BRAUNY_KEEP_ALIVE", "2h")

def _ganzzahl(name: str, vorgabe: int) -> int:
    """Wie _seed_lesen: einmal beim Laden pruefen statt bei jedem Aufruf
    abzustuerzen."""
    roh = os.environ.get(name, "").strip()
    if not roh:
        return vorgabe
    try:
        wert = int(roh)
    except ValueError:
        import logging
        logging.getLogger(__name__).warning(
            "%s=%r ist keine ganze Zahl - es gilt %d.", name, roh, vorgabe)
        return vorgabe
    return wert if wert > 0 else vorgabe

NUM_CTX = _ganzzahl("BRAUNY_NUM_CTX", 8192)
NUM_PREDICT = _ganzzahl("BRAUNY_NUM_PREDICT", 1024)


def _ollama_messages(messages) -> list[dict]:
    """Den Verlauf in die Form bringen, die Ollama erwartet.

    Der Agent baut seinen Verlauf im OpenAI-Format - das ist richtig so, denn
    dasselbe Gespraech soll auch an eine fremde API gehen koennen. Nur passt
    dieses Format an zwei Stellen nicht auf /api/chat:

      arguments   OpenAI verlangt einen JSON-TEXT, Ollama ein OBJEKT. Wird ein
                  Text geschickt, antwortet Ollama mit 400.
      Zusatzfelder id, type und tool_call_id kennt Ollama nicht; der Name
                  eines Werkzeugergebnisses heisst dort tool_name.

    Das ist genau die Gegenrichtung des Fehlers, den _parse_arguments
    behandelt: dort kam ein Text, wo ein Objekt erwartet wurde. Beide Seiten
    der Uebersetzung gehoeren an dieselbe Stelle - die Grenze zum Anbieter.
    """
    raus = []
    for nachricht in messages:
        rolle = nachricht.get("role", "user")
        sauber: dict = {"role": rolle, "content": nachricht.get("content", "") or ""}

        if rolle == "tool":
            # Ollama fuehrt den Werkzeugnamen unter 'tool_name'.
            name = nachricht.get("tool_name") or nachricht.get("name")
            if name:
                sauber["tool_name"] = name

        aufrufe = nachricht.get("tool_calls")
        if aufrufe:
            sauber["tool_calls"] = [
                {"function": {
                    "name": (a.get("function") or {}).get("name", ""),
                    "arguments": _parse_arguments((a.get("function") or {}).get("arguments")),
                }}
                for a in aufrufe
            ]
        raus.append(sauber)
    return raus


def _ollama_optionen(policy: str) -> dict:
    """Die Stellschrauben, die auf einer CPU-Maschine ueber Minuten entscheiden.

    num_ctx  Ollama nimmt ohne Angabe 4096 Token. Systemanweisung plus
             Werkzeugbeschreibungen plus wachsender Verlauf sprengen das
             schnell - dann wird der Anfang abgeschnitten, und mit ihm die
             Anweisung, an die der Agent sich halten soll. Zu gross ist
             allerdings auch schaedlich: der Zwischenspeicher waechst
             linear mit und frisst genau den Speicher, den das Modell
             braucht.
    num_predict  Deckel gegen Ausreisser. Ein Werkzeugaufruf ist kurz; wer
             hier zweitausend Token schreibt, hat die Aufgabe ohnehin
             missverstanden, und auf CPU kostet jedes davon Sekunden.
    """
    optionen = {
        "temperature": _temperatur(policy),
        "num_ctx": NUM_CTX,
        "num_predict": NUM_PREDICT,
    }
    if SEED is not None:
        # Lokal und bekannt - hier ist ein fester Startwert unbedenklich.
        optionen["seed"] = SEED
    return optionen


def _fehler_aus_antwort(status: int, roh) -> ProviderError:
    """Ollama schreibt den Grund einer Ablehnung in den Rumpf.

    Ohne ihn steht in der Oberflaeche nur "400 Bad Request" - wahr, aber
    unbrauchbar. Genau daran ist hier schon einmal eine Stunde verlorengegangen.
    """
    grund = ""
    try:
        grund = str(json.loads(roh).get("error", "")).strip()
    except Exception:
        grund = str(roh)[:400].strip()
    return ProviderError(f"Ollama lehnt die Anfrage ab (HTTP {status})"
                         + (f": {grund}" if grund else "."))


def _chat_ollama(messages, tools, policy=DETERMINISTISCH, on_text=None):
    import httpx

    nutzlast = {
        "model": MODEL,
        "messages": _ollama_messages(messages),
        "options": _ollama_optionen(policy),
        # Immer stroemen. Nicht wegen der Geschwindigkeit - die aendert sich
        # dadurch nicht -, sondern weil sonst minutenlang nichts zu sehen ist
        # und niemand unterscheiden kann, ob gerechnet wird oder etwas haengt.
        "stream": True,
        # Der wichtigste Wert auf dieser Maschine. Ohne ihn wirft Ollama das
        # Modell nach fuenf Minuten Ruhe aus dem Speicher - und laedt beim
        # naechsten Schritt 19 GB von der Platte nach. Genau das passiert in
        # einer Werkzeugschleife staendig, weil zwischen zwei Modellaufrufen
        # ein Test laufen kann.
        "keep_alive": KEEP_ALIVE,
    }
    if tools:
        nutzlast["tools"] = tools

    text_teile: list[str] = []
    roh_calls: list[dict] = []
    messung: dict = {}

    try:
        with httpx.stream("POST", f"{OLLAMA_HOST}/api/chat", json=nutzlast,
                          timeout=httpx.Timeout(MODELL_TIMEOUT, connect=10.0)) as antwort:
            if antwort.status_code >= 400:
                antwort.read()
                raise _fehler_aus_antwort(antwort.status_code, antwort.text)

            for zeile in antwort.iter_lines():
                zeile = zeile.strip()
                if not zeile:
                    continue
                try:
                    stueck = json.loads(zeile)
                except json.JSONDecodeError:
                    # Eine kaputte Zeile mitten im Strom ist kein Grund, den
                    # ganzen bereits erzeugten Text wegzuwerfen.
                    continue

                if stueck.get("error"):
                    raise ProviderError(f"Ollama meldet: {stueck['error']}")

                nachricht = stueck.get("message") or {}
                neu = nachricht.get("content") or ""
                if neu:
                    text_teile.append(neu)
                    if on_text is not None:
                        try:
                            on_text(neu)
                        except Exception:
                            # Ein Fehler beim Anzeigen darf den Modellaufruf
                            # nicht mitreissen.
                            pass
                roh_calls.extend(nachricht.get("tool_calls") or [])

                if stueck.get("done"):
                    messung = {
                        "prompt_token": stueck.get("prompt_eval_count", 0),
                        "prompt_s": round(stueck.get("prompt_eval_duration", 0) / 1e9, 2),
                        "antwort_token": stueck.get("eval_count", 0),
                        "antwort_s": round(stueck.get("eval_duration", 0) / 1e9, 2),
                        "gesamt_s": round(stueck.get("total_duration", 0) / 1e9, 2),
                        "geladen_s": round(stueck.get("load_duration", 0) / 1e9, 2),
                    }
    except ProviderError:
        raise
    except httpx.HTTPError as exc:
        raise ProviderError(f"Ollama nicht erreichbar: {exc}") from exc

    text = "".join(text_teile)

    calls = []
    for item in roh_calls:
        function = (item or {}).get("function") or {}
        name = function.get("name") or ""
        # Ein Aufruf ohne Namen ist nicht ausfuehrbar. Ihn stillschweigend
        # zu uebergehen ist richtiger, als spaeter ueber einen leeren
        # Werkzeugnamen zu stolpern.
        if not name:
            continue
        calls.append(ToolCall(
            name=name,
            arguments=_parse_arguments(function.get("arguments")),
        ))
    return Reply(text=text, tool_calls=calls, messung=messung)


# ------------------------------------------------------------------ OpenAI-kompatibel

def _chat_openai(messages, tools, policy=DETERMINISTISCH):
    import httpx

    if not API_BASE:
        raise ProviderError("BRAUNY_API_BASE ist nicht gesetzt.")
    payload = {"model": MODEL, "messages": messages,
               "temperature": _temperatur(policy)}
    if SEED is not None:
        # Nur auf Ansage: nicht jeder Endpunkt kennt 'seed', und manche
        # weisen unbekannte Felder mit 400 zurueck.
        payload["seed"] = SEED
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

def chat(messages, tools=None, policy=DETERMINISTISCH, on_text=None) -> Reply:
    """Blockierender Modellaufruf. Aufrufer legen ihn in einen Thread.

    policy waehlt das Abtastverhalten: DETERMINISTISCH fuer alles, was genau
    einmal richtig sein muss - Werkzeugaufrufe, Plaene, Extraktion. VIELFALT
    nur dort, wo mehrere Vorschlaege verglichen werden sollen.
    """
    if PROVIDER == "ollama":
        return _chat_ollama(messages, tools, policy, on_text=on_text)
    if PROVIDER in OPENAI_ALIASES:
        return _chat_openai(messages, tools, policy)
    raise ProviderError(f"Unbekannter Anbieter: {PROVIDER!r}")


def health() -> tuple[bool, str]:
    """Leichte Pruefung fuer /healthz.

    Bei Ollama wird tatsaechlich nachgefragt - das ist lokal und gratis. Eine
    entfernte API wird NICHT angerufen: ein Health-Check darf keine Anfragen
    verbrauchen, die Geld kosten. Geprueft wird nur die Konfiguration, und
    genau so wird es auch gemeldet.
    """
    if PROVIDER == "ollama":
        # Auch hier ueber HTTP, aus demselben Grund wie bei _chat_ollama und
        # damit /healthz und der Modellaufruf dieselbe Verbindung pruefen.
        try:
            import httpx
            antwort = httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=5.0)
            antwort.raise_for_status()
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
