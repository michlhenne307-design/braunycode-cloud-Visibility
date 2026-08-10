# Sandbox-Image fuer BraunyCode.
#
# Warum ueberhaupt ein eigenes Image: Der Container laeuft mit abgeschaltetem
# Netzwerk. Was drin ist, ist drin - nachinstallieren kann er nichts. Ohne
# Testlaeufer und ohne Hypothesis bleibt dem Agenten nur "Datei parst", und
# genau das ist der schwaechste aller Belege.
#
# ruff ist mit 27 MB der groesste Brocken hier, verdient den Platz aber: es
# findet undefinierte Namen in Millisekunden, OHNE etwas auszufuehren. Genau
# diese Fehlerklasse produziert ein kleines Modell am haeufigsten, und sie
# jetzt zu finden ist billiger als ein Containerstart, der daran scheitert.
#
# Was NICHT hineinkommt und warum:
#
#   mypy    - 19 MB, aber nur nuetzlich, wenn ein Projekt Typannotationen
#             pflegt. Der Parser dafuer ist trotzdem da: bringt ein Projekt
#             sein eigenes mypy mit, werden dessen Befunde verstanden.
#   mutmut  - zieht textual, rich und markdown-it-py mit, einen kompletten
#             Terminal-UI-Stapel. In einem Container ohne Netz und ohne
#             Terminal ist das Ballast: 17 Pakete statt 6. Mutationstests
#             gehoeren ausserdem nicht in die Schleife nach jeder Aenderung,
#             sondern seltener und ausserhalb.
#   Node und TypeScript sind KEIN Beiwerk: ohne sie kann der Agent an einem
#   JavaScript- oder TypeScript-Projekt nichts belegen - kein Syntaxtest,
#   kein Testlauf, kein Typcheck. Er weicht dann auf Python aus, weil das das
#   Einzige ist, was er pruefen kann, und liefert Behauptungen statt Belegen.
#
#   requests, numpy, ... - der Agent soll gegen die Standardbibliothek
#             arbeiten. Jede zusaetzliche Bibliothek ist eine Annahme darueber,
#             was das Projekt benutzt, und liegt bei einem fremden Projekt
#             meistens daneben.

# Node kommt aus dem offiziellen Image statt aus apt: das spart den halben
# Paketmanager und liefert eine aktuelle Fassung. Kopiert werden nur die
# Laufzeit und npm - beides laeuft gegen die glibc, die hier ohnehin liegt.
FROM node:20-slim AS node

FROM python:3.11-slim
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# Bereichsgrenzen statt exakter Pins: eine Obergrenze haelt einen
# ueberraschenden Hauptversionssprung heraus, eine Untergrenze sichert die
# benoetigten Funktionen. Das ist KEIN Lockfile - vollstaendige
# Reproduzierbarkeit braeuchte eine gepinnte Anforderungsdatei mit Hashes.
# Solange das Image lokal gebaut und nicht verteilt wird, waere das mehr
# Maschinerie, als es hier traegt.
# TypeScript global: der Container laeuft ohne Netz, also muss alles, was
# geprueft werden soll, schon drin sein. Ohne tsc bliebe bei einem
# TypeScript-Projekt nur "sieht gut aus" - und genau das ist kein Beleg.
RUN npm install -g --no-fund --no-audit typescript@5 \
 && npm cache clean --force

RUN pip install --no-cache-dir --root-user-action=ignore \
      "pytest>=8,<10" \
      "hypothesis>=6,<7" \
      "ruff>=0.6,<1" \
 && find /usr/local -name '__pycache__' -type d -prune -exec rm -rf {} + \
 && rm -rf /root/.cache

# Der Container wird vom Aufrufer ohnehin mit uid 65534, ohne Capabilities und
# nur-lesend eingehaengtem Projekt gestartet - siehe app/sandbox.py. Hier wird
# bewusst KEIN USER gesetzt, damit diese Entscheidung an einer Stelle bleibt
# und nicht an zweien auseinanderlaeuft.

# Ein Selbsttest zur Bauzeit: schlaegt der Import fehl, ist das Image kaputt
# und der Fehler faellt beim Bauen auf statt beim ersten Agentenlauf.
RUN python -c "import pytest, hypothesis; print(pytest.__version__, hypothesis.__version__)" \
 && ruff --version \
 && node --version \
 && npm --version \
 && tsc --version
