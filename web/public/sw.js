// Makes the site installable as an app. Everything is fetched live from the
// server; only a small offline notice is cached for when the network is down.
const OFFLINE = '/offline.html'

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open('fsoc-v1').then((cache) => cache.add(OFFLINE)).then(() => self.skipWaiting()))
})
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))
self.addEventListener('fetch', (event) => {
  if (event.request.mode === 'navigate') event.respondWith(fetch(event.request).catch(() => caches.match(OFFLINE)))
})
