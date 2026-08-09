const CACHE = "cabo-v67";
const FILES = [
  "./",
  "./CABO_v0.7.2.1.html",
  "./manifest-721.webmanifest",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "./assets/rules-4.jpg",
  "./xlsx.full.min.js"
];
self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE)
      .then((c) => Promise.all(FILES.map((f) => c.add(f).catch(() => null))))
      .then(() => self.skipWaiting())
  );
});
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())
  );
});
self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  const url = new URL(e.request.url);
  const isPage = e.request.mode === "navigate" || url.pathname.endsWith(".html") || url.pathname.endsWith("/");

  if (isPage) {
    // 页面/HTML：网络优先，失败回缓存（保证新版本入口页能及时更新，不被旧 SW 锁死）
    e.respondWith(
      fetch(e.request).then((resp) => {
        if (resp && resp.status === 200 && resp.type === "basic") {
          const copy = resp.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return resp;
      }).catch(() => caches.match(e.request).then((r) => r || caches.match("./")))
    );
    return;
  }

  // 静态资源：缓存优先，失败回网络
  e.respondWith(
    caches.match(e.request).then((r) => r || fetch(e.request).then((resp) => {
      if (resp && resp.status === 200 && resp.type === "basic") {
        const copy = resp.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
      }
      return resp;
    }).catch(() => r))
  );
});
