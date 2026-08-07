/* Service Worker: cached nur die statische Huelle.
   API-Aufrufe und WebSockets laufen immer direkt ans Netz - eine gecachte
   /healthz-Antwort waere schlimmer als gar keine. */
const CACHE = 'brauny-v1';
const SHELL = [
  '/',
  '/static/app.css',
  '/static/app.js',
  '/manifest.json',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/apple-touch-icon.png',
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE)
      .then(cache => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname === '/healthz' || url.pathname.startsWith('/ws/')) return;

  // Netz zuerst, Cache als Rueckfalloption: so ist eine frisch deployte
  // Oberflaeche sofort da, offline bleibt die letzte Fassung nutzbar.
  event.respondWith(
    fetch(request)
      .then(response => {
        if (response && response.status === 200 && response.type === 'basic') {
          const copy = response.clone();
          caches.open(CACHE).then(cache => cache.put(request, copy));
        }
        return response;
      })
      .catch(() => caches.match(request).then(hit => hit || caches.match('/')))
  );
});
