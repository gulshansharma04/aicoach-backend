const CACHE_NAME = "aicoach-pwa-v1";

const CORE_ASSETS = [
  "./",
  "./index.html",
  "./batting.html",
  "./static/style.css",
  "./static/app.js",
  "./static/config.js",
  "./manifest.json"
];

// Install: cache core files
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(CORE_ASSETS))
  );
  self.skipWaiting();
});

// Activate: cleanup old caches
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.map((k) => (k !== CACHE_NAME ? caches.delete(k) : null)))
    )
  );
  self.clients.claim();
});

// Fetch: network-first for HTML, cache-first for static
self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // Only handle same-origin
  if (url.origin !== location.origin) return;

  // For navigation requests, go network first
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(() => caches.match("./batting.html"))
    );
    return;
  }

  // For other requests, use cache-first then network
  event.respondWith(
    caches.match(req).then((cached) => cached || fetch(req))
  );
});
