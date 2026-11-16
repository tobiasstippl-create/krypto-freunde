/* Wealthlens – Zwischenspeicher für die Website-Fassung (von baue.py nach web/sw.js geschrieben).
   App-Seite: erst Netz (damit Updates sofort ankommen), ohne Netz die gemerkte Fassung.
   Bilder, Video, Schriften, Symbole: einmal laden, danach vom Gerät.
   Abrufe zu anderen Seiten (Kurse, Wallets, Meldungen) laufen unverändert durch. */
var VERSION = '2b9c1863ece8';
var CACHE = 'wealthlens-' + VERSION;
var DATEIEN = ["./", "medien/band.mp4", "medien/band.webp", "medien/hintergrund-dunkel.webp", "medien/hintergrund-hell.webp", "medien/leer.webp", "medien/logo.webp", "schriften/Inter-latin-2.woff2", "schriften/Inter-latin-ext-1.woff2", "schriften/JetBrainsMono-latin-2.woff2", "schriften/JetBrainsMono-latin-ext-1.woff2", "schriften/Sora-latin-2.woff2", "schriften/Sora-latin-ext-1.woff2", "manifest.webmanifest", "icon-180.png", "icon-192.png", "icon-512.png"];
var SEITE = new URL('./', self.registration.scope).href;

self.addEventListener('install', function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) {
    // einzeln, damit ein Aussetzer nicht alles verwirft
    return Promise.all(DATEIEN.map(function (d) { return c.add(d).catch(function () {}); }));
  }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener('activate', function (e) {
  e.waitUntil(caches.keys().then(function (namen) {
    return Promise.all(namen.filter(function (n) { return n.indexOf('wealthlens-') === 0 && n !== CACHE; })
      .map(function (n) { return caches.delete(n); }));
  }).then(function () { return self.clients.claim(); }));
});

function istSeite(req, url) {
  return req.mode === 'navigate' || url.href.split('?')[0] === SEITE || /\/index\.html$/.test(url.pathname);
}

function seiteHolen(req) {
  return caches.open(CACHE).then(function (c) {
    var gemerkt = c.match(SEITE);
    var netz = fetch(req).then(function (r) {
      if (r && r.ok) c.put(SEITE, r.clone());
      return r;
    });
    // Netz zuerst; antwortet es nicht binnen 5 s oder gar nicht, die gemerkte Fassung
    var frist = new Promise(function (ok) { setTimeout(ok, 5000); }).then(function () { return gemerkt; });
    return Promise.race([netz.catch(function () { return gemerkt; }), frist]).then(function (r) {
      return r || netz;
    });
  });
}

// Safari fragt Videos stückweise an (Range) – aus dem Speicher eine passende 206-Antwort bauen
function teilAntwort(antwort, bereich) {
  return antwort.arrayBuffer().then(function (buf) {
    var m = /bytes=(\d*)-(\d*)/.exec(bereich) || [];
    var start = m[1] ? +m[1] : 0, ende = m[2] ? +m[2] : buf.byteLength - 1;
    if (!m[1] && m[2]) { start = Math.max(0, buf.byteLength - +m[2]); ende = buf.byteLength - 1; }
    ende = Math.min(ende, buf.byteLength - 1);
    return new Response(buf.slice(start, ende + 1), {
      status: 206,
      headers: {
        'Content-Type': antwort.headers.get('Content-Type') || 'video/mp4',
        'Content-Range': 'bytes ' + start + '-' + ende + '/' + buf.byteLength,
        'Content-Length': String(ende - start + 1),
        'Accept-Ranges': 'bytes'
      }
    });
  });
}

function dateiHolen(req) {
  return caches.open(CACHE).then(function (c) {
    return c.match(req.url, { ignoreSearch: true }).then(function (gemerkt) {
      var bereich = req.headers.get('range');
      if (gemerkt) return bereich ? teilAntwort(gemerkt, bereich) : gemerkt;
      if (bereich) return fetch(req);
      return fetch(req).then(function (r) {
        if (r && r.ok && r.status === 200) c.put(req.url.split('?')[0], r.clone());
        return r;
      });
    });
  });
}

self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') return;
  var url = new URL(req.url);
  if (url.origin !== self.location.origin || url.href.indexOf(self.registration.scope) !== 0) return;
  if (/\/sw\.js$/.test(url.pathname)) return;
  e.respondWith(istSeite(req, url) ? seiteHolen(req) : dateiHolen(req));
});
