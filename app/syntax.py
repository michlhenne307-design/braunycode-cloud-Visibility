"""Syntaxpruefung fuer mehrere Sprachen, nicht nur Python.

Warum es das gibt: Der Agent konnte bisher ausschliesslich Python pruefen.
An einem TypeScript-Projekt hiess das Ergebnis jeder Aenderung "Belegt ist nur
die Syntax - der Code wurde nicht ausgefuehrt", und in Wahrheit war nicht
einmal die Syntax geprueft. Ein Agent, der nichts pruefen kann, weicht auf das
aus, was er pruefen kann - im Betrieb hat er an einer Next.js-Anwendung
angefangen, Python-Module zu importieren, bis der Lauf abbrach.

Das ist kein Modellfehler. Wer nur einen Hammer hat, sucht Naegel.

Grundsatz bleibt: Was nicht geprueft werden kann, wird auch nicht als geprueft
gemeldet. Eine ehrliche Fehlanzeige ist besser als ein erfundener Haken.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess

# Node wird nur einmal gesucht - ein which() bei jedem Aufruf waere Verschwendung.
NODE = shutil.which("node")
TIMEOUT = int(os.environ.get("BRAUNY_SYNTAX_TIMEOUT", "20"))


def _globaler_modulpfad() -> str:
    """Wo npm global installierte Module ablegt.

    Noetig, weil 'node -e' NICHT im globalen Ordner sucht: require() geht vom
    aktuellen Verzeichnis aus nach oben, und ein global installiertes
    typescript liegt in keinem dieser Verzeichnisse. Ohne NODE_PATH meldet die
    Pruefung deshalb ewig "Parser steht nicht bereit", obwohl er installiert
    ist - genau so ist es hier beim Bauen passiert.
    """
    vorgabe = os.environ.get("BRAUNY_NODE_PATH", "").strip()
    if vorgabe:
        return vorgabe
    npm = shutil.which("npm")
    if not npm:
        return ""
    try:
        fertig = subprocess.run([npm, "root", "-g"], capture_output=True,
                                text=True, timeout=TIMEOUT)
    except (subprocess.SubprocessError, OSError):
        return ""
    return fertig.stdout.strip() if fertig.returncode == 0 else ""


NODE_PATH = _globaler_modulpfad()

PYTHON = {".py", ".pyi"}
# Der TypeScript-Parser versteht alle vier Formen, auch reines JavaScript und
# JSX. Deshalb eine Liste statt vier Sonderwege.
TYPESCRIPT = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
JSON_ARTIG = {".json"}

# Nur parsen, NICHT typpruefen. Ein Typfehler braucht node_modules und die
# ganze Projektumgebung; ein Syntaxfehler braucht nichts davon. Vermischt man
# beides, meldet die Pruefung in jedem echten Projekt hunderte Fehler ueber
# fehlende Pakete - und wird deshalb ignoriert.
_TS_SKRIPT = r"""
const fs = require("fs");
let ts;
try { ts = require("typescript"); }
catch (e) { console.log(JSON.stringify({ok: null, grund: "typescript fehlt"})); process.exit(0); }
// Achtung: bei 'node -e' faengt argv NICHT bei [2] an wie bei einer
// Skriptdatei - dort steht [0] node und [1] bereits das erste Argument.
// Mit [2] ist die Datei undefined, readFileSync wirft, und die Pruefung
// meldete "Parser steht nicht bereit", obwohl alles installiert war.
const datei = process.argv[process.argv.length - 1];
const text = fs.readFileSync(datei, "utf8");
const jsx = /\.(tsx|jsx)$/.test(datei) ? ts.JsxEmit.Preserve : undefined;
const quelle = ts.createSourceFile(datei, text, ts.ScriptTarget.Latest, true,
  /\.(tsx|jsx)$/.test(datei) ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
const fehler = (quelle.parseDiagnostics || []).map(d => {
  const pos = quelle.getLineAndCharacterOfPosition(d.start);
  return {zeile: pos.line + 1, spalte: pos.character + 1,
          text: ts.flattenDiagnosticMessageText(d.messageText, " ")};
});
console.log(JSON.stringify({ok: fehler.length === 0, fehler: fehler.slice(0, 5)}));
"""


def _node(skript: str, *args) -> dict | None:
    """Fuehrt ein kleines Node-Skript aus und liest sein JSON.

    Gibt None zurueck, wenn node fehlt oder etwas Unerwartetes passiert -
    dann gilt die Sprache als ungeprueft, nicht als geprueft.
    """
    if not NODE:
        return None
    try:
        umgebung = {**os.environ}
        if NODE_PATH:
            vorhanden = umgebung.get("NODE_PATH", "")
            umgebung["NODE_PATH"] = (f"{vorhanden}{os.pathsep}{NODE_PATH}"
                                     if vorhanden else NODE_PATH)
        fertig = subprocess.run([NODE, "-e", skript, "--", *args], env=umgebung,
                                capture_output=True, text=True, timeout=TIMEOUT)
    except (subprocess.SubprocessError, OSError):
        return None
    if fertig.returncode != 0:
        return None
    try:
        return json.loads(fertig.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return None


def unterstuetzt(pfad: str) -> bool:
    """Kann diese Datei ueberhaupt geprueft werden?"""
    endung = os.path.splitext(pfad)[1].lower()
    if endung in PYTHON or endung in JSON_ARTIG:
        return True
    return endung in TYPESCRIPT and NODE is not None


def pruefen(pfad: str, quelltext: str, arbeitsdatei: str = "") -> tuple[bool | None, str]:
    """(bestanden, Meldung).

    bestanden ist None, wenn fuer diese Sprache keine Pruefung da ist. Genau
    diese dritte Moeglichkeit ist der Punkt: 'weiss nicht' ist ein Ergebnis
    und darf nicht als 'in Ordnung' durchgehen.
    """
    endung = os.path.splitext(pfad)[1].lower()

    if endung in PYTHON:
        try:
            ast.parse(quelltext)
        except SyntaxError as exc:
            return False, f"SyntaxError in {pfad}, Zeile {exc.lineno}: {exc.msg}"
        return True, f"{pfad}: Syntax in Ordnung."

    if endung in JSON_ARTIG:
        try:
            json.loads(quelltext)
        except json.JSONDecodeError as exc:
            return False, f"JSON-Fehler in {pfad}, Zeile {exc.lineno}: {exc.msg}"
        return True, f"{pfad}: Syntax in Ordnung."

    if endung in TYPESCRIPT:
        if not NODE:
            return None, (f"{pfad}: keine Prüfung möglich — node ist auf diesem "
                          "Server nicht installiert.")
        if not arbeitsdatei:
            return None, f"{pfad}: keine Prüfung möglich — Datei nicht auf der Platte."
        ergebnis = _node(_TS_SKRIPT, arbeitsdatei)
        if ergebnis is None or ergebnis.get("ok") is None:
            return None, (f"{pfad}: keine Prüfung möglich — der TypeScript-Parser "
                          "steht nicht bereit.")
        if ergebnis.get("ok"):
            return True, f"{pfad}: Syntax in Ordnung."
        zeilen = [f"  Zeile {f['zeile']}, Spalte {f['spalte']}: {f['text']}"
                  for f in ergebnis.get("fehler", [])]
        return False, f"SyntaxError in {pfad}:\n" + "\n".join(zeilen)

    return None, (f"{pfad}: keine Prüfung für '{endung or 'ohne Endung'}' "
                  "vorhanden — die Änderung ist damit nicht belegt.")


# --------------------------------------------------------------- Symbole

# Ein Prozess fuer ALLE Dateien, nicht einer je Datei. Ein Node-Start kostet
# rund 50 ms; bei zweihundert Dateien waeren das zehn Sekunden nur fuer das
# Hochfahren. Die Liste kommt ueber die Standardeingabe, weil eine
# Kommandozeile mit zweihundert Pfaden an die Laengengrenze stoesst.
_SYMBOL_SKRIPT = r"""
let ts;
try { ts = require("typescript"); }
catch (e) { console.log("[]"); process.exit(0); }
const fs = require("fs");

let roh = "";
process.stdin.on("data", d => roh += d);
process.stdin.on("end", () => {
  let dateien = [];
  try { dateien = JSON.parse(roh); } catch (e) { console.log("[]"); return; }
  const raus = [];

  for (const eintrag of dateien) {
    const [rel, absolut] = eintrag;
    let text;
    try { text = fs.readFileSync(absolut, "utf8"); } catch (e) { continue; }
    const tsx = /\.(tsx|jsx)$/.test(rel);
    const quelle = ts.createSourceFile(rel, text, ts.ScriptTarget.Latest, true,
      tsx ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
    if ((quelle.parseDiagnostics || []).length > 40) continue;

    const zeile = n => quelle.getLineAndCharacterOfPosition(n.getStart(quelle)).line + 1;
    const endzeile = n => quelle.getLineAndCharacterOfPosition(n.getEnd()).line + 1;
    const doku = n => {
      // Bei 'export const X = () => ...' haengt der Kommentar am STATEMENT,
      // nicht an der Deklaration. Ohne den Blick nach oben bliebe die
      // Beschreibung genau bei der haeufigsten React-Form leer.
      let traeger = n;
      for (let i = 0; i < 3 && traeger && !(traeger.jsDoc && traeger.jsDoc.length); i++) {
        traeger = traeger.parent;
      }
      const n2 = traeger || n;
      const j = n2.jsDoc && n2.jsDoc[0];
      // Kein "\n" im Quelltext dieses Skripts: es steht in einer
      // Python-Zeichenkette und wuerde dort zum echten Zeilenumbruch - mitten
      // in einem JS-String. Genau daran ist es beim Bauen gescheitert.
      return j && j.comment ? String(j.comment).split(/\r?\n/)[0].slice(0, 160) : "";
    };
    const params = n => !n.parameters ? "" :
      "(" + n.parameters.map(p => p.name && p.name.getText ? p.name.getText(quelle) : "?")
        .join(", ") + ")";

    const nimm = (knoten, kind, name, qual, sig) => {
      if (!name) return;
      raus.push({kind, name, qualname: qual || name, path: rel,
                 line: zeile(knoten), end_line: endzeile(knoten),
                 signature: sig || (name + params(knoten)), doc: doku(knoten)});
    };

    const besuche = (knoten, praefix) => {
      if (ts.isFunctionDeclaration(knoten) && knoten.name) {
        nimm(knoten, "function", knoten.name.text, knoten.name.text);
      } else if (ts.isClassDeclaration(knoten) && knoten.name) {
        const cname = knoten.name.text;
        nimm(knoten, "class", cname, cname, "class " + cname);
        for (const m of knoten.members || []) {
          if ((ts.isMethodDeclaration(m) || ts.isConstructorDeclaration(m)) && m.name) {
            const mname = m.name.getText ? m.name.getText(quelle) : "constructor";
            nimm(m, "method", mname, cname + "." + mname);
          }
        }
      } else if (ts.isInterfaceDeclaration(knoten) && knoten.name) {
        nimm(knoten, "interface", knoten.name.text, knoten.name.text,
             "interface " + knoten.name.text);
      } else if (ts.isTypeAliasDeclaration(knoten) && knoten.name) {
        nimm(knoten, "type", knoten.name.text, knoten.name.text,
             "type " + knoten.name.text);
      } else if (ts.isEnumDeclaration(knoten) && knoten.name) {
        nimm(knoten, "enum", knoten.name.text, knoten.name.text,
             "enum " + knoten.name.text);
      } else if (ts.isVariableStatement(knoten)) {
        // const Foo = () => ... ist in React die haeufigste Form einer
        // Komponente. Ohne diesen Zweig faende der Index in einer typischen
        // .tsx-Datei ueberhaupt nichts.
        for (const d of knoten.declarationList.declarations) {
          if (!d.name || !d.name.text) continue;
          const init = d.initializer;
          if (init && (ts.isArrowFunction(init) || ts.isFunctionExpression(init))) {
            nimm(d, "function", d.name.text, d.name.text, d.name.text + params(init));
          }
        }
      }
      ts.forEachChild(knoten, k => besuche(k, praefix));
    };
    ts.forEachChild(quelle, k => besuche(k, ""));
  }
  console.log(JSON.stringify(raus));
});
"""


def symbole(dateien: list[tuple[str, str]]) -> list[dict] | None:
    """Symbole aus TypeScript- und JavaScript-Dateien.

    dateien: Paare (Pfad im Projekt, Pfad auf der Platte).

    None bedeutet "nicht moeglich" - kein node, kein typescript. Auch hier
    gilt: lieber keine Angabe als eine erfundene.
    """
    if not NODE or not dateien:
        return None
    umgebung = {**os.environ}
    if NODE_PATH:
        vorhanden = umgebung.get("NODE_PATH", "")
        umgebung["NODE_PATH"] = (f"{vorhanden}{os.pathsep}{NODE_PATH}"
                                 if vorhanden else NODE_PATH)
    try:
        fertig = subprocess.run(
            [NODE, "-e", _SYMBOL_SKRIPT], env=umgebung,
            input=json.dumps(dateien), capture_output=True, text=True,
            timeout=max(TIMEOUT, 60))
    except (subprocess.SubprocessError, OSError):
        return None
    if fertig.returncode != 0:
        return None
    try:
        ergebnis = json.loads(fertig.stdout.strip() or "[]")
    except json.JSONDecodeError:
        return None
    return ergebnis if isinstance(ergebnis, list) else None
