/* Drönarkoll — kartklient.
 *
 * Startsidan är en kartapp: kartan fyller skärmen, positionen hämtas direkt,
 * och svaret kommer upp som en panel över kartan.
 *
 * Regel R2: exakt tre svarslägen, aldrig ett fjärde. Inget svar formuleras
 * som tillåtelse.
 * Regel R3: all geometri kommer ur data/ som i sin tur kommer ur
 * myndighets-API. Klienten ritar inga egna buffertar eller cirklar.
 * Punkt-i-polygon körs alltid mot ORIGINALGEOMETRIN i data/geom/{ruta}.json,
 * aldrig mot den förenklade visningsgeometrin i data/rutor/.
 * Regel R5: LFV-lagret hämtas som ostylat raster direkt från LFV:s server.
 */
(function () {
  "use strict";

  var DATA = window.DK_DATA_BAS || "/data";
  var LFV = window.DK_LFV || {};
  var LFV_LAGER = "mais:CTR,mais:TIZ,mais:TIA,mais:ATZ,mais:RSTA,mais:DNGA";
  var VISNING_RUTA = 1.0;        // grader per visningsruta
  var GEOM_RUTA = 0.25;          // grader per geometri-/bbox-ruta
  var DETALJ_FRAN_ZOOM = 9;

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
  function fargFor(l) { return FARG[l] || "#444"; }

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

  /* ------------------------------------------------ punkt-i-polygon m.m. */
  function punktIRing(x, y, ring) {
    var inne = false;
    for (var i = 0; i < ring.length - 1; i++) {
      var x1 = ring[i][0], y1 = ring[i][1], x2 = ring[i + 1][0], y2 = ring[i + 1][1];
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
      if (!rings.length || !punktIRing(lon, lat, rings[0])) continue;
      var iHal = false;
      for (var h = 1; h < rings.length; h++) {
        if (punktIRing(lon, lat, rings[h])) { iHal = true; break; }
      }
      if (!iHal) return true;
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
  function rutorForBounds(b, steg) {
    var ut = [];
    for (var x = Math.floor(b.getWest() / steg); x * steg <= b.getEast(); x++) {
      for (var y = Math.floor(b.getSouth() / steg); y * steg <= b.getNorth(); y++) {
        ut.push(x + "_" + y);
      }
    }
    return ut;
  }
  function rutaFor(lon, lat, steg) {
    return Math.floor(lon / steg) + "_" + Math.floor(lat / steg);
  }

  /* ------------------------------------------------------------ kartappen */
  function kartappen() {
    var karta = L.map("karta", {
      preferCanvas: true, zoomControl: false,
      center: [62.5, 16.5], zoom: 5, worldCopyJump: false
    });
    L.control.zoom({ position: "bottomright" }).addTo(karta);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    }).addTo(karta);

    var lfv = L.tileLayer.wms(LFV.url, {
      layers: LFV_LAGER, format: "image/png", transparent: true,
      version: "1.1.1", opacity: 0.7, attribution: LFV.attribution
    });

    var omradeslager = L.layerGroup().addTo(karta);
    var laddadeRutor = {};       // rutid -> true
    var ritade = {};             // nvrid -> true
    var visadeFeatures = [];     // för avståndsberäkning
    var oversiktLagd = false;
    var oversiktInfo = null;

    var statusEl = document.getElementById("kartstatus");
    var svarEl = document.getElementById("svar");
    var panelEl = document.getElementById("panel");

    function stil(f) {
      var c = fargFor(f.properties.lager);
      return { color: c, weight: 1.5, opacity: 0.9, fillColor: c, fillOpacity: 0.25 };
    }
    function popup(p) {
      return "<strong>" + esc(p.namn) + "</strong><br>" + esc(p.skyddstyp) +
        '<br><a href="/omrade/' + p.nvrid + "-" + p.slug + '/">Vad säger beslutet?</a>';
    }
    function laggTill(features) {
      var nya = features.filter(function (f) { return !ritade[f.properties.nvrid]; });
      nya.forEach(function (f) { ritade[f.properties.nvrid] = true; });
      if (!nya.length) return;
      visadeFeatures = visadeFeatures.concat(nya);
      L.geoJSON({ type: "FeatureCollection", features: nya }, {
        style: stil,
        onEachFeature: function (f, l) { l.bindPopup(popup(f.properties)); }
      }).addTo(omradeslager);
    }
    function status(text) { statusEl.textContent = text || ""; }

    function laddaForVy() {
      var z = karta.getZoom();
      if (z < DETALJ_FRAN_ZOOM) {
        if (oversiktLagd) {
          status(oversiktInfo);
          return;
        }
        status("Laddar översikt …");
        return hamta(DATA + "/oversikt.json").then(function (gj) {
          oversiktLagd = true;
          laggTill(gj.features);
          oversiktInfo = gj.antal_utelamnade
            ? gj.antal_utelamnade + " mindre områden visas först när du zoomar in"
            : "";
          status(oversiktInfo);
        }).catch(function (e) {
          status("Kartdata kunde inte laddas — tolka inte en tom karta som att " +
                 "inget är reglerat. (" + e.message + ")");
        });
      }
      var rutor = rutorForBounds(karta.getBounds(), VISNING_RUTA).filter(function (r) {
        return !laddadeRutor[r];
      });
      if (!rutor.length) { status(""); return; }
      status("Laddar områden …");
      return Promise.all(rutor.map(function (r) {
        laddadeRutor[r] = true;
        return hamta(DATA + "/rutor/" + r + ".json")
          .then(function (gj) { laggTill(gj.features); })
          .catch(function () { /* ruta utan områden */ });
      })).then(function () { status(""); });
    }

    karta.on("moveend zoomend", laddaForVy);

    /* ---------------------------------------------------------- position */
    var minPosition = null;
    var positionsMarkor = null;

    function visaPanel(html) {
      svarEl.innerHTML = html;
      panelEl.classList.add("oppen");
    }
    document.getElementById("stangpanel").addEventListener("click", function () {
      panelEl.classList.remove("oppen");
    });

    function hamtaPosition(automatiskt) {
      if (!navigator.geolocation) {
        visaPanel(svarBlock("tacks-ej", "Positionsbestämning saknas i den här webbläsaren",
          "<p>Sök upp området på kartan i stället.</p>"));
        return;
      }
      status("Hämtar din position …");
      navigator.geolocation.getCurrentPosition(function (pos) {
        var lat = pos.coords.latitude, lon = pos.coords.longitude;
        minPosition = [lon, lat];
        if (positionsMarkor) karta.removeLayer(positionsMarkor);
        positionsMarkor = L.circleMarker([lat, lon], {
          radius: 7, color: "#0b4f8a", weight: 3, fillColor: "#fff", fillOpacity: 1
        }).bindTooltip("Din position").addTo(karta);
        karta.setView([lat, lon], 12);
        svaraForPosition(lon, lat);
      }, function (err) {
        status("");
        if (!automatiskt) {
          visaPanel(svarBlock("tacks-ej", "Positionen kunde inte hämtas",
            "<p>" + esc(err.message) + " Sök upp området på kartan i stället.</p>"));
        }
      }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 });
    }
    document.getElementById("positionsknapp")
      .addEventListener("click", function () { hamtaPosition(false); });

    window.DK_positionssvar = svaraForPosition;   // används av testkörningar

    function svarBlock(klass, rubrik, brodtext) {
      return '<div class="svar svar-' + klass + '"><strong>' + esc(rubrik) +
             "</strong>" + brodtext + "</div>";
    }

    function svaraForPosition(lon, lat) {
      visaPanel('<p class="laddstatus">Kontrollerar mot områdesgränserna …</p>');
      // 3×3 rutor: mittrutan räcker för att avgöra om punkten ligger inne i ett
      // område, men grannarna behövs för att kunna säga vad som ligger nära.
      var mx = Math.floor(lon / GEOM_RUTA), my = Math.floor(lat / GEOM_RUTA);
      var rid = mx + "_" + my, grannar = [];
      for (var gx = -1; gx <= 1; gx++) {
        for (var gy = -1; gy <= 1; gy++) grannar.push((mx + gx) + "_" + (my + gy));
      }
      Promise.all([
        Promise.all(grannar.map(function (g) {
          return hamta(DATA + "/bbox/" + g + ".json").catch(function () { return { rader: [] }; });
        })).then(function (svar) {
          var rader = [];
          svar.forEach(function (x) { rader = rader.concat(x.rader || []); });
          return { rader: rader };
        }),
        Promise.all(grannar.map(function (g) {
          return hamta(DATA + "/geom/" + g + ".json").catch(function () { return {}; });
        })).then(function (svar) {
          var celler = { omraden: {}, stora: [] };
          svar.forEach(function (c) {
            Object.assign(celler.omraden, c.omraden || {});
            celler.stora = celler.stora.concat(c.stora || []);
          });
          return celler;
        })
      ])
        .then(function (svar) {
          var idx = svar[0], cell = svar[1];
          var omraden = cell.omraden || {};
          var kandidater = (idx.rader || []).filter(function (r) {
            return lon >= r[6] && lon <= r[8] && lat >= r[7] && lat <= r[9];
          });
          // Stora områden ligger i egna filer — hämta bara de som är kandidater.
          var storaKvar = (cell.stora || []).filter(function (nv) {
            return kandidater.some(function (r) { return r[0] === nv; });
          });
          return Promise.all(storaKvar.map(function (nv) {
            return hamta(DATA + "/geom/stora/" + nv + ".json")
              .then(function (g) { omraden[nv] = g; })
              .catch(function () {});
          })).then(function () {
            return {
              traffar: kandidater.filter(function (r) {
                return punktIGeometri(lon, lat, omraden[r[0]]);
              }),
              rader: idx.rader || [],
              geometrier: omraden
            };
          });
        })
        .then(function (res) {
          var traffar = res.traffar;
          // Kolumnordning enligt data/bbox-index.json: nvrid, slug, namn,
          // skyddstyp, lager, svarsläge, minx, miny, maxx, maxy.
          visaSvar(lon, lat, traffar.map(function (r) {
            return { nvrid: r[0], slug: r[1], namn: r[2], skyddstyp: r[3],
                     antalCitat: r[10] };
          }), res.rader, res.geometrier);
          status("");
        })
        .catch(function (e) {
          visaPanel(svarBlock("tacks-ej", "Kontrollen kunde inte genomföras",
            "<p>" + esc(e.message) +
            " Tolka inte detta som att inget är reglerat på platsen.</p>"));
        });
    }

    function avstandTillBbox(lon, lat, bb) {
      // Avstånd till rektangeln. Noll om punkten ligger i den. Alltid ≤
      // avståndet till själva geometrin, alltså en försiktig uppskattning.
      var dx = Math.max(bb[0] - lon, 0, lon - bb[2]);
      var dy = Math.max(bb[1] - lat, 0, lat - bb[3]);
      var nx = dx ? avstandM(lon, lat, lon + dx, lat) : 0;
      var ny = dy ? avstandM(lon, lat, lon, lat + dy) : 0;
      return Math.sqrt(nx * nx + ny * ny);
    }

    /* Närliggande områden hämtas ur bbox-rutorna runt punkten, inte ur det som
     * råkar vara utritat på kartan. Den tidigare varianten läste de ritade
     * ytorna, och när kartan var utzoomad fanns bara områden över 50 ha där —
     * svaret påstod då att närmaste område låg 3,3 km bort när det i själva
     * verket låg 940 m bort. Vad kartan visar och vad som finns är två skilda
     * saker.
     *
     * Avståndet mäts mot originalgeometrin när den finns i de hämtade rutorna,
     * annars mot områdets omslutande rektangel — vilket ger ett kortare, alltså
     * försiktigare, avstånd. */
    function narliggande(lon, lat, uteslut, rader, geometrier) {
      var lista = [];
      rader.forEach(function (r) {
        if (uteslut.indexOf(r[0]) !== -1) return;
        var g = geometrier[r[0]];
        var d = g ? narmasteHorn(lon, lat, g) : avstandTillBbox(lon, lat, r.slice(6, 10));
        if (d < 10000) {
          lista.push({ nvrid: r[0], slug: r[1], namn: r[2], skyddstyp: r[3],
                       antalCitat: r[10], d: d, exakt: !!g });
        }
      });
      lista.sort(function (a, b) { return a.d - b.d; });
      return lista.slice(0, 6);
    }

    function visaSvar(lon, lat, traffar, rader, geometrier) {
      var h = "";
      if (traffar.length) {
        var rader = traffar.map(function (o) {
          return '<li><a href="/omrade/' + o.nvrid + "-" + o.slug + '/">' +
            esc(o.namn) + "</a> — " + esc(o.skyddstyp) +
            (o.antalCitat ? " · " + o.antalCitat + " citat ur beslutet"
                          : " · läs beslutet") + "</li>";
        }).join("");
        h += svarBlock("reglerat", "Reglerat område — läs beslutet",
          "<p>Du står i " + traffar.length +
          (traffar.length === 1 ? " område" : " områden") +
          " med myndighetsbeslut.</p><ul class=\"lista-omraden\">" + rader + "</ul>");
      } else {
        h += svarBlock("ingen", "Ingen restriktion hittad i de källor tjänsten täcker",
          "<p>Positionen ligger inte inom något skyddat område i tjänstens databas. " +
          "Det är ett besked om vad databasen innehåller — inte om din flygning.</p>");
      }
      h += svarBlock("tacks-ej", "Denna källa täcks inte här",
        '<p>Luftrum (flygplatser, restriktionsområden, NOTAM) visas i LFV-lagret och ' +
        'på LFV:s drönarkarta — kontrollera alltid det separat: ' +
        '<a href="' + LFV.dronechart + '" rel="noopener">' + LFV.dronechart + "</a>.</p>");

      var nara = narliggande(lon, lat, traffar.map(function (o) { return o.nvrid; }),
                             rader || [], geometrier || {});
      if (nara.length) {
        h += "<h2>Närliggande</h2><ul class=\"lista-omraden\">" +
          nara.map(function (n) {
            return '<li><a href="/omrade/' + n.nvrid + "-" + n.slug + '/">' +
              esc(n.namn) + "</a> <span class=\"avstand\">ca " +
              formatAvstand(n.d) + "</span></li>";
          }).join("") + "</ul>";
      }
      h += '<p class="ansvar">' + (window.DK_ANSVARSTEXT || "") + "</p>";
      visaPanel(h);
    }

    /* ------------------------------------------------------------ lager */
    var lfvRuta = document.getElementById("lfvtoggle");
    if (lfvRuta.checked) lfv.addTo(karta);
    lfvRuta.addEventListener("change", function () {
      if (lfvRuta.checked) lfv.addTo(karta); else karta.removeLayer(lfv);
    });
    document.getElementById("lagerknapp").addEventListener("click", function () {
      document.getElementById("lagerpanel").classList.toggle("oppen");
    });

    laddaForVy();
    hamtaPosition(true);       // fråga direkt — appen handlar om var du står
  }

  /* --------------------------------------------------------- minikartan */
  function minikarta() {
    var el = document.getElementById("minikarta");
    if (!el) return;
    var nvrid = el.getAttribute("data-nvrid");
    var rid = el.getAttribute("data-ruta");
    var farg = el.getAttribute("data-farg") || "#2e7d32";
    var karta = L.map("minikarta", { scrollWheelZoom: false });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    }).addTo(karta);

    function rita(geom) {
      if (!geom) {
        el.innerHTML = "<p>Området saknar kartgeometri i källdatan.</p>";
        return;
      }
      var lager = L.geoJSON(geom, {
        style: { color: farg, weight: 2, fillColor: farg, fillOpacity: 0.25 }
      }).addTo(karta);
      karta.fitBounds(lager.getBounds(), { padding: [12, 12] });
      L.tileLayer.wms(LFV.url, {
        layers: LFV_LAGER, format: "image/png", transparent: true,
        version: "1.1.1", opacity: 0.7, attribution: LFV.attribution
      }).addTo(karta);
    }

    hamta(DATA + "/geom/" + rid + ".json").then(function (d) {
      var geom = (d.omraden || {})[nvrid];
      if (geom) return rita(geom);
      // Stora områden ligger i egen fil.
      return hamta(DATA + "/geom/stora/" + nvrid + ".json").then(rita);
    }).catch(function () { rita(null); });
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (document.getElementById("karta")) kartappen();
    if (document.getElementById("minikarta")) minikarta();
  });
})();
