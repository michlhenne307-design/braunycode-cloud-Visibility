/* BraunyCode - Frontend ohne Build-Schritt.

   Aufbau: ein fortlaufender Gespraechsverlauf. Der Auftrag steht als eigener
   Beitrag darin, darunter waechst die Arbeit des Agenten mit.

   Werkzeugaufrufe werden ZUGEKLAPPT gezeigt, ihre Ausgabe steckt darin. Im
   Normalfall interessiert nur das Ergebnis; geht etwas schief, ist der Weg
   einen Fingertipp entfernt. Die Belegzeilen am Ende eines Laufs
   ("Geaendert:", "Belegt durch:") bleiben dagegen IMMER sichtbar - sie sind
   der Grund, warum es dieses Programm gibt, und gehoeren nicht in eine
   Klappe.

   Modell-Ausgaben werden ausschliesslich per textContent gesetzt, niemals
   per innerHTML - sonst waere jeder erzeugte String ein XSS-Vektor. */
'use strict';

const $ = id => document.getElementById(id);
const TOKEN_KEY = 'brauny.token';
const HIST_KEY = 'brauny.history';
const HIST_MAX = 20;

const VORSCHLAEGE = [
  'Schreibe rechner.py mit addiere(a, b) und einem Test dazu. Führe den Test aus.',
  'Lies das Projekt und sag mir in fünf Sätzen, was es tut.',
  'Finde die Stelle, an der Eingaben geprüft werden, und melde Lücken.',
];

let ws = null;
let running = false;
let startedAt = 0;
let turn = null;        // aktueller Agenten-Beitrag
let schritt = null;     // offener Werkzeugaufruf (details-Element)
let ausgabe = null;     // laufender Ausgabeblock

/* ----------------------------------------------------------- Hilfsmittel */

function toast(text) {
  const el = $('toast');
  el.textContent = text;
  el.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove('show'), 1900);
}

function amEnde() {
  const s = $('stream');
  return s.scrollHeight - s.scrollTop - s.clientHeight < 90;
}

function nachUnten(erzwingen) {
  const s = $('stream');
  if (erzwingen || amEnde()) s.scrollTop = s.scrollHeight;
}

function el(tag, klasse, text) {
  const node = document.createElement(tag);
  if (klasse) node.className = klasse;
  if (text !== undefined) node.textContent = text;
  return node;
}

/* Haengt einen Knoten in den laufenden Beitrag - vor die Tippanzeige, damit
   die immer unten bleibt. */
function anhaengen(node) {
  if (!turn) return;
  const unten = amEnde();
  const punkte = turn.querySelector('.thinking');
  if (punkte) turn.insertBefore(node, punkte);
  else turn.appendChild(node);
  nachUnten(unten);
}

/* -------------------------------------------------------------- Beitraege */

function leerenAusblenden() {
  const leer = $('empty');
  if (leer) leer.remove();
}

function beitragNutzer(text) {
  leerenAusblenden();
  const t = el('div', 'turn user');
  t.appendChild(el('div', 'bubble', text));
  $('stream').appendChild(t);
  nachUnten(true);
}

function beitragAgentBeginnen() {
  turn = el('div', 'turn agent');
  const punkte = el('div', 'thinking');
  punkte.appendChild(el('i'));
  punkte.appendChild(el('i'));
  punkte.appendChild(el('i'));
  turn.appendChild(punkte);
  $('stream').appendChild(turn);
  schritt = null;
  ausgabe = null;
  nachUnten(true);
}

function beitragAgentBeenden() {
  if (!turn) return;
  const punkte = turn.querySelector('.thinking');
  if (punkte) punkte.remove();
  turn = null;
  schritt = null;
  ausgabe = null;
}

function sagen(text, art) {
  if (!text) return;
  anhaengen(el('div', 'say' + (art ? ' ' + art : ''), text));
}

function werkzeug(text) {
  // Ein neuer Aufruf beendet den vorigen und den laufenden Ausgabeblock.
  schritt = null;
  ausgabe = null;

  const d = document.createElement('details');
  d.className = 'step';
  const s = document.createElement('summary');
  s.appendChild(el('span', 'glyph', '⚙'));
  s.appendChild(el('span', 'what', text));
  d.appendChild(s);
  d.appendChild(el('div', 'body', ''));
  anhaengen(d);
  schritt = d;
}

function ausgabezeile(text) {
  // Laeuft gerade ein Werkzeug, gehoert seine Ausgabe hinein.
  if (schritt) {
    const body = schritt.querySelector('.body');
    body.textContent += (body.textContent ? '\n' : '') + text;
    if (/fehler|error|traceback|failed/i.test(text)) schritt.classList.add('fail');
    nachUnten();
    return;
  }
  if (!ausgabe) {
    ausgabe = el('div', 'block out');
    const kopf = el('div', 'head');
    kopf.appendChild(el('span', null, 'Ausgabe'));
    ausgabe.appendChild(kopf);
    ausgabe.appendChild(el('pre', null, ''));
    anhaengen(ausgabe);
  }
  const pre = ausgabe.querySelector('pre');
  pre.textContent += (pre.textContent ? '\n' : '') + text;
  nachUnten();
}

function codeblock(text, pfad, versuch) {
  schritt = null;
  ausgabe = null;

  const b = el('div', 'block');
  const kopf = el('div', 'head');
  let titel = pfad || 'main.py';
  if (versuch && versuch > 1) titel += ' · Versuch ' + versuch;
  kopf.appendChild(el('span', null, titel));

  const knopf = el('button', null, 'Kopieren');
  knopf.type = 'button';
  knopf.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(text);
      toast('Code kopiert');
    } catch {
      toast('Kopieren nicht erlaubt — braucht HTTPS');
    }
  });
  kopf.appendChild(knopf);

  b.appendChild(kopf);
  b.appendChild(el('pre', null, text));
  anhaengen(b);
}

function ergebnis(ev) {
  schritt = null;
  ausgabe = null;

  const gut = !!ev.ok;
  const v = el('div', 'verdict ' + (gut ? 'ok' : 'fail'));
  v.appendChild(el('span', 'glyph', gut ? '✓' : '✗'));
  v.appendChild(el('span', null, ev.text || (gut ? 'Fertig.' : 'Fehlgeschlagen.')));

  const sek = typeof ev.seconds === 'number'
    ? ev.seconds.toFixed(1)
    : (startedAt ? ((Date.now() - startedAt) / 1000).toFixed(1) : null);
  if (sek !== null) v.appendChild(el('span', 'when', sek + ' s'));
  anhaengen(v);
  return sek;
}

/* ----------------------------------------------------------- Zustand */

function setRunning(on) {
  running = on;
  document.body.classList.toggle('running', on);
  $('btn-run').setAttribute('aria-label', on ? 'Abbrechen' : 'Auftrag starten');
  if (on) setHealth('busy', 'Agent arbeitet …');
  else checkHealth();
}

function setHealth(state, text) {
  $('health').className = 'dot ' + state;
  $('health-text').textContent = text;
}

async function checkHealth() {
  if (running) return;
  try {
    const res = await fetch('/healthz', { cache: 'no-store' });
    const data = await res.json();
    if (res.ok) {
      setHealth('ok', 'Bereit');
      $('hint-model').textContent = data.model || '—';
    } else {
      // Auf "fehler:" pruefen statt auf "ok": eine konfigurierte API meldet
      // "konfiguriert (nicht angefragt)" und ist damit nicht kaputt.
      const labels = { modell_backend: 'Modell', docker: 'Docker' };
      const kaputt = Object.keys(labels)
        .filter(k => String(data[k] || '').startsWith('fehler'))
        .map(k => labels[k]);
      setHealth('bad', 'Problem: ' + (kaputt.join(', ') || 'unbekannt'));
    }
  } catch {
    setHealth('bad', 'Server nicht erreichbar');
  }
}

/* ----------------------------------------------------------- Verlauf */

function loadHistory() {
  try { return JSON.parse(localStorage.getItem(HIST_KEY)) || []; }
  catch { return []; }
}

function addHistory(entry) {
  const list = loadHistory();
  list.unshift(entry);
  localStorage.setItem(HIST_KEY, JSON.stringify(list.slice(0, HIST_MAX)));
}

function renderHistory() {
  const ul = $('hist-list');
  ul.textContent = '';
  const list = loadHistory();
  if (!list.length) {
    ul.appendChild(el('li', null, 'Noch keine Läufe.'));
    return;
  }
  list.forEach(item => {
    const li = el('li', null, item.prompt);
    const when = el('div', 'when');
    when.appendChild(el('span', item.ok ? 'ok' : 'fail',
                        item.ok ? '✓ erfolgreich' : '✗ fehlgeschlagen'));
    when.appendChild(document.createTextNode(
      ' · ' + new Date(item.at).toLocaleString('de-DE') + ' · ' + item.seconds + ' s'));
    li.appendChild(when);
    li.addEventListener('click', () => {
      $('prompt').value = item.prompt;
      hoeheAnpassen();
      $('history').hidden = true;
      $('prompt').focus();
    });
    ul.appendChild(li);
  });
}

/* ----------------------------------------------------------- Lauf */

function start() {
  const prompt = $('prompt').value.trim();
  if (!prompt) { toast('Bitte einen Auftrag eingeben'); return; }
  if (!localStorage.getItem(TOKEN_KEY)) { openGate(); return; }

  const token = localStorage.getItem(TOKEN_KEY) || '';
  $('prompt').value = '';
  hoeheAnpassen();

  beitragNutzer(prompt);
  beitragAgentBeginnen();
  startedAt = Date.now();
  setRunning(true);

  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${scheme}://${location.host}/ws/agent`);
  ws.onopen = () => ws.send(JSON.stringify({ token, prompt }));
  ws.onmessage = e => handleEvent(e.data, prompt);
  ws.onerror = () => sagen(
    'Verbindung unterbrochen. Der Lauf auf dem Server kann weitergelaufen sein.',
    'error');
  ws.onclose = () => { ws = null; beitragAgentBeenden(); setRunning(false); };
}

function stop() {
  if (ws) { ws.close(); ws = null; }
  sagen('Vom Benutzer abgebrochen.', 'error');
  beitragAgentBeenden();
  setRunning(false);
}

function handleEvent(raw, prompt) {
  let ev;
  try { ev = JSON.parse(raw); }
  catch { sagen(String(raw), 'status'); return; }

  switch (ev.type) {
    case 'tool':
      werkzeug(ev.text || '');
      break;
    case 'code':
      codeblock(ev.text || '', ev.path, ev.attempt);
      break;
    case 'sandbox':
      (ev.text || '').split('\n').forEach(ausgabezeile);
      break;
    case 'plan':
      sagen(ev.text || '');
      break;
    case 'error':
      schritt = null;
      sagen(ev.text || '', 'error');
      if (ev.code === 'auth') {
        localStorage.removeItem(TOKEN_KEY);
        openGate('Token abgelehnt. Bitte erneut eingeben.');
      }
      break;
    case 'done': {
      const sek = ergebnis(ev);
      addHistory({ prompt, ok: !!ev.ok, at: Date.now(), seconds: sek || '?' });
      break;
    }
    default:
      // status und alles Unbekannte: als schlichte Zeile, niemals versteckt.
      schritt = null;
      sagen(ev.text || '', 'status');
  }
}

/* ----------------------------------------------------------- Anmeldung */

function openGate(note) {
  $('gate').hidden = false;
  if (note) $('gate').querySelector('p').textContent = note;
  setTimeout(() => $('token').focus(), 120);
}

function saveToken() {
  const value = $('token').value.trim();
  if (!value) { toast('Token fehlt'); return; }
  localStorage.setItem(TOKEN_KEY, value);
  $('token').value = '';
  $('gate').hidden = true;
  toast('Angemeldet');
  checkHealth();
}

/* ----------------------------------------------------------- Eingabefeld */

function hoeheAnpassen() {
  const t = $('prompt');
  t.style.height = 'auto';
  t.style.height = Math.min(t.scrollHeight, window.innerHeight * 0.38) + 'px';
}

function vorschlaegeBauen() {
  const box = $('chips');
  if (!box) return;
  VORSCHLAEGE.forEach(text => {
    const b = el('button', 'chip', text);
    b.type = 'button';
    b.addEventListener('click', () => {
      $('prompt').value = text;
      hoeheAnpassen();
      $('prompt').focus();
    });
    box.appendChild(b);
  });
}

/* ----------------------------------------------------------- Verdrahtung */

$('composer').addEventListener('submit', e => {
  e.preventDefault();
  if (running) stop(); else start();
});

$('prompt').addEventListener('input', hoeheAnpassen);
$('prompt').addEventListener('keydown', e => {
  // Zeilenumbruch mit Umschalt, Absenden mit Eingabe - aber nur dort, wo es
  // eine echte Tastatur gibt. Auf Beruehrungsgeraeten ist die Eingabetaste
  // der einzige Weg zu einem Absatz.
  if (e.key !== 'Enter' || e.shiftKey) return;
  if (e.metaKey || e.ctrlKey || !matchMedia('(pointer: coarse)').matches) {
    e.preventDefault();
    if (!running) start();
  }
});

$('btn-new').addEventListener('click', () => {
  if (running) { toast('Erst den laufenden Auftrag beenden'); return; }
  const s = $('stream');
  s.textContent = '';
  const leer = el('div', 'welcome');
  leer.id = 'empty';
  leer.appendChild(el('div', 'mark', 'B'));
  leer.appendChild(el('h1', null, 'Was soll ich bauen?'));
  leer.appendChild(el('p', null,
    'Ich lese, schreibe und führe Code aus — und belege jede Änderung mit einem Lauf.'));
  const box = el('div', 'chips');
  box.id = 'chips';
  leer.appendChild(box);
  s.appendChild(leer);
  vorschlaegeBauen();
});

$('btn-history').addEventListener('click', () => { renderHistory(); $('history').hidden = false; });
$('btn-hist-close').addEventListener('click', () => { $('history').hidden = true; });
$('btn-hist-clear').addEventListener('click', () => {
  localStorage.removeItem(HIST_KEY);
  renderHistory();
  toast('Verlauf gelöscht');
});

$('btn-login').addEventListener('click', saveToken);
$('token').addEventListener('keydown', e => { if (e.key === 'Enter') saveToken(); });

document.querySelectorAll('.sheet').forEach(sheet => {
  sheet.addEventListener('click', e => {
    if (e.target === sheet && sheet.id !== 'gate') sheet.hidden = true;
  });
});

/* ----------------------------------------------------------- Start */

vorschlaegeBauen();
hoeheAnpassen();
if (!localStorage.getItem(TOKEN_KEY)) openGate();
checkHealth();
setInterval(checkHealth, 20000);

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      /* Ohne HTTPS registriert kein Browser einen Service Worker - die App
         funktioniert trotzdem, nur ohne Offline-Huelle. */
    });
  });
}
