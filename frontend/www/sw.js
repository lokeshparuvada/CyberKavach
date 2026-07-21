// Citizen Fraud Shield -- service worker
// Caches the app shell (HTML/CSS/JS/icons) so the app opens instantly and
// still loads its interface offline. API calls (quick-check, chat, admin)
// always go to the network since they need live data -- only the static
// shell is cached.

const CACHE_NAME = "fraud-shield-shell-v2";
const SHELL_FILES = [
  "./index.html",
  "./manifest.json",
  "./icon-192.png",
  "./icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_FILES))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Never cache API calls -- these need live, current data.
  const isApiCall = url.pathname.startsWith("/session") ||
                     url.pathname.startsWith("/quick-check") ||
                     url.pathname.startsWith("/feedback") ||
                     url.pathname.startsWith("/admin") ||
                     url.pathname.startsWith("/report") ||
                     url.pathname.startsWith("/languages") ||
                     url.pathname.startsWith("/health");
  if (isApiCall) return;

  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) return cached;
      return fetch(event.request).then((resp) => {
        // Opportunistically cache same-origin shell assets as they're used.
        if (event.request.method === "GET" && url.origin === location.origin) {
          const clone = resp.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        }
        return resp;
      });
    })
  );
});
