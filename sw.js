/* Wealthlens – Zwischenspeicher für die Website-Fassung (von baue.py nach web/sw.js geschrieben).
   App-Seite: erst Netz (damit Updates sofort ankommen), ohne Netz die gemerkte Fassung.
   Bilder, Video, Schriften, Symbole: einmal laden, danach vom Gerät.
   Abrufe zu anderen Seiten (Kurse, Wallets, Meldungen) laufen unverändert durch. */
var VERSION = '5946c6fde073';
var CACHE = 'wealthlens-' + VERSION;
var DATEIEN = ["./", "medien/band.mp4", "medien/band.webp", "medien/hintergrund-dunkel.webp", "medien/hintergrund-hell.webp", "medien/leer.webp", "medien/logo.webp", "schriften/Inter-latin-2.woff2", "schriften/Inter-latin-ext-1.woff2", "schriften/JetBrainsMono-latin-2.woff2", "schriften/JetBrainsMono-latin-ext-1.woff2", "schriften/Sora-latin-2.woff2", "schriften/Sora-latin-ext-1.woff2", "manifest.webmanifest", "icon-180.png", "icon-192.png", "icon-512.png"];
var SEITE = new URL('./', self.registration.scope).href;

self.addEventListener('install', function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) {
    // einzeln, damit ein Aussetzer nicht alles verwirft; „reload“ am Browser-Zwischenspeicher vorbei,
    // sonst landet nach einem Update eine alte Fassung (etwa das Manifest) im neuen Speicher
    return Promise.all(DATEIEN.map(function (d) { return c.add(new Request(d, { cache: 'reload' })).catch(function () {}); }));
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

// Manifest: erst Netz (Teilen-Ziel, Symbole ändern sich damit sofort), ohne Netz die gemerkte Fassung
function manifestHolen(req) {
  return caches.open(CACHE).then(function (c) {
    return fetch(req.url, { cache: 'no-cache' }).then(function (r) {
      if (r && r.ok) c.put(req.url.split('?')[0], r.clone());
      return r;
    }).catch(function () { return c.match(req.url, { ignoreSearch: true }); });
  });
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

// Teilen-Menü (Android, installierte App): Die geteilten CSV-Dateien kommen als POST an ./teilen.
// Sie werden kurz im Speicher abgelegt; die App holt sie nach der Weiterleitung ab (teilen.js) und löscht sie.
// Eigener Name ohne „wealthlens-“, damit ein Update den Zwischenspeicher nicht vorher wegräumt.
var GETEILT = 'geteilt-wealthlens';
function teilenAnnehmen(req) {
  return req.formData().then(function (fd) {
    var dateien = fd.getAll('dateien').filter(function (f) { return f && typeof f === 'object' && f.size > 0; });
    return caches.open(GETEILT).then(function (c) {
      return Promise.all(dateien.slice(0, 10).map(function (f, i) {
        return c.put(new URL('./geteilt/' + Date.now() + '-' + i, self.registration.scope).href,
          new Response(f, { headers: { 'Content-Type': f.type || 'text/csv', 'X-Name': encodeURIComponent(f.name || 'geteilt.csv') } }));
      }));
    });
  }).catch(function () { /* Weiterleiten trotzdem, die App meldet dann nichts */ }).then(function () {
    return Response.redirect(new URL('./?geteilt=' + Date.now(), self.registration.scope).href, 303);
  });
}

self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method === 'POST' && /\/teilen$/.test(new URL(req.url).pathname)) { e.respondWith(teilenAnnehmen(req)); return; }
  if (req.method !== 'GET') return;
  var url = new URL(req.url);
  if (url.origin !== self.location.origin || url.href.indexOf(self.registration.scope) !== 0) return;
  if (/\/sw\.js$/.test(url.pathname)) return;
  if (/\/manifest\.webmanifest$/.test(url.pathname)) { e.respondWith(manifestHolen(req)); return; }
  e.respondWith(istSeite(req, url) ? seiteHolen(req) : dateiHolen(req));
});
