/* Service worker de Rafita: cachea el shell de la app para uso offline;
   la API siempre va a la red (datos frescos).
   Decision (Fase 3): call_rafita.html queda FUERA de la experiencia
   instalable a proposito: vive en otro origen (voz.*) y se embebe desde la
   vista Llamada; aqui solo va el shell de la SPA. */
const CACHE = 'rafita-shell-6d5fd6be';
const SHELL = [
  './',
  './index.html',
  './styles-fd29ea6d.css',
  './app-62e39d05.js',
  './config.js',
  './tokens-1c80d23c.css',
  './manifest.webmanifest',
  './icons/icon-192.png',
  './icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.pathname.startsWith('/api')) {
    return;
  }
  event.respondWith(
    caches.match(event.request).then(
      (cached) =>
        cached ||
        fetch(event.request)
          .then((resp) => {
            if (url.origin === self.location.origin && resp.ok) {
              const copia = resp.clone();
              caches.open(CACHE).then((cache) => cache.put(event.request, copia));
            }
            return resp;
          })
          .catch(() => caches.match('./index.html'))
    )
  );
});
