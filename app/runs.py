"""Laeufe leben auf dem Server, nicht in der Verbindung.

Vorher trieb die WebSocket-Verbindung den Lauf an: Der Browser schickte den
Auftrag, und alles Weitere passierte innerhalb dieser einen Verbindung. Ging
sie verloren - Bildschirm aus, App gewechselt, Mobilfunk statt WLAN -, war
der Lauf weg. Auf einem Telefon ist das kein Randfall, sondern der Normalfall:
das Betriebssystem legt einen Browser im Hintergrund nach Sekunden schlafen.

Genau daran ist der erste echte Auftrag gescheitert. Der Server hatte sauber
gearbeitet, kein Speichermangel, kein Absturz - nur hatte niemand mehr
zugehoert, und mit dem Zuhoerer starb die Arbeit.

Hier ist ein Lauf deshalb ein eigenes Ding mit eigener Kennung. Die Verbindung
haengt sich nur an. Faellt sie weg, laeuft er weiter; kommt sie zurueck,
bekommt sie ab der Stelle weiter, an der sie war. Der Fortschritt wird nicht
neu erzaehlt und nicht doppelt.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field

# Ein Lauf ist durch MAX_STEPS begrenzt, seine Sandbox-Ausgabe aber nicht.
# Ein Programm in einer Schleife koennte den Speicher sonst vollschreiben.
MAX_EREIGNISSE = 4000
MAX_TEXT = 20000

# Wie viele Laeufe ueberhaupt aufbewahrt werden und wie lange ein fertiger
# noch abrufbar bleibt. Lang genug, um nach einer Bahnfahrt nachzusehen.
MAX_LAEUFE = 20
AUFBEWAHRUNG = 3600.0


@dataclass
class Lauf:
    id: str
    prompt: str
    gestartet: float
    ereignisse: list[dict] = field(default_factory=list)
    fertig: bool = False
    ok: bool | None = None
    beendet: float | None = None
    # Die ausfuehrende Aufgabe. Ohne sie waere der Abbruchknopf eine Luege:
    # das Schliessen der Verbindung beendet den Lauf ja gerade nicht mehr.
    aufgabe: asyncio.Task | None = None
    # Wird bei jedem neuen Ereignis gesetzt und sofort ersetzt. Wartende
    # Zuschauer haengen daran statt an einer Warteschlange pro Zuschauer -
    # so kostet ein zweiter Zuschauer nichts und keiner kann zurueckstauen.
    _weckruf: asyncio.Event = field(default_factory=asyncio.Event)

    def _wecken(self) -> None:
        self._weckruf.set()
        self._weckruf = asyncio.Event()

    def anhaengen(self, typ: str, text: str = "", **extra) -> None:
        """Ein Ereignis aufnehmen und alle Zuschauer wecken."""
        if self.fertig:
            return
        if len(self.ereignisse) >= MAX_EREIGNISSE:
            # Nicht still abschneiden: wer nachliest, muss erfahren, dass hier
            # etwas fehlt, sonst haelt er ein halbes Protokoll fuer ein ganzes.
            self.ereignisse.append({
                "type": "error",
                "text": f"Zu viele Meldungen ({MAX_EREIGNISSE}) — der Lauf wird "
                        "abgebrochen, damit der Server nicht vollaeuft.",
            })
            self.abschliessen(ok=False)
            return
        ereignis = {"type": typ, "text": str(text)[:MAX_TEXT], **extra}
        self.ereignisse.append(ereignis)
        if typ == "done":
            self.fertig = True
            self.ok = bool(extra.get("ok"))
            self.beendet = time.time()
        self._wecken()

    def abschliessen(self, ok: bool, text: str = "") -> None:
        """Abschluss von aussen - etwa wenn der Lauf mit einer Ausnahme endet."""
        if self.fertig:
            return
        self.ereignisse.append({
            "type": "done",
            "text": text or ("Fertig." if ok else "Fehlgeschlagen."),
            "ok": ok, "exit": 0 if ok else -1, "attempts": 0,
            "seconds": round(time.time() - self.gestartet, 1),
        })
        self.fertig = True
        self.ok = ok
        self.beendet = time.time()
        self._wecken()

    async def folgen(self, ab: int = 0):
        """Ereignisse ab Index 'ab' liefern und dann auf neue warten.

        Endet, wenn der Lauf fertig ist und nichts mehr aussteht. Ein
        Zuschauer, der spaeter dazukommt, sieht dadurch erst den ganzen
        bisherigen Verlauf und haengt sich dann nahtlos an.
        """
        i = max(0, int(ab))
        while True:
            while i < len(self.ereignisse):
                yield i, self.ereignisse[i]
                i += 1
            if self.fertig:
                return
            await self._weckruf.wait()

    def kopf(self) -> dict:
        """Kurzfassung fuer die Oberflaeche."""
        return {
            "id": self.id,
            "prompt": self.prompt,
            "fertig": self.fertig,
            "ok": self.ok,
            "ereignisse": len(self.ereignisse),
            "gestartet": self.gestartet,
        }


class Register:
    """Alle Laeufe dieses Prozesses.

    Bewusst nur im Arbeitsspeicher: ein Neustart des Dienstes beendet ohnehin
    jeden laufenden Auftrag, ein Protokoll davon auf Platte waere also die
    Erinnerung an etwas, das es nicht mehr gibt. Wer Dauerhaftigkeit will,
    braucht zuerst wiederaufnehmbare Laeufe - und die gibt es hier nicht.
    """

    def __init__(self) -> None:
        self._laeufe: dict[str, Lauf] = {}

    def starten(self, prompt: str) -> Lauf:
        self._aufraeumen()
        lauf = Lauf(id=secrets.token_urlsafe(9), prompt=prompt, gestartet=time.time())
        self._laeufe[lauf.id] = lauf
        return lauf

    def holen(self, lauf_id: str) -> Lauf | None:
        return self._laeufe.get(str(lauf_id))

    def offen(self) -> Lauf | None:
        """Der juengste noch laufende Auftrag, falls es einen gibt."""
        laufend = [l for l in self._laeufe.values() if not l.fertig]
        return max(laufend, key=lambda l: l.gestartet) if laufend else None

    def liste(self) -> list[dict]:
        return [l.kopf() for l in
                sorted(self._laeufe.values(), key=lambda l: l.gestartet, reverse=True)]

    def _aufraeumen(self) -> None:
        jetzt = time.time()
        # Laufende werden nie weggeraeumt, egal wie alt - sonst verschwaende
        # ein langer Auftrag genau dann, wenn er am wertvollsten ist.
        alt = [k for k, l in self._laeufe.items()
               if l.fertig and l.beendet and jetzt - l.beendet > AUFBEWAHRUNG]
        for k in alt:
            del self._laeufe[k]

        if len(self._laeufe) > MAX_LAEUFE:
            fertige = sorted((l for l in self._laeufe.values() if l.fertig),
                             key=lambda l: l.beendet or 0)
            for l in fertige[:len(self._laeufe) - MAX_LAEUFE]:
                del self._laeufe[l.id]


register = Register()
