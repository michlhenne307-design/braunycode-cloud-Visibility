# BraunyCode auf einem Hetzner-Server — komplett vom Handy

Diese Anleitung ist für den Fall gedacht, dass du **keinen Rechner** zur Hand
hast. Kein SSH, keine Kommandozeile, keine Tastenkombinationen. Du klickst den
Server zusammen, fügst einmal einen Textblock ein, wartest — und öffnest dann
eine Adresse im Browser.

Dauer: 5 Minuten Klicken, danach 15–25 Minuten Warten (Modell-Download).

---

## Warum Hetzner und nicht Oracle

Oracle Cloud ist kostenlos, hat aber zwei Hürden, an denen viele hängenbleiben:
die Kartenprüfung bei der Anmeldung, und danach die Meldung „Out of host
capacity" — die kostenlose ARM-Maschine ist meistens ausgebucht. Support gibt
es für nicht zahlende Accounts praktisch nicht.

Hetzner kostet ein paar Euro im Monat, ist dafür sofort da und akzeptiert
**PayPal und SEPA-Lastschrift** — die Kreditkarte, an der die Oracle-Anmeldung
scheitert, brauchst du hier nicht.

---

## Schritt 1 — Konto anlegen

**https://console.hetzner.com/** → *Register*

E-Mail bestätigen, Adresse eintragen, Zahlungsart wählen (PayPal oder
Lastschrift). Hetzner prüft neue Konten kurz manuell; das dauert meist Minuten,
gelegentlich ein paar Stunden.

## Schritt 2 — Projekt anlegen

Nach dem Einloggen: **New Project** → Name egal, z. B. `braunycode` → *Add
Project* → Projekt anklicken.

## Schritt 3 — Server erstellen

**Add Server**. Jetzt der Reihe nach:

| Feld | Auswahl |
|---|---|
| **Location** | `Nuremberg` oder `Falkenstein` (Deutschland) |
| **Image** | **Ubuntu 24.04** |
| **Type** | Reiter **Arm64 (Ampere)** → **CAX21** |
| **Networking** | **Public IPv4** muss angehakt sein |
| **SSH Keys** | überspringen — brauchst du nicht |
| **Volumes / Firewalls / Backups** | überspringen |

### Zur Größe

Das Modell braucht **mindestens 8 GB RAM** — darunter läuft es nicht.

- **CAX21** (4 Kerne ARM, 8 GB): die passende Wahl. Entspricht ungefähr dem,
  wofür BraunyCode gebaut wurde.
- **CAX31** (8 Kerne, 16 GB): spürbar schneller, wenn dir das den Aufpreis
  wert ist. Erst damit lohnt das 14B-Modell.
- **CAX11** (2 Kerne, 4 GB): **zu klein.** Nicht nehmen.

Den aktuellen Preis siehst du im Bestellvorgang direkt neben dem Typ — dort
steht auch, ob die IPv4-Adresse extra kostet. Prüf ihn dort, statt dich auf
Zahlen aus einer Anleitung zu verlassen.

## Schritt 3b (empfohlen) — Kostenlosen Namen für HTTPS holen

Überspringbar, aber lohnt sich: ohne HTTPS geht dein Passwort unverschlüsselt
über die Leitung, und die App bleibt ohne Offline-Hülle und Kopieren-Knopf.

Dauert eine Minute, kostet nichts, geht am Handy:

1. **https://www.duckdns.org** öffnen
2. Oben mit einem bestehenden Konto anmelden (GitHub, Google, Reddit …)
3. Wunschnamen eintragen, z. B. `meinbrauny` → **add domain**
4. Du hast jetzt `meinbrauny.duckdns.org`

Das Feld **current ip** füllst du gleich nach Schritt 5 mit der IP deines
Servers. Der Name ist danach sofort nutzbar.

> **Warum ein Name und nicht einfach die IP?** Let's Encrypt stellt keine
> Zertifikate auf IP-Adressen aus. Dienste wie `sslip.io`, die jede IP als
> Namen auflösen, wären naheliegend — sie stehen aber nicht auf der Public
> Suffix List. Let's Encrypt zählt dort die gesamte Domain als eine einzige
> mit 50 Zertifikaten pro Woche, geteilt mit allen Nutzern weltweit. Das
> Limit ist praktisch dauernd ausgeschöpft. `duckdns.org` steht auf der
> Liste, dort bekommt jede Unterdomain ein eigenes Kontingent.

## Schritt 4 — Das Startskript einfügen (der wichtige Teil)

Weiter unten auf derselben Seite: **Cloud config** aufklappen.

Dort hinein kommt der komplette Inhalt von
[`deploy/hetzner-cloud-init.yaml`](../deploy/hetzner-cloud-init.yaml).

> **Vorher genau eine Zeile ändern.** Etwa in der Mitte steht:
>
> ```
> BRAUNY_TOKEN=bitte-hier-ein-langes-passwort-eintragen
> ```
>
> Ersetze den Text hinter dem `=` durch dein eigenes Passwort, **mindestens
> 12 Zeichen**. Das ist das Passwort, mit dem du dich später in BraunyCode
> anmeldest. Merk es dir — danach steht es nur noch auf dem Server.
>
> Lässt du den Platzhalter stehen, bricht die Einrichtung absichtlich ab.
> Ein öffentlich erreichbarer Dienst mit einem Passwort, das in einer
> Anleitung steht, wäre offen für jeden.

**Wenn du Schritt 3b gemacht hast**, trag ein paar Zeilen tiefer noch deinen
Namen ein:

```
BRAUNY_DOMAIN=meinbrauny.duckdns.org
```

Leer lassen = nur HTTP. Port 8000 bleibt in beiden Fällen offen, ein
Fehlschlag beim Zertifikat sperrt dich also nie aus.

Dann: **Create & Buy now**.

## Schritt 5 — Warten

Der Server läuft nach ~30 Sekunden, aber die Einrichtung braucht **15–25
Minuten**. Das meiste davon ist der Modell-Download (~4,7 GB).

In der Übersicht steht jetzt die **IPv4-Adresse** deines Servers, etwa
`46.62.144.20`. Notier sie dir.

**Falls du DuckDNS benutzt:** Jetzt zurück auf duckdns.org, die IP ins Feld
**current ip** eintragen, **update ip** drücken. Am besten sofort — die
Einrichtung braucht den Namen gegen Ende, wenn sie das Zertifikat holt.

Ruf im Browser auf:

```
http://<DEINE-SERVER-IP>:8000
```

Mit DuckDNS zusätzlich, sobald HTTPS steht:

```
https://<DEIN-NAME>.duckdns.org
```

- **Seite lädt nicht / „Verbindung fehlgeschlagen"** → noch nicht fertig,
  in 5 Minuten nochmal.
- **Passwortfeld erscheint** → fertig. Dein Passwort eingeben.

## Schritt 6 — Als App aufs Handy

Adresse im Browser öffnen → Teilen- bzw. Menü-Symbol → **Zum Home-Bildschirm**.
Danach startet BraunyCode im Vollbild mit eigenem Icon, ohne Browserleiste.

---

## Wenn etwas nicht klappt

Du brauchst dafür **kein SSH**. Hetzner hat eine Konsole im Browser:
Server anklicken → Symbol **>_** oben rechts.

Du landest direkt als `root` auf der Maschine. Nützliche Befehle:

```bash
# Was ist bei der Einrichtung passiert?
tail -50 /var/log/braunycode-setup.log

# Läuft der Dienst?
systemctl status braunycode

# Sind Modell und Docker erreichbar?
curl -s localhost:8000/healthz
```

| Symptom | Ursache und Abhilfe |
|---|---|
| Log endet mit „ABBRUCH: kein eigenes Passwort" | Du hast den Platzhalter stehen lassen. In der Konsole: `nano /etc/braunycode.setup`, Passwort eintragen, mit `Strg+O`, `Enter`, `Strg+X` speichern, dann `/usr/local/bin/braunycode-setup.sh` |
| Seite lädt nach 30 Minuten immer noch nicht | `tail -50 /var/log/braunycode-setup.log` zeigt, wo es hängt |
| Statuspunkt in der Oberfläche ist rot | `curl -s localhost:8000/healthz` nennt unter `modell_backend` bzw. `docker` die Ursache |
| „Token abgelehnt" | Passwort stimmt nicht. Nachsehen: `grep BRAUNY_TOKEN /home/brauny/braunycode/brauny.env` |

## Kosten im Blick behalten

Der Server läuft und kostet, bis du ihn löschst. **Ausschalten reicht nicht** —
ein gestoppter Server kostet bei Hetzner weiter, weil die Ressourcen reserviert
bleiben.

Wenn du BraunyCode nicht mehr brauchst: Server anklicken → **Delete**. Willst
du den Stand behalten, vorher einen Snapshot anlegen (kostet wenig) oder die
Ergebnisse per `git_push`-Konnektor in ein Repository schieben.

---

## HTTPS nachträglich einschalten

Hast du Schritt 3b übersprungen und willst es doch: Namen bei DuckDNS holen,
Server-IP dort eintragen, dann in der Browser-Konsole (Server anklicken →
Symbol **>_**):

```bash
bash /opt/braunycode-src/deploy/enable-https.sh <DEIN-NAME>.duckdns.org
```

Danach die App noch einmal zum Home-Bildschirm hinzufügen, damit sie die
HTTPS-Adresse benutzt.

## Sicherheitshinweis, ehrlich gesagt

**Ohne HTTPS** gehen dein Passwort und dein Code unverschlüsselt über die
Leitung. Im Mobilfunknetz oder im heimischen WLAN ist das Risiko überschaubar,
in einem fremden offenen WLAN nicht. Außerdem fehlen dann zwei Dinge, die
Browser nur in einem sicheren Kontext erlauben: der **Service Worker** (die
Offline-Hülle der App) und der **Kopieren-Knopf**.

**Auch mit HTTPS** bleibt der Dienst öffentlich erreichbar — geschützt nur
durch dein Passwort. Nimm ein langes, und benutz es nirgends sonst. Wer es
hat, kann auf deinem Server Code ausführen.

Port 8000 bleibt auch nach dem Einschalten von HTTPS offen, damit ein Problem
mit dem Zertifikat dich nicht aussperrt. Wenn du das nicht willst, kannst du
ihn später schließen — dann ist HTTPS aber der einzige Weg hinein.
