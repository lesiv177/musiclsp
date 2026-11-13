/* MusicLSP service worker: лише оболонка застосунку для режиму «вкладка/застосунок» на ПК.
   НЕ кешуємо: /api/*, аудіо-потоки, Range-запити, чужі домени. Мережа в пріоритеті, кеш — запасний варіант,
   тож оновлення файлів підхоплюються одразу, а не застрягають у старій версії. */
const CACHE = "musiclsp-shell-v1";
const SHELL = ["./", "./index.html", "./icon.svg", "./icon-192.png", "./icon-512.png", "./manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET" || req.headers.has("range")) return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;            // чужі домени (API, CDN, Telegram) — без нас
  if (url.pathname.includes("/api/") || /\.(mp3|ogg|flac|m4a|wav|aac)$/i.test(url.pathname)) return;
  event.respondWith(
    fetch(req).then((res) => {
      if (res && res.ok && res.type === "basic") {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
      }
      return res;
    }).catch(() => caches.match(req).then((hit) => hit || (req.mode === "navigate" ? caches.match("./index.html") : Response.error())))
  );
});
