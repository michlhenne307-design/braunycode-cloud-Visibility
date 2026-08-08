/* BraunyCode Control - Frontend ohne Build-Schritt.
   Modell-Ausgaben werden ausschliesslich per textContent gesetzt,
   niemals per innerHTML - sonst waere jeder generierte String ein XSS-Vektor. */
'use strict';

const $ = id => document.getElementById(id);
const TOKEN_KEY = 'brauny.token';
const HIST_KEY = 'brauny.history';
const HIST_MAX = 20;

const TAGS = {
  status:  'System',
  plan:    'Plan',
  tool:    'Werkzeug',
  code:    'Code',
  sandbox: 'Sandbox',
  error:   'Fehler',
  done:    'Fertig',
};

let ws = null;
let running = false;
let codeText = '';
let outLines = 0;
let startedAt = 0;

/* ----------------------------------------------------------- Hilfsmittel */

function toast(text) {
  const el = $('toast');
  el.textContent = text;
  el.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove('show'), 1900);
}

function clearPanel(el, placeholder) {
  el.textContent = '';
  if (placeholder) {
    const div = document.createElement('div');
    div.className = 'empty';
    div.textContent = placeholder;
    el.appendChild(div);
  }
}

function dropPlaceholder(el) {
  const ph = el.querySelector('.empty');
  if (ph) ph.remove();
  // Auch die Whitespace-Textknoten aus der HTML-Einrueckung entfernen -
  // sie wuerden sonst als Leerraum am Panelanfang stehen bleiben.
  [...el.childNodes].forEach(node => {
    if (node.nodeType === Node.TEXT_NODE && !node.textContent.trim()) node.remove();
  });
}

function logLine(kind, text) {
  const panel = $('panel-log');
  dropPlaceholder(panel);
  const atBottom = panel.scrollHeight - panel.scrollTop - panel.clientHeight < 60;

  const line = document.createElement('span');
  line.className = 'line ' + kind;
  const tag = document.createElement('span');
  tag.className = 'tag';
  tag.textContent = TAGS[kind] || kind;
  line.appendChild(tag);
  line.appendChild(document.createTextNode(text));
  panel.appendChild(line);

  if (atBottom) panel.scrollTop = panel.scrollHeight;
}

/* ----------------------------------------------------------- Ansichten */

function selectTab(name) {
  document.querySelectorAll('.tabs button').forEach(b => {
    b.setAttribute('aria-selected', String(b.dataset.panel === name));
  });
  ['log', 'code', 'out'].forEach(p => { $('panel-' + p).hidden = p !== name; });
  if (name === 'code') $('badge-code').textContent = '';
  if (name === 'out') $('badge-out').textContent = '';
}

function setRunning(on) {
  running = on;
  const btn = $('btn-run');
  btn.textContent = on ? 'Stoppen' : 'Agent starten';
  btn.classList.toggle('stop', on);
  $('prompt').disabled = on;
  $('btn-clear').disabled = on;
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
      setHealth('ok', 'Bereit · ' + data.model);
    } else {
      // Auf "fehler:" prüfen statt auf "ok": eine konfigurierte API meldet
      // "konfiguriert (nicht angefragt)" und ist damit nicht kaputt.
      const labels = { modell_backend: 'Modell', docker: 'Docker' };
      const broken = Object.keys(labels)
        .filter(k => String(data[k] || '').startsWith('fehler'))
        .map(k => labels[k]);
      setHealth('bad', 'Problem: ' + (broken.join(', ') || 'unbekannt'));
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
    const li = document.createElement('li');
    li.textContent = 'Noch keine Läufe.';
    ul.appendChild(li);
    return;
  }
  list.forEach(item => {
    const li = document.createElement('li');
    li.textContent = item.prompt;
    const when = document.createElement('div');
    when.className = 'when';
    const mark = document.createElement('span');
    mark.className = item.ok ? 'ok' : 'fail';
    mark.textContent = item.ok ? '✓ erfolgreich' : '✗ fehlgeschlagen';
    when.appendChild(mark);
    when.appendChild(document.createTextNode(
      ' · ' + new Date(item.at).toLocaleString('de-DE') + ' · ' + item.seconds + ' s'));
    li.appendChild(when);
    li.addEventListener('click', () => {
      $('prompt').value = item.prompt;
      $('history').hidden = true;
      toast('Auftrag übernommen');
    });
    ul.appendChild(li);
  });
}

/* ----------------------------------------------------------- Lauf */

function start() {
  const prompt = $('prompt').value.trim();
  if (!prompt) { toast('Bitte einen Auftrag eingeben'); return; }

  const token = localStorage.getItem(TOKEN_KEY) || '';

  clearPanel($('panel-log'), null);
  clearPanel($('code-body'), 'Der generierte Code erscheint hier.');
  clearPanel($('out-body'), 'Die Ausgabe des Programms erscheint hier.');
  $('badge-code').textContent = '';
  $('badge-out').textContent = '';
  codeText = '';
  outLines = 0;
  startedAt = Date.now();
  selectTab('log');
  setRunning(true);

  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${scheme}://${location.host}/ws/agent`);

  ws.onopen = () => ws.send(JSON.stringify({ token, prompt }));
  ws.onmessage = e => handleEvent(e.data, prompt);
  ws.onerror = () => logLine('error', 'Verbindung fehlgeschlagen. Läuft der Dienst? Ist Port 8000 offen?');
  ws.onclose = () => { ws = null; setRunning(false); };
}

function stop() {
  if (ws) { ws.close(); ws = null; }
  logLine('error', 'Vom Benutzer abgebrochen.');
  setRunning(false);
}

function handleEvent(raw, prompt) {
  let ev;
  try { ev = JSON.parse(raw); }
  catch { logLine('status', String(raw)); return; }

  switch (ev.type) {
    case 'code': {
      codeText = ev.text || '';
      const attempt = ev.attempt || 1;
      const body = $('code-body');
      clearPanel(body, null);
      const pre = document.createElement('div');
      pre.textContent = codeText;
      body.appendChild(pre);

      // Kopfzeile des Code-Reiters mit der Versuchsnummer beschriften
      const head = document.querySelector('#panel-code .panel-head span');
      if (head) head.textContent = attempt > 1 ? `main.py · Versuch ${attempt}` : 'main.py';

      // Bei einer korrigierten Fassung startet ein frischer Lauf:
      // alte Ausgabe wegräumen, damit sie sich nicht stapelt.
      if (attempt > 1) {
        clearPanel($('out-body'), 'Die Ausgabe des Programms erscheint hier.');
        outLines = 0;
        $('badge-out').textContent = '';
      }

      const lines = codeText.split('\n').length;
      logLine('code', attempt > 1
        ? `Korrigierte Fassung (Versuch ${attempt}), ${lines} Zeilen — im Reiter „Code“`
        : `${lines} Zeilen erzeugt — im Reiter „Code“`);
      if ($('panel-code').hidden) $('badge-code').textContent = '•';
      break;
    }
    case 'sandbox': {
      const body = $('out-body');
      dropPlaceholder(body);
      const line = document.createElement('div');
      line.textContent = ev.text;
      body.appendChild(line);
      body.scrollTop = body.scrollHeight;
      outLines += 1;
      if ($('panel-out').hidden) $('badge-out').textContent = String(outLines);
      logLine('sandbox', ev.text);
      break;
    }
    case 'done': {
      // Serverseitige Messung bevorzugen. Ohne vorheriges start() ist
      // startedAt 0 - dann waere die lokale Differenz die Epoch-Zeit.
      const seconds = typeof ev.seconds === 'number'
        ? ev.seconds.toFixed(1)
        : (startedAt ? ((Date.now() - startedAt) / 1000).toFixed(1) : '?');
      logLine('done', ev.text || (ev.ok ? 'Fertig.' : 'Fehlgeschlagen.'));
      addHistory({ prompt, ok: !!ev.ok, at: Date.now(), seconds });
      if (ev.ok && outLines && $('panel-out').hidden) selectTab('out');
      break;
    }
    case 'error': {
      logLine('error', ev.text);
      if (ev.code === 'auth') {
        localStorage.removeItem(TOKEN_KEY);
        openGate('Token abgelehnt. Bitte erneut eingeben.');
      }
      break;
    }
    default:
      logLine(ev.type in TAGS ? ev.type : 'status', ev.text || '');
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
  toast('Token gespeichert');
  checkHealth();
}

/* ----------------------------------------------------------- Verdrahtung */

$('btn-run').addEventListener('click', () => (running ? stop() : start()));
$('btn-clear').addEventListener('click', () => {
  clearPanel($('panel-log'), 'Noch kein Lauf gestartet.');
  clearPanel($('code-body'), 'Der generierte Code erscheint hier.');
  clearPanel($('out-body'), 'Die Ausgabe des Programms erscheint hier.');
  $('badge-code').textContent = '';
  $('badge-out').textContent = '';
});

document.querySelectorAll('.tabs button').forEach(b => {
  b.addEventListener('click', () => selectTab(b.dataset.panel));
});

$('btn-copy').addEventListener('click', async () => {
  if (!codeText) { toast('Noch kein Code da'); return; }
  try {
    await navigator.clipboard.writeText(codeText);
    toast('Code kopiert');
  } catch {
    toast('Kopieren nicht erlaubt — braucht HTTPS');
  }
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

$('prompt').addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) start();
});

document.querySelectorAll('.sheet').forEach(sheet => {
  sheet.addEventListener('click', e => {
    if (e.target === sheet && sheet.id !== 'gate') sheet.hidden = true;
  });
});

/* ----------------------------------------------------------- Start */

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
