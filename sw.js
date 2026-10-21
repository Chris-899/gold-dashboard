const CACHE_NAME = "gold-bias-v1";
const SHELL_URLS = ["./", "./manifest.webmanifest", "./icons/gold.svg"];

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(SHELL_URLS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key.startsWith("gold-bias-") && key !== CACHE_NAME).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) return;

  const cacheKey = new URL(request.url);
  cacheKey.search = "";
  const keyRequest = new Request(cacheKey, { method: "GET" });
  const isData = url.pathname.endsWith("/data/latest.json") || url.pathname.endsWith("/data/history.json");

  event.respondWith(
    fetch(request).then(response => {
        if (response.ok) {
          const copy = response.clone();
          return caches.open(CACHE_NAME)
            .then(cache => cache.put(keyRequest, copy))
            .then(() => response);
        }
        return response;
      })
      .catch(async () => {
        const cached = await caches.match(keyRequest);
        if (cached) return cached;
        if (isData) {
          return new Response("Market data is unavailable offline. Reconnect to refresh the latest dashboard snapshot.", {
            status: 503,
            headers: { "Content-Type": "text/plain; charset=utf-8" }
          });
        }
        return Response.error();
      })
  );
});
