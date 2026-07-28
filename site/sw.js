/* Drönarkoll — service worker.
 *
 * Två saker ska fungera utan nät, för de är själva poängen med appen:
 *
 *   1. LFV:s luftrum (data/lfv.json, ~200 kB)
 *   2. De föreskrifter som nämner luftfart ordagrant (data/luftfart.json, ~300 kB)
 *
 * Tillsammans är de en halv megabyte och räcker för att avge nivå 1-svaret —
 * det som vaktläget piper på — även när telefonen saknar täckning. Resten av
 * datan (rutnätsfilerna för geometri och störningsföreskrifter) cachas allt
 * eftersom du använder appen, och serveras ur cachen när nätet är borta.
 *
 * Det som INTE cachas för evigt är svaret på frågan "hur gammal är datan".
 * Ett bygge som misslyckas ska få sajten att se gammal ut, inte fräsch —
 * därför är HTML alltid nät-först och data alltid revalideras i bakgrunden.
 */
var VERSION = "__VERSION__";
var SKAL = "dk-skal-" + VERSION;
var DATA_CACHE = "dk-data-" + VERSION;
var KARTRUTOR = "dk-kartrutor-v1";
var MAX_KARTRUTOR = 600;

/* Utan de här går appen inte att öppna offline. */
var SKALFILER = [
  "/",
  "/regler/",
  "/assets/app.js?v=" + VERSION,
  "/assets/style.css?v=" + VERSION,
  "/vendor/leaflet/leaflet.js",
  "/vendor/leaflet/leaflet.css",
  "/manifest.webmanifest",
  "/assets/ikon-192.png",
  "/assets/ikon-512.png"
];

/* De här är svaret på nivå 1-frågan och måste finnas offline. */
var OFFLINEDATA = [
  "/data/lfv.json",
  "/data/luftfart.json"
];

self.addEventListener("install", function (e) {
  e.waitUntil(
    Promise.all([
      caches.open(SKAL).then(function (c) {
        // addAll faller på första 404. Skalet ska inte kunna halvinstalleras,
        // men en enskild ikon ska heller inte sänka hela installationen.
        return Promise.all(SKALFILER.map(function (u) {
          return c.add(new Request(u, { cache: "reload" })).catch(function () { });
        }));
      }),
      caches.open(DATA_CACHE).then(function (c) {
        return Promise.all(OFFLINEDATA.map(function (u) {
          return c.add(new Request(u, { cache: "reload" })).catch(function () { });
        }));
      })
    ]).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (e) {
  e.waitUntil(
    caches.keys().then(function (namn) {
      return Promise.all(namn.map(function (n) {
        if (n === SKAL || n === DATA_CACHE || n === KARTRUTOR) return null;
        return caches.delete(n);
      }));
    }).then(function () { return self.clients.claim(); })
  );
});

function begransa(cacheNamn, tak) {
  return caches.open(cacheNamn).then(function (c) {
    return c.keys().then(function (nycklar) {
      if (nycklar.length <= tak) return;
      return Promise.all(nycklar.slice(0, nycklar.length - tak)
        .map(function (k) { return c.delete(k); }));
    });
  });
}

/* Nät först, cache som fallback. För HTML och för datafiler: du ska aldrig få
 * ett gammalt svar när ett färskt är tillgängligt. */
function natForst(req, cacheNamn) {
  return fetch(req).then(function (svar) {
    if (svar && svar.ok) {
      var kopia = svar.clone();
      caches.open(cacheNamn).then(function (c) { c.put(req, kopia); });
    }
    return svar;
  }).catch(function () {
    return caches.match(req).then(function (traff) {
      if (traff) return traff;
      throw new Error("offline och inte cachad");
    });
  });
}

/* Cache först. För oföränderliga saker: versionerade tillgångar och kartrutor. */
function cacheForst(req, cacheNamn, tak) {
  return caches.match(req).then(function (traff) {
    if (traff) return traff;
    return fetch(req).then(function (svar) {
      if (svar && (svar.ok || svar.type === "opaque")) {
        var kopia = svar.clone();
        caches.open(cacheNamn).then(function (c) {
          c.put(req, kopia);
          if (tak) begransa(cacheNamn, tak);
        });
      }
      return svar;
    });
  });
}

self.addEventListener("fetch", function (e) {
  var req = e.request;
  if (req.method !== "GET") return;
  var url = new URL(req.url);

  // Kartbakgrunden från OpenStreetMap. Cache först, med tak — annars äter en
  // enda utzoomning av Sverige upp lagringsutrymmet.
  if (url.hostname.indexOf("tile.openstreetmap.org") !== -1) {
    e.respondWith(cacheForst(req, KARTRUTOR, MAX_KARTRUTOR));
    return;
  }

  // LFV:s egen WMS-ruta lämnas orörd: vi cachar aldrig om och återserverar
  // aldrig deras tiles.
  if (url.hostname.indexOf("lfv.se") !== -1) return;
  if (url.origin !== self.location.origin) return;

  if (url.pathname.indexOf("/data/") === 0) {
    e.respondWith(natForst(req, DATA_CACHE));
    return;
  }
  if (url.search.indexOf("v=") !== -1 || url.pathname.indexOf("/vendor/") === 0) {
    e.respondWith(cacheForst(req, SKAL));
    return;
  }
  if (req.mode === "navigate" || (req.headers.get("accept") || "").indexOf("text/html") !== -1) {
    e.respondWith(natForst(req, SKAL).catch(function () {
      return caches.match("/");
    }));
    return;
  }
  e.respondWith(natForst(req, SKAL));
});
