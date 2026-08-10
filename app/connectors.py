"""Konnektoren - der einzige Weg des Agenten nach draussen.

Alles andere in BraunyCode ist abgeschottet: die Sandbox hat kein Netz, die
Werkzeuge kommen nicht aus dem Projektverzeichnis heraus. Hier wird diese
Grenze bewusst und eng geoeffnet.

Beide Konnektoren sind standardmaessig AUS. Ohne Konfiguration sieht das
Modell sie nicht einmal im Werkzeugverzeichnis - was es nicht kennt, kann es
nicht missbrauchen.

  fetch_url  BRAUNY_FETCH=1
  git_push   BRAUNY_GIT_REMOTE=... und BRAUNY_GIT_TOKEN=...

Zur Gefahr bei fetch_url: Auf einer Cloud-Maschine liegt unter
169.254.169.254 der Metadaten-Dienst des Anbieters - bei Oracle, AWS und
Google gleichermassen. Wer den abrufen kann, bekommt Zugangsdaten der Instanz.
Ein Modell, das eine Adresse aus einer Aufgabenbeschreibung uebernimmt, ist
genau der Angriffsweg. Deshalb wird jede Adresse aufgeloest und jede
resultierende IP geprueft, bevor irgendetwas verbunden wird.
"""

from __future__ import annotations

import html
import ipaddress
import os
import re
import socket
import subprocess
from urllib.parse import urlparse

FETCH_ENABLED = os.environ.get("BRAUNY_FETCH", "0").strip() == "1"
FETCH_TIMEOUT = int(os.environ.get("BRAUNY_FETCH_TIMEOUT", "20"))
FETCH_MAX_BYTES = int(os.environ.get("BRAUNY_FETCH_MAX_BYTES", "200000"))
# Proxy-Variablen werden bewusst NICHT beachtet: ein Proxy loest den Namen
# selbst noch einmal auf und koennte an der Vorabpruefung vorbei auf ein
# internes Ziel verbinden. Wer hinter einem Zwangsproxy sitzt, setzt das
# hier auf 1 - und nimmt die schwaechere Zusicherung in Kauf.
FETCH_TRUST_ENV = os.environ.get("BRAUNY_FETCH_TRUST_ENV", "0").strip() == "1"

GIT_REMOTE = os.environ.get("BRAUNY_GIT_REMOTE", "").strip()
GIT_TOKEN = os.environ.get("BRAUNY_GIT_TOKEN", "").strip()
GIT_USER = os.environ.get("BRAUNY_GIT_USER", "x-access-token").strip()
GIT_BRANCH_PREFIX = os.environ.get("BRAUNY_GIT_BRANCH_PREFIX", "brauny/").strip()
GIT_TIMEOUT = int(os.environ.get("BRAUNY_GIT_TIMEOUT", "120"))

ALLOWED_PORTS = (80, 443)
# Kein Schraegstrich: der Namensraum kommt aus GIT_BRANCH_PREFIX, ein
# Schraegstrich aus dem Fliesstext waere nur Zufall.
SAFE_REF = re.compile(r"[^A-Za-z0-9._-]+")
TAGS = re.compile(r"<[^>]+>")
DROP_BLOCKS = re.compile(r"<(script|style|head)\b.*?</\1>", re.S | re.I)
BLANKS = re.compile(r"\n{3,}")


class ConnectorError(RuntimeError):
    pass


def available() -> list[str]:
    """Namen der Konnektoren, die tatsaechlich konfiguriert sind."""
    namen = []
    if FETCH_ENABLED:
        namen.append("fetch_url")
    # Ein Token genuegt: geklonte Projektordner bringen ihren eigenen Remote
    # mit. BRAUNY_GIT_REMOTE braucht nur, wer den ganzen Arbeitsordner auf ein
    # festes Ziel schieben will.
    if GIT_TOKEN:
        namen.append("git_push")
    return namen


def describe() -> dict:
    """Fuer /healthz - ohne Token und ohne die volle Remote-URL."""
    return {
        "fetch_url": FETCH_ENABLED,
        "git_push": bool(GIT_TOKEN),
        "git_remote_host": urlparse(GIT_REMOTE).hostname if GIT_REMOTE else None,
    }


def scrub(text: str) -> str:
    """Entfernt den Git-Token aus beliebigem Text.

    Letzte Verteidigungslinie: git schreibt ihn normalerweise nicht in seine
    Ausgabe, aber eine Fehlermeldung mit eingebetteter URL waere genau der
    Fall, in dem er doch auftaucht - und die Ausgabe geht ans Modell und in
    die Oberflaeche.
    """
    if GIT_TOKEN and text:
        return text.replace(GIT_TOKEN, "***")
    return text or ""


# ------------------------------------------------------------------ fetch_url

def _resolve(host: str) -> set[str]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise ConnectorError(f"Adresse nicht auflösbar: {host} ({exc})") from None
    return {info[4][0] for info in infos}


def check_url(url: str, resolver=_resolve) -> str:
    """Prueft eine Adresse, bevor sie angefasst wird.

    Abgewiesen wird alles, was nicht oeffentlich erreichbar ist: 127.x,
    10.x, 192.168.x, 169.254.x (Metadaten-Dienst!), IPv6-Gegenstuecke,
    Multicast und reservierte Bereiche. Geprueft werden ALLE aufgeloesten
    Adressen, nicht nur die erste - ein Name kann auf mehrere zeigen.
    """
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https"):
        raise ConnectorError("Nur http und https sind erlaubt.")
    if not parsed.hostname:
        raise ConnectorError("Adresse ohne Hostnamen.")
    # parsed.port wirft ValueError bei 'https://beispiel.de:abc'. Ohne diesen
    # Fang verliesse ein roher ValueError die Konnektor-Grenze, statt als
    # verstaendliche Meldung beim Modell anzukommen.
    try:
        port = parsed.port
    except ValueError:
        raise ConnectorError("Ungültige Portangabe in der Adresse.") from None
    if port is not None and port not in ALLOWED_PORTS:
        raise ConnectorError(f"Port {port} ist nicht erlaubt "
                             f"(nur {' und '.join(map(str, ALLOWED_PORTS))}).")

    for adresse in resolver(parsed.hostname):
        ip = ipaddress.ip_address(adresse)
        if ip.is_multicast or not ip.is_global:
            raise ConnectorError(
                f"Adresse zeigt auf ein nicht-öffentliches Ziel ({adresse}). "
                "Interne Dienste und Cloud-Metadaten sind gesperrt.")
    return parsed.geturl()


PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
              "ALL_PROXY", "all_proxy")


def proxy_aktiv() -> bool:
    """Kann ein Proxy zwischen uns und dem Ziel stehen?

    Dann ist die Gegenstelle der Proxy - oft 127.0.0.1 - und nicht der
    Zielserver; die Peer-Pruefung wuerde jede Anfrage verwerfen.

    Standardmaessig ignoriert fetch die Proxy-Variablen (FETCH_TRUST_ENV=0).
    Das ist die sichere Einstellung: ein Proxy loest den Namen selbst noch
    einmal auf und koennte damit an der Pruefung vorbei auf ein internes Ziel
    verbinden. Auf einem gewoehnlichen Server gibt es ohnehin keinen Proxy.
    """
    return FETCH_TRUST_ENV and any(os.environ.get(name) for name in PROXY_VARS)


def peer_pruefen(antwort) -> None:
    """Prueft NACH dem Verbinden, mit wem tatsaechlich gesprochen wurde.

    check_url loest den Namen auf und prueft die Adressen - danach loest httpx
    aber selbst noch einmal auf. Zwischen beiden Aufloesungen kann sich die
    DNS-Antwort aendern (DNS-Rebinding) oder ein Round-Robin eine andere
    Adresse liefern. Die Vorabpruefung allein waere dann wirkungslos.

    Die Verbindung laesst sich so nicht verhindern - aber die Antwort muss
    nicht zurueckgegeben werden, und genau darauf kommt es an: ohne Rueckgabe
    erfaehrt das Modell den Inhalt des Metadaten-Dienstes nicht.

    Laesst sich die Gegenstelle nicht ermitteln, wird nicht blockiert - sonst
    waere der Konnektor von einem Implementierungsdetail von httpx abhaengig.

    Hinter einem Proxy ist die Gegenstelle der Proxy, haeufig 127.0.0.1. Die
    Pruefung wuerde dort JEDE Anfrage verwerfen und den Konnektor unbrauchbar
    machen, deshalb entfaellt sie. Der Schutz gegen Rebinding ist dann nur so
    gut wie die Vorabpruefung - das steht so in der README.
    """
    if proxy_aktiv():
        return
    try:
        stream = antwort.extensions.get("network_stream")
        gegenstelle = stream.get_extra_info("server_addr") if stream else None
        adresse = gegenstelle[0] if gegenstelle else None
    except Exception:
        return
    if not adresse:
        return
    try:
        ip = ipaddress.ip_address(adresse)
    except ValueError:
        return
    if ip.is_multicast or not ip.is_global:
        raise ConnectorError(
            f"Die Verbindung ging an ein nicht-öffentliches Ziel ({adresse}) — "
            "Antwort verworfen. Das deutet auf DNS-Rebinding hin.")


def to_text(body: str) -> str:
    """HTML grob in Text. Kein Parser, keine Abhaengigkeit - reicht zum Lesen."""
    ohne_bloecke = DROP_BLOCKS.sub(" ", body or "")
    roh = TAGS.sub(" ", ohne_bloecke)
    entschluesselt = html.unescape(roh)
    zeilen = [zeile.strip() for zeile in entschluesselt.splitlines()]
    return BLANKS.sub("\n\n", "\n".join(zeilen)).strip()


def fetch(url: str, resolver=_resolve) -> str:
    """Holt eine Seite als Text. Folgt bewusst KEINER Weiterleitung.

    Eine Weiterleitung koennte nach der Pruefung auf ein internes Ziel zeigen.
    Statt automatisch zu folgen, wird das Ziel gemeldet - beim naechsten
    Aufruf wird es normal mitgeprueft.
    """
    if not FETCH_ENABLED:
        raise ConnectorError("fetch_url ist nicht aktiviert (BRAUNY_FETCH=1).")
    import httpx

    geprueft = check_url(url, resolver)
    with httpx.Client(timeout=FETCH_TIMEOUT, follow_redirects=False,
                      trust_env=FETCH_TRUST_ENV) as client:
        with client.stream("GET", geprueft,
                           headers={"User-Agent": "BraunyCode"}) as antwort:
            # Zweite Verteidigungslinie gegen DNS-Rebinding: erst pruefen,
            # mit wem wir wirklich reden, dann irgendetwas zurueckgeben.
            peer_pruefen(antwort)
            if 300 <= antwort.status_code < 400:
                ziel = antwort.headers.get("location", "(unbekannt)")
                return (f"Weiterleitung {antwort.status_code} nach: {ziel}\n"
                        "Nicht automatisch gefolgt. Rufe fetch_url erneut mit "
                        "dieser Adresse auf, wenn du sie brauchst.")
            if antwort.status_code >= 400:
                raise ConnectorError(f"Server antwortete mit "
                                     f"{antwort.status_code}.")
            teile, groesse = [], 0
            for stueck in antwort.iter_bytes():
                teile.append(stueck)
                groesse += len(stueck)
                if groesse >= FETCH_MAX_BYTES:
                    break
    roh = b"".join(teile)[:FETCH_MAX_BYTES].decode("utf-8", errors="replace")
    return to_text(roh) if "<" in roh[:2000] else roh.strip()


# ------------------------------------------------------------------ git_push

def safe_branch(name: str) -> str:
    """Macht aus beliebigem Text einen brauchbaren Branch-Namen.

    Git lehnt Referenzen mit '..' ab und mag kein '.lock' am Ende - beides
    wird hier weggeraeumt, sonst scheitert erst der Push.
    """
    sauber = SAFE_REF.sub("-", (name or "").strip().lower())
    sauber = re.sub(r"-{2,}", "-", sauber)
    sauber = re.sub(r"\.{2,}", ".", sauber)[:60].strip("-._")
    if sauber.endswith(".lock"):
        sauber = sauber[:-5].strip("-._")
    return sauber or "arbeit"


def _run(args, cwd, env=None):
    ergebnis = subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                              text=True, timeout=GIT_TIMEOUT)
    return ergebnis.returncode, scrub(ergebnis.stdout + ergebnis.stderr)


def _projekt_remote(ws, projekt: str) -> tuple[str, str]:
    """Ein geklontes Teilprojekt im Arbeitsordner und sein eigener Remote.

    Der Grund: Ein Arbeitsordner enthaelt mehrere Projekte, und jedes gehoert
    in SEIN Repository. Alles gemeinsam auf einen festen Remote zu schieben
    waere fuer genau einen Fall richtig und fuer alle anderen falsch.

    Der Ordnername kommt vom Modell. Er wird deshalb ueber ws.resolve gefuehrt,
    das den Arbeitsordner nicht verlaesst - sonst waere '../../..' ein Weg zu
    jedem Git-Projekt auf der Maschine.
    """
    ordner = ws.resolve(projekt)
    if not (ordner / ".git").exists():
        raise ConnectorError(
            f"'{projekt}' ist kein Git-Projekt. Nur ein geklonter Ordner mit "
            "eigenem Remote kann einzeln gepusht werden.")

    code, ausgabe = _run(["git", "remote", "get-url", "origin"], str(ordner))
    if code != 0 or not ausgabe.strip():
        raise ConnectorError(f"'{projekt}' hat keinen Remote 'origin'.")
    remote = ausgabe.strip().splitlines()[0].strip()

    # Steckt im origin bereits ein Token, liegt es dauerhaft in .git/config.
    # Dann wird nicht heimlich damit gepusht, sondern gesagt, was los ist.
    zerlegt = urlparse(remote)
    if zerlegt.scheme in ("http", "https") and (zerlegt.username or zerlegt.password):
        raise ConnectorError(
            f"Der Remote von '{projekt}' enthält Zugangsdaten in der URL. "
            "Bitte bereinigen — das Token gehört in BRAUNY_GIT_TOKEN.")
    return str(ordner), remote


def _projekt_push(ws, projekt: str, branch: str, message: str) -> str:
    """Committet und pusht EIN geklontes Teilprojekt auf dessen eigenen Remote."""
    wurzel, remote = _projekt_remote(ws, projekt)

    _run(["git", "add", "-A"], wurzel)
    # Ohne Autor bricht git ab, wenn auf der Maschine keiner gesetzt ist.
    code, ausgabe = _run(
        ["git", "-c", "user.name=BraunyCode", "-c", "user.email=brauny@localhost",
         "commit", "-m", message or "Änderung durch BraunyCode"], wurzel)
    # "nothing to commit" ist kein Fehler - dann gibt es schlicht nichts zu tun.
    if code != 0 and "nothing to commit" not in ausgabe:
        raise ConnectorError(f"Commit fehlgeschlagen: {ausgabe.strip()[:300]}")
    nichts_neues = "nothing to commit" in ausgabe

    ziel = GIT_BRANCH_PREFIX + safe_branch(branch or message or "arbeit")
    helper = ('!f() { echo username=$BRAUNY_GIT_USER; '
              'echo password=$BRAUNY_GIT_TOKEN; }; f')
    env = {**os.environ,
           "GIT_TERMINAL_PROMPT": "0",
           "GIT_ASKPASS": "",
           "BRAUNY_GIT_USER": GIT_USER,
           "BRAUNY_GIT_TOKEN": GIT_TOKEN}

    code, ausgabe = _run(
        ["git", "-c", f"credential.helper={helper}", "push", "--force-with-lease",
         remote, f"HEAD:refs/heads/{ziel}"], wurzel, env)
    if code != 0:
        raise ConnectorError(f"Push fehlgeschlagen: {ausgabe.strip()[:400]}")

    host = urlparse(remote).hostname or "Remote"
    hinweis = " (keine neuen Änderungen)" if nichts_neues else ""
    return (f"'{projekt}' auf {host} gepusht, Branch '{ziel}'{hinweis}.\n"
            f"{ausgabe.strip()[:400]}")


def git_push(ws, branch: str = "", message: str = "", projekt: str = "") -> str:
    """Schiebt den Projektstand auf den konfigurierten Remote.

    Der Token steht nur in der Umgebung des Kindprozesses und wird ueber einen
    credential.helper gelesen - nie in der Kommandozeile (dort waere er fuer
    jeden sichtbar, der 'ps' aufruft) und nie in .git/config (dort bliebe er
    auf der Platte stehen).
    """
    if not GIT_TOKEN:
        raise ConnectorError("git_push ist nicht konfiguriert (BRAUNY_GIT_TOKEN).")

    # Ein benannter Projektordner geht auf SEINEN eigenen Remote. Das ist der
    # Normalfall, sobald mehr als ein Projekt im Arbeitsordner liegt.
    if projekt.strip():
        return _projekt_push(ws, projekt.strip(), branch, message)

    if not GIT_REMOTE:
        raise ConnectorError(
            "Ohne Projektangabe braucht es BRAUNY_GIT_REMOTE. Oder nenne den "
            "Projektordner - dann wird dessen eigener Remote benutzt.")
    # Der Remote wird woertlich in .git/config geschrieben. Steckt darin ein
    # Token wie https://ghp_xyz@host/repo.git, liegt es dauerhaft auf der
    # Platte - und scrub() entfernt nur BRAUNY_GIT_TOKEN, nicht dieses.
    # Zugangsdaten kommen ausschliesslich aus BRAUNY_GIT_TOKEN.
    zerlegt = urlparse(GIT_REMOTE)
    if zerlegt.scheme in ("http", "https") and (zerlegt.username or zerlegt.password):
        raise ConnectorError(
            "BRAUNY_GIT_REMOTE enthält Zugangsdaten in der URL. Bitte die "
            "reine Adresse eintragen — das Token gehört in BRAUNY_GIT_TOKEN.")
    if not ws.git_ready():
        raise ConnectorError("Kein Git-Projekt vorhanden.")

    if message:
        ws.git_commit(message)
    if not ws.git_log(1):
        raise ConnectorError("Nichts zu übertragen — das Projekt hat noch "
                             "keinen Commit.")

    ziel = GIT_BRANCH_PREFIX + safe_branch(branch or message or "arbeit")
    wurzel = str(ws.root)

    # Remote ohne Token eintragen. set-url schlaegt fehl, wenn es ihn noch
    # nicht gibt - dann anlegen.
    code, _ = _run(["git", "remote", "set-url", "brauny", GIT_REMOTE], wurzel)
    if code != 0:
        _run(["git", "remote", "add", "brauny", GIT_REMOTE], wurzel)

    helper = ('!f() { echo username=$BRAUNY_GIT_USER; '
              'echo password=$BRAUNY_GIT_TOKEN; }; f')
    env = {**os.environ,
           "GIT_TERMINAL_PROMPT": "0",
           "GIT_ASKPASS": "",
           "BRAUNY_GIT_USER": GIT_USER,
           "BRAUNY_GIT_TOKEN": GIT_TOKEN}

    code, ausgabe = _run(
        ["git", "-c", f"credential.helper={helper}", "push", "--force-with-lease",
         "brauny", f"HEAD:refs/heads/{ziel}"], wurzel, env)
    if code != 0:
        raise ConnectorError(f"Push fehlgeschlagen: {ausgabe.strip()[:400]}")

    host = urlparse(GIT_REMOTE).hostname or "Remote"
    return f"Auf {host} gepusht, Branch '{ziel}'.\n{ausgabe.strip()[:400]}"
