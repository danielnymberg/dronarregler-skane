/* Drönarkoll Skåne — kartklient.
 *
 * Regel R2: exakt tre svarslägen, aldrig ett fjärde. Inget svar formuleras
 * som tillåtelse.
 * Regel R3: all geometri kommer ur data/ som i sin tur kommer ur
 * myndighets-API. Klienten ritar inga egna buffertar eller cirklar.
 * Punkt-i-polygon körs alltid mot originalgeometrin i data/omraden/{nvrid}.json,
 * aldrig mot den förenklade visningsgeometrin.
 * Regel R5: LFV-lagret hämtas som ostylat raster direkt från LFV:s server.
 */
(function () {
  "use strict";

  var DATA = window.DK_DATA_BAS || "/data";
  var LFV = window.DK_LFV;
  var LFV_LAGER = "mais:CTR,mais:TIZ,mais:TIA,mais:ATZ,mais:RSTA,mais:DNGA";

  var FARG = {
    nationalpark: "#1b6b3a",
    naturreservat: "#2e7d32",
    "naturreservat-kommunalt": "#4a7c1f",
    naturvardsomrade: "#00695c",
    "djur-och-vaxtskydd": "#b34700",
    kulturreservat: "#6a4f9c",
    "interimistiskt-forbud": "#8e0000",
    naturminne: "#00695c",
    vattenskyddsomrade: "#0d6e9c",
    landskapsbildsskydd: "#7a6a2f",
    biotopskydd: "#4c6b2f"
  };

  function fargFor(lager) { return FARG[lager] || "#444"; }

  /* ---------------------------------------------------- punkt-i-polygon */
  function punktIRing(x, y, ring) {
    var inne = false;
    for (var i = 0; i < ring.length - 1; i++) {
      var x1 = ring[i][0], y1 = ring[i][1];
      var x2 = ring[i + 1][0], y2 = ring[i + 1][1];
      if ((y1 > y) !== (y2 > y)) {
        var xs = (x2 - x1) * (y - y1) / (y2 - y1) + x1;
        if (x === xs) return true;
        if (x < xs) inne = !inne;
      }
    }
    return inne;
  }

  function punktIGeometri(lon, lat, geom) {
    if (!geom) return false;
    var polys = geom.type === "MultiPolygon" ? geom.coordinates : [geom.coordinates];
    for (var p = 0; p < polys.length; p++) {
      var rings = polys[p];
      if (!rings.length) continue;
      if (punktIRing(lon, lat, rings[0])) {
        var iHal = false;
        for (var h = 1; h < rings.length; h++) {
          if (punktIRing(lon, lat, rings[h])) { iHal = true; break; }
        }
        if (!iHal) return true;
      }
    }
    return false;
  }

  function avstandM(lon1, lat1, lon2, lat2) {
    var R = 6371008.8, rad = Math.PI / 180;
    var p1 = lat1 * rad, p2 = lat2 * rad;
    var dp = p2 - p1, dl = (lon2 - lon1) * rad;
    var a = Math.sin(dp / 2) * Math.sin(dp / 2) +
            Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) * Math.sin(dl / 2);
    return 2 * R * Math.asin(Math.sqrt(a));
  }

  function narmasteHorn(lon, lat, geom) {
    var basta = Infinity;
    (function gar(c) {
      if (typeof c[0] === "number") {
        var d = avstandM(lon, lat, c[0], c[1]);
        if (d < basta) basta = d;
      } else { for (var i = 0; i < c.length; i++) gar(c[i]); }
    })(geom.coordinates);
    return basta;
  }

  function formatAvstand(m) {
    if (m < 950) return Math.round(m / 10) * 10 + " m";
    return (m / 1000).toFixed(m < 9500 ? 1 : 0).replace(".", ",") + " km";
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function hamta(url) {
    return fetch(url, { credentials: "omit" }).then(function (r) {
      if (!r.ok) throw new Error(url + " svarade " + r.status);
      return r.json();
    });
  }

  /* -------------------------------------------------------------- karta */
  function byggKarta(elId, opts) {
    opts = opts || {};
    var karta = L.map(elId, {
      preferCanvas: true,
      center: opts.center || [55.85, 13.6],
      zoom: opts.zoom || 8,
      scrollWheelZoom: !opts.mini
    });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>-bidragsgivare'
    }).addTo(karta);
    return karta;
  }

  function lfvLager() {
    return L.tileLayer.wms(LFV.url, {
      layers: LFV_LAGER,
      format: "image/png",
      transparent: true,
      version: "1.1.1",
      opacity: 0.7,
      attribution: LFV.attribution
    });
  }

  /* --------------------------------------------------------- startsidan */
  function startsidan() {
    var karta = byggKarta("karta");
    var statusEl = document.getElementById("laddstatus");
    var svarEl = document.getElementById("positionssvar");
    var posKnapp = document.getElementById("positionsknapp");
    var extraToggle = document.getElementById("extralager");
    var lfvToggle = document.getElementById("lfvtoggle");

    var lfv = lfvLager();
    if (lfvToggle.checked) lfv.addTo(karta);
    lfvToggle.addEventListener("change", function () {
      if (lfvToggle.checked) lfv.addTo(karta); else karta.removeLayer(lfv);
    });

    var karnGrupp = L.layerGroup().addTo(karta);
    var extraGrupp = L.layerGroup();
    var alltGeojson = null;

    extraToggle.addEventListener("change", function () {
      if (extraToggle.checked) extraGrupp.addTo(karta); else karta.removeLayer(extraGrupp);
    });

    function stil(f) {
      var c = fargFor(f.properties.lager);
      return { color: c, weight: 1.5, opacity: 0.9, fillColor: c, fillOpacity: 0.25 };
    }

    function popup(f) {
      var p = f.properties;
      var url = "/omrade/" + p.nvrid + "-" + p.slug + "/";
      return '<strong>' + esc(p.namn) + "</strong><br>" + esc(p.skyddstyp) +
        "<br>" + (p.antal_citat
          ? p.antal_citat + " verifierade citat ur beslutet"
          : "Inga verifierade citat — läs beslutet") +
        '<br><a href="' + url + '">Öppna områdessidan</a>';
    }

    hamta(DATA + "/areas.geojson").then(function (gj) {
      alltGeojson = gj;
      var karn = [], extra = [];
      gj.features.forEach(function (f) {
        (f.properties.karnlager ? karn : extra).push(f);
      });
      L.geoJSON({ type: "FeatureCollection", features: karn }, {
        style: stil,
        onEachFeature: function (f, l) { l.bindPopup(popup(f)); }
      }).addTo(karnGrupp);
      L.geoJSON({ type: "FeatureCollection", features: extra }, {
        style: stil,
        onEachFeature: function (f, l) { l.bindPopup(popup(f)); }
      }).addTo(extraGrupp);
      statusEl.textContent = karn.length + " områden i kärnlagret laddade, " +
        extra.length + " i extralagren.";
      posKnapp.disabled = false;
      try { karta.fitBounds(L.geoJSON(gj).getBounds(), { padding: [10, 10] }); }
      catch (e) { /* behåll startvyn */ }
    }).catch(function (e) {
      statusEl.textContent = "Kartdata kunde inte laddas: " + e.message +
        " — inga områden visas. Tolka inte en tom karta som att inget är reglerat.";
    });

    var bboxIndex = null;
    function index() {
      if (bboxIndex) return Promise.resolve(bboxIndex);
      return hamta(DATA + "/bbox-index.json").then(function (d) {
        bboxIndex = d; return d;
      });
    }

    posKnapp.addEventListener("click", function () {
      if (!navigator.geolocation) {
        svarEl.innerHTML = '<div class="svar svar-tacks-ej"><span class="svar-rubrik">' +
          "Positionsbestämning saknas i den här webbläsaren</span>" +
          "Sök upp området på kartan i stället.</div>";
        return;
      }
      posKnapp.disabled = true;
      svarEl.innerHTML = '<p class="laddstatus">Hämtar position …</p>';
      navigator.geolocation.getCurrentPosition(function (pos) {
        var lat = pos.coords.latitude, lon = pos.coords.longitude;
        L.circleMarker([lat, lon], { radius: 6, color: "#0b4f8a", weight: 3,
          fillColor: "#fff", fillOpacity: 1 }).bindTooltip("Din position").addTo(karta);
        karta.setView([lat, lon], 12);
        svaraForPosition(lon, lat);
      }, function (err) {
        posKnapp.disabled = false;
        svarEl.innerHTML = '<div class="svar svar-tacks-ej"><span class="svar-rubrik">' +
          "Positionen kunde inte hämtas</span>" + esc(err.message) +
          " Sök upp området på kartan i stället.</div>";
      }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 });
    });

    window.DK_positionssvar = svaraForPosition;   // används av testkörningar

    function svaraForPosition(lon, lat) {
      svarEl.innerHTML = '<p class="laddstatus">Kontrollerar mot områdesgränserna …</p>';
      index().then(function (idx) {
        var kandidater = idx.rader.filter(function (r) {
          return lon >= r[6] && lon <= r[8] && lat >= r[7] && lat <= r[9];
        });
        return Promise.all(kandidater.map(function (r) {
          return hamta(DATA + "/omraden/" + r[0] + ".json");
        }));
      }).then(function (omraden) {
        var traffar = omraden.filter(function (o) {
          return punktIGeometri(lon, lat, o.geometri);
        });
        visaSvar(lon, lat, traffar);
      }).catch(function (e) {
        svarEl.innerHTML = '<div class="svar svar-tacks-ej"><span class="svar-rubrik">' +
          "Kontrollen kunde inte genomföras</span>" + esc(e.message) +
          " Tolka inte detta som att inget är reglerat på platsen.</div>";
      }).then(function () { posKnapp.disabled = false; });
    }

    function narliggande(lon, lat, uteslut) {
      if (!alltGeojson) return [];
      var lista = [];
      alltGeojson.features.forEach(function (f) {
        if (uteslut.indexOf(f.properties.nvrid) !== -1) return;
        if (!f.properties.karnlager) return;
        var d = narmasteHorn(lon, lat, f.geometry);
        if (d < 10000) lista.push({ p: f.properties, d: d });
      });
      lista.sort(function (a, b) { return a.d - b.d; });
      return lista.slice(0, 8);
    }

    function visaSvar(lon, lat, traffar) {
      var h = "";
      if (traffar.length) {
        h += '<div class="svar svar-reglerat"><span class="svar-rubrik">' +
          "Reglerat område — läs beslutet</span>" +
          "<p>Din position ligger inom " + traffar.length +
          (traffar.length === 1 ? " område" : " områden") +
          " med myndighetsbeslut. Tjänsten avgör inte vad besluten innebär för " +
          "din flygning.</p><ul class=\"lista-omraden\">";
        traffar.forEach(function (o) {
          h += "<li><a href=\"/omrade/" + o.nvrid + "-" + o.slug + "/\">" +
            esc(o.namn) + "</a> — " + esc(o.skyddstyp) +
            (o.citat && o.citat.length
              ? " · " + o.citat.length + " verifierade citat"
              : " · inga verifierade citat, läs beslutet") +
            (o.ocr ? " · OCR-tolkad text" : "") + "</li>";
        });
        h += "</ul></div>";
      } else {
        h += '<div class="svar svar-ingen"><span class="svar-rubrik">' +
          "Ingen restriktion hittad i de källor tjänsten täcker</span>" +
          "<p>Positionen ligger inte inom något av de skyddade områden som finns i " +
          "tjänstens databas. Det är ett besked om vad databasen innehåller — " +
          "inte ett besked om din flygning.</p></div>";
      }
      h += '<div class="svar svar-tacks-ej"><span class="svar-rubrik">' +
        "Denna källa täcks inte här</span>" +
        "<p>Luftrum (flygplatser, restriktionsområden, NOTAM) visas i LFV-lagret och " +
        'på LFV:s drönarkarta — kontrollera alltid det separat: ' +
        '<a href="' + LFV.dronechart + '" rel="noopener">' + LFV.dronechart +
        "</a>.</p></div>";

      var narliggandeLista = narliggande(lon, lat, traffar.map(function (o) { return o.nvrid; }));
      if (narliggandeLista.length) {
        h += "<h3>Närliggande områden</h3><ul class=\"lista-omraden\">";
        narliggandeLista.forEach(function (n) {
          h += "<li><a href=\"/omrade/" + n.p.nvrid + "-" + n.p.slug + "/\">" +
            esc(n.p.namn) + "</a> — " + esc(n.p.skyddstyp) +
            ' <span class="avstand">ca ' + formatAvstand(n.d) + " bort</span></li>";
        });
        h += "</ul><p class=\"avstand\">Avstånden är beräknade mot den förenklade " +
          "kartgeometrin (tolerans 15 m) och är ungefärliga.</p>";
      }
      h += '<div class="ansvar">' + (window.DK_ANSVARSTEXT || "") + "</div>";
      svarEl.innerHTML = h;
    }
  }

  /* --------------------------------------------------------- minikartan */
  function minikarta() {
    var el = document.getElementById("minikarta");
    if (!el) return;
    var nvrid = el.getAttribute("data-nvrid");
    var karta = byggKarta("minikarta", { mini: true });
    hamta(DATA + "/omraden/" + nvrid + ".json").then(function (o) {
      if (!o.geometri) { el.innerHTML = "<p>Området saknar kartgeometri i källdatan.</p>"; return; }
      var lager = L.geoJSON(o.geometri, {
        style: { color: fargFor(o.lager), weight: 2, fillColor: fargFor(o.lager),
                 fillOpacity: 0.25 }
      }).addTo(karta);
      karta.fitBounds(lager.getBounds(), { padding: [12, 12] });
      lfvLager().addTo(karta);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (document.getElementById("karta")) startsidan();
    if (document.getElementById("minikarta")) minikarta();
  });
})();
