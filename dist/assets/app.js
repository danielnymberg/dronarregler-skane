/* Drönarkoll — kartklient.
 *
 * Appen svarar på en enda fråga: vad säger myndigheternas egna handlingar om
 * luftfart där du står?
 *
 * Svaret rangordnas i fem lägen. Färgen speglar vad KÄLLAN SÄGER, aldrig
 * tjänstens bedömning, och inget läge är ett klartecken:
 *
 *   1 röd     Luftrumszon hos LFV, eller en naturföreskrift som nämner
 *             luftfart ordagrant. Citatet visas direkt i panelen.
 *   2 orange  Störningsförbud — en föreskrift som kan träffa en drönare utan
 *             att nämna den. Citatet visas.
 *   3 blå     Beslutet är läst och innehåller ingenting om luftfart.
 *   4 grå     Beslutet är INTE läst, eller saknas digitalt. En lucka, inte ett
 *             svar. Rankas som mer oroande än läge 3.
 *   5 tom     Ingen träff i tjänstens källor.
 *
 * Läge 3 och 4 var samma sak i förra versionen ("lanklage", 5 485 områden).
 * Att visa "vi har läst och inte funnit något" och "vi har inte läst" likadant
 * är precis den otydlighet tjänsten finns till för att slippa.
 *
 * Regel R3: all geometri kommer ur myndighets-API. Klienten ritar inga egna
 * buffertar eller cirklar. Punkt-i-polygon körs mot ORIGINALGEOMETRIN i
 * data/geom/, aldrig mot den förenklade visningsgeometrin i data/rutor/.
 */
(function () {
  "use strict";

  var DATA = window.DK_DATA_BAS || "/data";
  var LFV = window.DK_LFV || {};
  var LFV_WMS_LAGER = "mais:CTR,mais:TIZ,mais:TIA,mais:ATZ,mais:RSTA,mais:DNGA";
  var VISNING_RUTA = 0.5;
  var GEOM_RUTA = 0.25;
  var MAX_RUTOR = 30;

  /* Rangordning. Lägre tal = mer angeläget. Notera att "inte läst" (2) rankas
   * före "läst, inget om luftfart" (3): en lucka i underlaget ska aldrig se
   * lugnare ut än ett faktiskt besked. */
  var RANG = {
    /* En LFV-zon som nar marken beror varje hojd och ar niva 0. En zon vars
     * underkant ligger hogt gor det inte — den fick tidigare samma rodmarkering,
     * vilket ar precis den trubbiga larmning uppdraget raknade som felklass 3.
     * ES R129 Helsingborg borjar pa 400 ft och malade hela staden rod. */
    lfv: 0, "lfv-hog": 2, luftfart: 0, storning: 1,
    olast: 2, "utan-dokument": 2,
    "last-annat": 3, "last-tomt": 3
  };
  function rangFor(l) { return RANG[l] == null ? 4 : RANG[l]; }

  /* Två rubriker per nivå, och skillnaden är inte kosmetisk.
   *
   * Ett citat som saknar verifierad föreskriftsinledning kan komma ur beslutets
   * SKÄL i stället för ur dess föreskrifter. Västra Kullaberg är fallet som
   * visade det: citatet lyder "I området finns miljöer som kan utgöra
   * häckningsplatser för fåglar …", vilket är bakgrundstext — men rubriken sa
   * "Föreskriften förbjuder störning". Rubriken påstod mer än citatet.
   *
   * Finns inledningen är det belagt att texten står under en föreskriftsrubrik,
   * och då får ordet "föreskrift" användas. Saknas den beskriver rubriken bara
   * vad som står i beslutet. Nivån ändras inte — det är fortfarande värt att
   * stanna upp för — men påståendet gör det. */
  var LAGE = {
    0: { klass: "forbud",
         rubrik: "Här finns en regel som gäller drönare",
         svag: "Beslutet här nämner luftfart" },
    1: { klass: "storning",
         rubrik: "Här finns en föreskrift som kan träffa en drönare",
         svag: "Beslutet här nämner störning av djurlivet" },
    2: { klass: "okant", rubrik: "Skyddat område — beslutet är inte läst" },
    3: { klass: "last", rubrik: "Skyddat område — beslutet nämner inget om luftfart" },
    4: { klass: "ingen", rubrik: "Ingen träff i de källor tjänsten täcker" }
  };

  /* Är påståendet "föreskrift" belagt? En LFV-zon är alltid en zon, så där
   * räcker zonen själv. För ett naturbeslut krävs en verifierad inledning. */
  function foreskriftBelagd(res) {
    return res.poster.some(function (p) {
      if (p.rang > 1) return false;
      if (p.typ === "lfv") return true;
      return (p.citat || []).some(function (c) { return !!c.i; });
    });
  }

  var FARG = {
    nationalpark: "#1b6b3a", naturreservat: "#2e7d32",
    "naturreservat-kommunalt": "#4a7c1f", naturvardsomrade: "#00695c",
    "djur-och-vaxtskydd": "#b34700", kulturreservat: "#6a4f9c",
    "interimistiskt-forbud": "#8e0000", naturminne: "#00695c",
    vattenskyddsomrade: "#0d6e9c", landskapsbildsskydd: "#7a6a2f",
    biotopskydd: "#4c6b2f"
  };
  /* Kartan färgas efter vad beslutet säger om luftfart, inte efter skyddsform.
   * Skyddsformen är ointressant för den som ska lyfta; föreskriften är det. */
  var LAGEFARG = {
    luftfart: "#c62828", storning: "#e07b00",
    olast: "#7a7a7a", "utan-dokument": "#7a7a7a",
    "last-annat": "#3f6fa8", "last-tomt": "#3f6fa8"
  };
  var LFVFARG = { CTR: "#b3003c", TIZ: "#b3003c", ATZ: "#b3003c",
                  RSTA: "#8e0000", DNGA: "#a35200", TIA: "#7a5a8e" };

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

  /* ===================================================== LFV:s luftrum ====
   * Hela Sveriges luftrum är 259 polygoner, ~200 kB. Det bärs i minnet och i
   * service workerns cache, vilket gör att zonfrågan kan besvaras utan nät —
   * förutsättningen för att vaktläget ska hinna varna innan du lyfter. */
  var lfvZoner = null;
  var lfvMeta = null;
  function laddaLfv() {
    if (lfvZoner) return Promise.resolve(lfvZoner);
    return hamta(DATA + "/lfv.json").then(function (d) {
      lfvMeta = d;
      lfvZoner = d.zoner || [];
      return lfvZoner;
    }).catch(function () { lfvZoner = []; return lfvZoner; });
  }
  function lfvTraffar(lon, lat) {
    if (!lfvZoner) return [];
    return lfvZoner.filter(function (z) {
      var b = z.bbox;
      if (lon < b[0] || lon > b[2] || lat < b[1] || lat > b[3]) return false;
      return punktIGeometri(lon, lat, z.geometri);
    });
  }

  /* ============================================ luftfartscitat offline ==== */
  var luftfartsCitat = null;
  function laddaLuftfart() {
    if (luftfartsCitat) return Promise.resolve(luftfartsCitat);
    return hamta(DATA + "/luftfart.json").then(function (d) {
      luftfartsCitat = d.citat || {};
      return luftfartsCitat;
    }).catch(function () { luftfartsCitat = {}; return luftfartsCitat; });
  }

  /* ================================================ de allmänna reglerna ==
   * 14 avsnitt ur 6 författningar, 14 kB. Bärs offline vid sidan av lfv.json
   * och luftfart.json — de flesta regler som fäller en drönarpilot är inte
   * platsbundna, och de ska därför visas ÄVEN när platsen är tyst. Ingen punkt
   * i Sverige är regelfri. */
  var reglerData = null;
  function laddaRegler() {
    if (reglerData) return Promise.resolve(reglerData);
    return hamta(DATA + "/regler.json").then(function (d) {
      reglerData = d;
      return d;
    }).catch(function () { reglerData = { avsnitt: [], kallor: {} }; return reglerData; });
  }

  /* ================================== kommunala föreskrifter, per kommun ==
   * Registret generaliserar inte. En kommun som inte står i det är INTE
   * kontrollerad, vilket är något annat än att den saknar regler — och det är
   * skillnaden luckredovisningen ska visa. */
  var kommunData = null;
  function laddaKommunala() {
    if (kommunData) return Promise.resolve(kommunData);
    return hamta(DATA + "/kommunala-foreskrifter.json").then(function (d) {
      kommunData = d;
      return d;
    }).catch(function () { kommunData = { kommuner: {} }; return kommunData; });
  }

  /* ====================================================== drönarprofiler ==
   * Vilka avståndsregler som gäller beror på drönarens klassmärkning och vikt.
   * Det är en uppgift DU har och tjänsten inte har — precis som vägmärkets
   * "gäller ej över 3,5 t" förutsätter att föraren vet vad fordonet väger.
   *
   * Flera profiler stöds, för de flesta har mer än en drönare. Den aktiva
   * profilen visas i varje svar och kan aldrig döljas: risken Daniel själv
   * pekade ut är att profilen står på 249 g medan drönaren i väskan väger 500. */
  var PROFIL_NYCKEL = "dk-profiler-1";
  var profiler = { lista: [], aktiv: -1 };

  function laddaProfiler() {
    try {
      var r = JSON.parse(localStorage.getItem(PROFIL_NYCKEL) || "null");
      if (r && Array.isArray(r.lista)) profiler = r;
    } catch (e) { /* trasig lagring — kör vidare utan profil */ }
  }
  function sparaProfiler() {
    try { localStorage.setItem(PROFIL_NYCKEL, JSON.stringify(profiler)); } catch (e) { }
  }
  function aktivProfil() {
    return profiler.aktiv >= 0 ? profiler.lista[profiler.aktiv] || null : null;
  }

  /* Härledningen är ren avbildning av förordningstexten, inte en bedömning.
   * Klassmärkta drönare enligt UAS.OPEN.020/030/040, omärkta enligt artikel 20.
   * `varfor` visas i gränssnittet så att härledningen går att kontrollera. */
  function underkategori(p) {
    if (!p) return null;
    var k = String(p.klass || "").toUpperCase();
    var g = Number(p.gram) || 0;
    if (k === "C0") return { kat: "A1", varfor: "klass C0" };
    if (k === "C1") return { kat: "A1", varfor: "klass C1" };
    if (k === "C2") return { kat: "A2", varfor: "klass C2 (får även flygas i A3)" };
    if (k === "C3") return { kat: "A3", varfor: "klass C3" };
    if (k === "C4") return { kat: "A3", varfor: "klass C4" };
    if (g > 0 && g < 250) return { kat: "A1", varfor: "omärkt, under 250 g — artikel 20 a" };
    if (g >= 250 && g < 25000) return { kat: "A3", varfor: "omärkt, " + g + " g — artikel 20 b" };
    if (g >= 25000) return { kat: null, varfor: "över 25 kg — inte öppen kategori" };
    return null;
  }

  function profilChip() {
    var p = aktivProfil();
    var uk = underkategori(p);
    if (!p) {
      return '<button class="profilchip tom" id="profilknapp">' +
        "Ingen drönare vald — alla underkategorier visas ▾</button>";
    }
    return '<button class="profilchip" id="profilknapp">' +
      '<span class="pnamn">' + esc(p.namn || "Drönare") + "</span>" +
      '<span class="pdet">' + esc(p.klass || "omärkt") + " · " + esc(p.gram) + " g" +
      (uk && uk.kat ? " · " + esc(uk.kat) : "") + "</span> ▾</button>" +
      (uk ? '<p class="pharledning">' + esc(uk.varfor) + "</p>" : "");
  }

  /* --------------------------------------------- regelinventeringen ------
   * Svarets andra halva: en checklista över de regler som gäller, som en
   * skyltinventering. Raden visar kategorin (statisk text som säger VAD raden
   * handlar om) och en ORDAGRANN nyckelfras ur författningen — aldrig en
   * sammanfattning. Hela citatet fälls ut. */
  function regelinventering() {
    if (!reglerData || !reglerData.avsnitt.length) return "";
    var uk = underkategori(aktivProfil());
    var galler = [], ovriga = [];
    reglerData.avsnitt.forEach(function (a) {
      // Utan vald profil visas ALLA underkategorier. Att fälla ihop dem när
      // tjänsten inte vet vilken som gäller vore att dölja regler den inte har
      // grund att sortera bort.
      var traff = !a.galler_underkategori || !uk || !uk.kat ||
        a.galler_underkategori.indexOf(uk.kat) >= 0;
      (traff ? galler : ovriga).push(a);
    });

    function rad(a) {
      var k = reglerData.kallor[a.kalla] || {};
      return "<details class='regelrad'><summary>" +
        '<span class="rkat">' + esc(a.kategori) + "</span>" +
        '<span class="rfras">' + esc(a.nyckelfras || a.fraga) + "</span>" +
        '<span class="rkalla">' + esc(k.kortnamn || "") + " " + esc(a.referens) +
        "</span></summary>" +
        '<blockquote>' + esc(a.text) + "</blockquote>" +
        '<p class="kallrad"><a href="/regler/#' + esc(a.id) + '">' +
        esc(k.titel || "") + " " + esc(a.referens) + "</a></p></details>";
    }

    var h = '<h2 class="invrubrik">Regler som gäller här</h2>' +
      '<p class="notis">Gäller oavsett plats. Klicka för författningstexten ' +
      "ordagrant.</p>" + profilChip() +
      '<div class="regelinv">' + galler.map(rad).join("") + "</div>";
    if (ovriga.length) {
      h += "<details class='ovrigareg'><summary>" + ovriga.length +
        " regler som gäller andra underkategorier</summary><div class='regelinv'>" +
        ovriga.map(rad).join("") + "</div></details>";
    }
    return h;
  }

  /* ================================================== ljud och vibration ==
   * Tonen skapas i WebAudio i stället för att laddas som fil: den fungerar
   * offline utan att något behöver cachas, och AudioContext:en skapas vid
   * användarens tryck på vaktknappen, vilket iOS kräver. */
  var ljud = null;
  function forberedLjud() {
    if (ljud) return ljud;
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    try { ljud = new AC(); } catch (e) { return null; }
    return ljud;
  }
  function pip(antal) {
    var ac = ljud;
    if (ac) {
      if (ac.state === "suspended") ac.resume();
      for (var i = 0; i < (antal || 3); i++) {
        var osc = ac.createOscillator(), g = ac.createGain();
        var t = ac.currentTime + i * 0.28;
        osc.type = "square";
        osc.frequency.setValueAtTime(880, t);
        g.gain.setValueAtTime(0.0001, t);
        g.gain.exponentialRampToValueAtTime(0.25, t + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t + 0.2);
        osc.connect(g); g.connect(ac.destination);
        osc.start(t); osc.stop(t + 0.22);
      }
    }
    if (navigator.vibrate) navigator.vibrate([180, 90, 180, 90, 180]);
  }

  /* ============================================================ bedömning */
  /* ---------------------------------------------------- handlingsöversikt --
   * "Vilka områden träffar min position" är fel fråga — svaret blir en hög att
   * tolka. "Får jag lyfta här, landa här, flyga över" är rätt fråga. Samma
   * citat besvarar bägge; det är grupperingen som skiljer.
   *
   * Raden säger vad KÄLLORNA NÄMNER om handlingen, aldrig om den är tillåten.
   * "Inget hittat" betyder att tjänstens källor är tysta — inte att det är fritt. */
  var HANDLINGAR = [
    { id: "lyfta", ord: "Lyfta här" },
    { id: "landa", ord: "Landa här" },
    { id: "flyga", ord: "Flyga över" },
    { id: "marken", ord: "Vara här med utrustningen" }
  ];

  function handlingsoversikt(poster) {
    var per = { lyfta: [], landa: [], flyga: [], marken: [] };
    poster.forEach(function (p) {
      if (p.typ === "lfv") {
        // En luftrumszon som når marken berör alla tre luftburna handlingar.
        // En zon som börjar högt berör bara flygning, och bara villkorat —
        // om du alls kommer upp i den beror på terränghöjden.
        if (p.zon.nar_marken) {
          ["lyfta", "landa", "flyga"].forEach(function (h) {
            per[h].push({ namn: p.zon.namn, villkorad: false });
          });
        } else {
          per.flyga.push({ namn: p.zon.namn + " (från " + p.zon.underkant_m + " m ö.h.)",
                           villkorad: true });
        }
        return;
      }
      (p.citat || []).forEach(function (c) {
        (c.h || []).forEach(function (h) {
          if (!per[h]) return;
          if (per[h].some(function (x) { return x.namn === p.omrade.namn; })) return;
          per[h].push({ namn: p.omrade.namn, villkorad: false });
        });
      });
    });
    return HANDLINGAR.map(function (h) {
      return { ord: h.ord, kallor: per[h.id] };
    });
  }

  function handlingsHtml(rader) {
    return '<ul class="handlingar">' + rader.map(function (r) {
      if (!r.kallor.length) {
        return '<li class="tyst"><span class="hnamn">' + esc(r.ord) + "</span>" +
          '<span class="hsvar">inget hittat i källorna</span></li>';
      }
      // Villkorad = alla källor bakom raden är höga luftrumszoner. Då är
      // träffen beroende av vilken höjd du faktiskt flyger på.
      var villkorad = r.kallor.every(function (k) { return k.villkorad; });
      var namn = r.kallor.map(function (k) { return k.namn; });
      return '<li class="' + (villkorad ? "villkorad" : "traff") + '">' +
        '<span class="hnamn">' + esc(r.ord) + "</span>" +
        '<span class="hsvar">' + esc(namn.slice(0, 2).join(", ")) +
        (namn.length > 2 ? " +" + (namn.length - 2) : "") + "</span></li>";
    }).join("") + "</ul>";
  }

  /* ------------------------------------------------------- luckredovisning --
   * Det som INTE kontrollerades ska stå i svaret, inte i en fotnot någon
   * annanstans. Annars läses tystnad som klartecken, vilket är hela felet
   * tjänsten finns till för att undvika. */
  /* Kommunraden skiljer tre lägen åt: kontrollerad utan träff, kontrollerad
   * MED träff, och inte kontrollerad. Den tredje är en lucka och ska aldrig
   * se ut som den första. */
  function kommunrad(kommun) {
    var reg = (kommunData && kommunData.kommuner) || {};
    /* Kommunfältet är FLERVÄRT — ett område kan spänna över flera kommuner
     * ("Båstad, Halmstad, Höganäs"). Står man utanför alla områden finns ingen
     * kommunuppgift alls; att då plocka kommunen från ett godtyckligt
     * grannområde gav Helsingborg svaret "Båstad, Halmstad, Höganäs".
     * Tjänsten gissar därför inte kommun. */
    var namn = String(kommun || "").split(/\s*,\s*/).filter(Boolean)
      .filter(function (n) { return reg[n]; });

    if (!namn.length) {
      var lista = Object.keys(reg).filter(function (n) {
        return reg[n].status === "kontrollerad";
      });
      return "Kommunala dokument — <strong>inte kontrollerade för din kommun</strong>. " +
        "Kommunen får inte reglera luftrummet, men råder över marken: " +
        "parkreglementen och badplatsföreskrifter kan träffa lyft och landning." +
        (lista.length ? " Hittills kontrollerade: " + esc(lista.join(", ")) + "." : "");
    }

    return namn.map(function (n) {
      var k = reg[n];
      var dok = (k.dokument || []).filter(function (d) {
        return d.status === "kontrollerad";
      });
      var bitar = dok.map(function (d) {
        var traff = (d.luftfartstraffar || []).length;
        return '<li><a href="' + esc(d.url) + '" rel="noopener">' + esc(d.namn) +
          "</a> — " +
          (d.typ === "intern"
            ? "kommunens egna rutiner, binder inte en privat pilot"
            : "föreskrift") +
          ". Kontrollerad " + esc(d.kontrollerad) + ". " +
          (traff
            ? "<strong>" + traff + " träffar på drönare eller luftfart.</strong>"
            : "Ingenting om drönare eller farkost.") + "</li>";
      }).join("");
      return "<strong>" + esc(n) + "</strong> — " + dok.length +
        (dok.length === 1 ? " dokument kontrollerat" : " dokument kontrollerade") +
        ":<ul class='kommundok'>" + bitar + "</ul>";
    }).join("");
  }

  function luckorHtml(kommun) {
    var rader = [
      "Tillfälliga restriktioner och NOTAM — kontrollera hos " +
        '<a href="' + esc(LFV.dronechart) + '" rel="noopener">LFV</a>',
      kommunrad(kommun),
      "Skyddsobjekt enligt skyddslagen — var de ligger publiceras inte samlat",
      "Markägarens medgivande för lyft och landning",
      "Natura 2000"
    ];
    return '<details class="luckor"><summary>Detta kontrollerades inte</summary>' +
      "<ul><li>" + rader.join("</li><li>") + "</li></ul>" +
      '<p>Tjänsten täcker skyddad natur ur Naturvårdsregistret, luftrummet ur ' +
      'LFV:s DAIM och de författningar som listas på <a href="/regler/">' +
      "reglerna</a>. Övrigt måste du kontrollera själv.</p></details>";
  }

  function bedom(lon, lat, omradesTraffar, citatPerNvrid, kommun) {
    var zoner = lfvTraffar(lon, lat);
    var poster = [];

    zoner.forEach(function (z) {
      poster.push({ typ: "lfv", zon: z,
                    rang: rangFor(z.nar_marken ? "lfv" : "lfv-hog") });
    });
    omradesTraffar.forEach(function (o) {
      var lage = o.luftfartslage || "utan-dokument";
      poster.push({
        typ: "omrade", rang: rangFor(lage), lage: lage, omrade: o,
        citat: (citatPerNvrid && citatPerNvrid[o.nvrid]) || []
      });
    });

    poster.sort(function (a, b) { return a.rang - b.rang; });
    var niva = poster.length ? poster[0].rang : 4;
    return { niva: niva, poster: poster, zoner: zoner };
  }

  /* ============================================================ rendering */
  /* Märker ut de ord som gjorde att citatet valdes ut.
   *
   * Ett citat är ofta en halv sida beslutstext. Utan märkning måste man läsa
   * alltihop för att se varför det kom upp — och det var den befogade
   * invändningen mot första versionen: tjänsten lämnade över hela läsbördan.
   *
   * Texten ändras inte. Ingenting stryks, ingenting skrivs om, ordföljden är
   * orörd. Det enda som händer är att urvalsgrunden görs synlig. */
  function markera(text, ord) {
    var ut = esc(text);
    (ord || []).forEach(function (o) {
      if (!o || o.length < 3) return;
      var n = esc(o), i = 0, bit = "";
      var jamfor = ut.toLowerCase(), sok = n.toLowerCase();
      while (true) {
        var p = jamfor.indexOf(sok, i);
        if (p < 0) { bit += ut.slice(i); break; }
        // Hoppa över träffar inuti taggar vi redan lagt in.
        if (ut.lastIndexOf("<", p) > ut.lastIndexOf(">", p)) {
          bit += ut.slice(i, p + sok.length); i = p + sok.length; continue;
        }
        // Bara vid ordbörjan — "fordon" inuti "terrängfordon" ska inte ge
        // "terräng[fordon]", det läser som ett stavfel.
        if (p > 0 && /[A-Za-zÀ-ÿ-]/.test(ut.charAt(p - 1))) {
          bit += ut.slice(i, p + sok.length); i = p + sok.length; continue;
        }
        bit += ut.slice(i, p) + "<mark>" + ut.slice(p, p + sok.length) + "</mark>";
        i = p + sok.length;
      }
      ut = bit;
    });
    return ut;
  }

  function citatHtml(c) {
    var kalla = [];
    if (c.p) kalla.push("punkt " + esc(c.p));
    if (c.s) kalla.push("s. " + esc(c.s));
    var lank = c.u ? '<a href="' + esc(c.u) + '" rel="noopener">' +
                     esc(c.d || "beslutet") + "</a>" : esc(c.d || "");
    return '<figure class="citat">' +
      (c.i ? '<p class="inledning">' + markera(c.i, c.w) + "</p>" : "") +
      "<blockquote>" + markera(c.t, c.w) + "</blockquote>" +
      '<figcaption>' + (kalla.length ? esc(kalla.join(" · ")) + " · " : "") + lank +
      (c.o ? ' <span class="ocr">texttolkad bild</span>' : "") +
      "</figcaption></figure>";
  }

  /* Höjden med källvärdet först och metern inom parentes.
   *
   * Ett oomräknat "400" är oanvändbart för den vars drönare visar meter, och
   * omräkningen är exakt aritmetik (1 ft = 0,3048 m) — inte en bedömning. */
  function hojdText(z) {
    var kalla = (z.underkant || "?") + "–" + (z.overkant || "?");
    if (z.underkant_m == null && z.overkant_m == null) return esc(kalla);
    var u = z.underkant_m == null ? "?" : z.underkant_m;
    var o = z.overkant_m == null ? "?" : z.overkant_m;
    var enhet = z.hojdreferens === "FL" ? "" : " ft";
    return esc(kalla) + enhet + ' <span class="meter">(' + esc(u) + "–" + esc(o) +
           " m)</span>";
  }

  function zonHtml(z) {
    var hog = !z.nar_marken;
    /* En zon vars underkant ligger över marken är den vanligaste orsaken till
     * falsklarm hos andra tjänster: hela Helsingborg målas rött av ES R129,
     * som börjar på 400 ft. Den renderas därför dämpat och med underkanten
     * utskriven — men den döljs aldrig, för jämförelsen mot drönarens 120 m
     * över MARKEN kräver terränghöjden, och den vet inte tjänsten. */
    return '<div class="post post-' + (hog ? "lfv-hog" : "lfv") + '">' +
      '<div class="posttopp"><span class="lagerkod lager-' + esc(z.lager) + '">' +
      esc(z.lager) + '</span><strong>' + esc(z.namn) + "</strong></div>" +
      (hog
        ? '<p class="hojdnot">Zonens underkant ligger <strong>' +
          esc(z.underkant_m) + " m över havet</strong>, inte vid marken. " +
          "Öppen kategori tillåter högst 120 m över <em>marken</em> — hur de " +
          "två förhåller sig beror på terrängens höjd där du står, vilket " +
          "tjänsten inte känner till.</p>"
        : "") +
      '<dl class="fakta">' +
      "<dt>Höjd</dt><dd>" + hojdText(z) +
      (z.hojdreferens === "AMSL" ? ' <span class="ref">över havet</span>'
       : z.hojdreferens === "GND" ? ' <span class="ref">från marken</span>' : "") +
      "</dd>" +
      (z.icao ? "<dt>Flygplats</dt><dd>" + esc(z.icao) + "</dd>" : "") +
      (z.galler_fran ? "<dt>Gäller från</dt><dd>" + esc(z.galler_fran) + "</dd>" : "") +
      "</dl>" +
      (z.kommentar ? '<blockquote class="lfvkommentar">' + esc(z.kommentar) +
                     "</blockquote>" : "") +
      '<p class="kallrad">Källa: LFV DAIM. ' +
      '<a href="' + esc(LFV.dronechart) + '" rel="noopener">LFV:s drönarkarta</a> · ' +
      '<a href="/regler/#luftrum">Vad säger reglerna om luftrum?</a></p>' +
      "</div>";
  }

  var LAGETEXT = {
    luftfart: "Föreskriften nämner luftfart ordagrant.",
    storning: "Föreskriften förbjuder störning. Den nämner inte drönare, men en " +
              "drönare kan träffas av den.",
    /* Utan verifierad föreskriftsinledning kan citatet lika gärna stå i
     * beslutets skäl. Då beskrivs det som det är. */
    "luftfart-svag": "Beslutet nämner luftfart ordagrant. Tjänsten har inte kunnat "
                     + "knyta citatet till en föreskriftspunkt — läs det i sitt "
                     + "sammanhang.",
    "storning-svag": "Beslutet nämner störning av djurlivet. Tjänsten har inte "
                     + "kunnat knyta citatet till en föreskriftspunkt — läs det i "
                     + "sitt sammanhang.",
    "last-annat": "Tjänsten har läst beslutet. Föreskrifterna handlar om annat än luftfart.",
    "last-tomt": "Tjänsten har läst beslutet utan att hitta någon föreskriftstext.",
    olast: "Beslutet finns men tjänsten har inte läst det ännu.",
    "utan-dokument": "Det finns inget digitalt beslut att läsa i registret."
  };

  function omradeHtml(p) {
    var o = p.omrade;
    var belagd = (p.citat || []).some(function (c) { return !!c.i; });
    var nyckel = (!belagd && (p.lage === "luftfart" || p.lage === "storning"))
      ? p.lage + "-svag" : p.lage;
    return '<div class="post post-' + esc(p.lage) + '">' +
      '<div class="posttopp"><span class="prick prick-' + esc(p.lage) + '"></span>' +
      '<strong><a href="/omrade/' + esc(o.nvrid) + "-" + esc(o.slug) + '/">' +
      esc(o.namn) + "</a></strong></div>" +
      '<p class="skyddstyp">' + esc(o.skyddstyp) + " — " +
      esc(LAGETEXT[nyckel] || "") + "</p>" +
      /* Första citatet syns, resten fälls ut. Ett beslut kan ha ett halvdussin
       * punkter som alla nämner luftfart — undantag för räddningstjänst,
       * undantag för förvaltaren — och staplade i full längd blir svaret en
       * vägg text man slutar läsa. Ingenting döljs: antalet står i knappen,
       * och alla citat finns dessutom på områdessidan. */
      p.citat.slice(0, 1).map(citatHtml).join("") +
      (p.citat.length > 1
        ? "<details class='flercitat'><summary>" + (p.citat.length - 1) +
          (p.citat.length === 2 ? " citat till" : " citat till") +
          " ur beslutet</summary>" +
          p.citat.slice(1).map(citatHtml).join("") + "</details>"
        : "") +
      (p.citat.length === 0 && o.antalCitat
        ? '<p class="kallrad"><a href="/omrade/' + esc(o.nvrid) + "-" + esc(o.slug) +
          '/">' + o.antalCitat + " citat ur beslutet</a></p>"
        : "") +
      "</div>";
  }

  /* =========================================================== kartappen */
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

    var omradeslager = L.layerGroup().addTo(karta);
    var lfvLager = L.layerGroup().addTo(karta);
    var laddadeRutor = {}, ritade = {};

    var statusEl = document.getElementById("kartstatus");
    var svarEl = document.getElementById("svar");
    var panelEl = document.getElementById("panel");

    function stil(f) {
      var c = LAGEFARG[f.properties.luftfartslage] ||
              FARG[f.properties.lager] || "#444";
      var stark = f.properties.luftfartslage === "luftfart";
      return { color: c, weight: stark ? 2.5 : 1.4, opacity: 0.95,
               fillColor: c, fillOpacity: stark ? 0.32 : 0.18 };
    }
    function popup(p) {
      return "<strong>" + esc(p.namn) + "</strong><br>" + esc(p.skyddstyp) +
        "<br>" + esc(LAGETEXT[p.luftfartslage] || "") +
        '<br><a href="/omrade/' + p.nvrid + "-" + p.slug + '/">Läs beslutet</a>';
    }
    function laggTill(features) {
      var nya = features.filter(function (f) { return !ritade[f.properties.nvrid]; });
      nya.forEach(function (f) { ritade[f.properties.nvrid] = true; });
      if (!nya.length) return;
      L.geoJSON({ type: "FeatureCollection", features: nya }, {
        style: stil,
        onEachFeature: function (f, l) { l.bindPopup(popup(f.properties)); }
      }).addTo(omradeslager);
    }
    function status(text) { statusEl.textContent = text || ""; }

    function laddaForVy() {
      var rutor = rutorForBounds(karta.getBounds(), VISNING_RUTA);
      if (rutor.length > MAX_RUTOR) {
        status("Zooma in för att se skyddade områden.");
        return;
      }
      var nya = rutor.filter(function (r) { return !laddadeRutor[r]; });
      if (!nya.length) { status(""); return; }
      status("Laddar …");
      return Promise.all(nya.map(function (r) {
        laddadeRutor[r] = true;
        return hamta(DATA + "/rutor/" + r + ".json")
          .then(function (gj) { laggTill(gj.features); })
          .catch(function () { });
      })).then(function () { status(""); }).catch(function (e) {
        status("Kartdata kunde inte laddas — tolka inte en tom karta som att "
               + "inget är reglerat. (" + e.message + ")");
      });
    }
    karta.on("moveend zoomend", laddaForVy);

    /* LFV-zonerna ritas ur samma vektordata som svaret bygger på. Det som syns
     * på kartan och det som panelen påstår är alltså samma sak — tidigare var
     * kartan ett rasterlager tjänsten själv inte kunde läsa. */
    function ritaLfv() {
      if (!lfvZoner || !lfvZoner.length) return;
      lfvZoner.forEach(function (z) {
        var c = LFVFARG[z.lager] || "#8e0000";
        L.geoJSON(z.geometri, {
          style: { color: c, weight: 1.6, opacity: 0.9, fillColor: c,
                   fillOpacity: z.nar_marken ? 0.14 : 0.05,
                   dashArray: z.nar_marken ? null : "5,5" }
        }).bindPopup("<strong>" + esc(z.namn) + "</strong><br>" + esc(z.lager) +
                     " " + esc(z.underkant) + "–" + esc(z.overkant) +
                     (z.kommentar ? "<br>" + esc(z.kommentar) : "") +
                     '<br><a href="/regler/#luftrum">Vad gäller här?</a>')
          .addTo(lfvLager);
      });
    }

    /* ---------------------------------------------------------- position */
    var positionsMarkor = null, noggrannhet = null;

    function visaPanel(html, klass) {
      svarEl.innerHTML = html;
      panelEl.className = "panel oppen" + (klass ? " niva-" + klass : "");
    }
    document.getElementById("stangpanel").addEventListener("click", function () {
      panelEl.classList.remove("oppen");
    });

    function markera(lon, lat, acc) {
      if (positionsMarkor) karta.removeLayer(positionsMarkor);
      if (noggrannhet) karta.removeLayer(noggrannhet);
      positionsMarkor = L.circleMarker([lat, lon], {
        radius: 7, color: "#0b4f8a", weight: 3, fillColor: "#fff", fillOpacity: 1
      }).bindTooltip("Din position").addTo(karta);
      /* Noggrannhetscirkeln kommer ur GPS:ens egen feluppskattning och ritas
       * bara som just det. Den är inte en zon och används aldrig i något svar
       * — att blanda ihop en uppskattad felmarginal med en regelgräns är hur
       * andra tjänster har hamnat fel. */
      if (acc && acc > 25) {
        noggrannhet = L.circle([lat, lon], {
          radius: acc, color: "#0b4f8a", weight: 1, opacity: 0.4,
          fillOpacity: 0.05, dashArray: "4,4", interactive: false
        }).addTo(karta);
      }
    }

    function hamtaPosition(automatiskt) {
      if (!navigator.geolocation) {
        visaPanel(svarBlock("okant", "Positionsbestämning saknas i webbläsaren",
          "<p>Sök upp platsen på kartan i stället.</p>"), "2");
        return;
      }
      visaPanel('<p class="laddstatus">Hämtar din position …</p>');
      navigator.geolocation.getCurrentPosition(function (pos) {
        var lat = pos.coords.latitude, lon = pos.coords.longitude;
        markera(lon, lat, pos.coords.accuracy);
        karta.setView([lat, lon], 12);
        svaraForPosition(lon, lat, pos.coords.accuracy);
      }, function (err) {
        if (automatiskt) { panelEl.classList.remove("oppen"); return; }
        visaPanel(svarBlock("okant", "Positionen kunde inte hämtas",
          "<p>" + esc(err.message) + " Sök upp platsen på kartan i stället.</p>"), "2");
      }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 });
    }
    document.getElementById("positionsknapp")
      .addEventListener("click", function () { hamtaPosition(false); });
    karta.on("contextmenu", function (e) {
      markera(e.latlng.lng, e.latlng.lat, null);
      svaraForPosition(e.latlng.lng, e.latlng.lat, null);
    });

    window.DK_positionssvar = svaraForPosition;   // används av testkörningar

    function svarBlock(klass, rubrik, brodtext) {
      return '<div class="svar svar-' + klass + '"><strong>' + esc(rubrik) +
             "</strong>" + brodtext + "</div>";
    }

    /* Hämtar allt positionssvaret behöver: kandidater, originalgeometri och
     * störningscitat för rutorna runt punkten. */
    function underlagFor(lon, lat) {
      var mx = Math.floor(lon / GEOM_RUTA), my = Math.floor(lat / GEOM_RUTA);
      var grannar = [];
      for (var gx = -1; gx <= 1; gx++) {
        for (var gy = -1; gy <= 1; gy++) grannar.push((mx + gx) + "_" + (my + gy));
      }
      var mitten = mx + "_" + my;
      return Promise.all([
        Promise.all(grannar.map(function (g) {
          return hamta(DATA + "/bbox/" + g + ".json").catch(function () { return { rader: [] }; });
        })),
        Promise.all(grannar.map(function (g) {
          return hamta(DATA + "/geom/" + g + ".json").catch(function () { return {}; });
        })),
        hamta(DATA + "/citat/" + mitten + ".json").catch(function () { return { citat: {} }; })
      ]).then(function (svar) {
        var rader = [];
        svar[0].forEach(function (x) { rader = rader.concat(x.rader || []); });
        var omraden = {}, stora = [];
        svar[1].forEach(function (c) {
          Object.assign(omraden, c.omraden || {});
          stora = stora.concat(c.stora || []);
        });
        return { rader: rader, omraden: omraden, stora: stora,
                 storningscitat: svar[2].citat || {} };
      });
    }

    function svaraForPosition(lon, lat, acc) {
      visaPanel('<p class="laddstatus">Kontrollerar …</p>');
      Promise.all([laddaLfv(), laddaLuftfart(), underlagFor(lon, lat), laddaRegler(),
                   laddaKommunala()])
        .then(function (allt) {
          var u = allt[2], omraden = u.omraden, sett = {};
          var kandidater = u.rader.filter(function (r) {
            if (sett[r[0]]) return false;
            if (!(lon >= r[6] && lon <= r[8] && lat >= r[7] && lat <= r[9])) return false;
            sett[r[0]] = true;
            return true;
          });
          var storaKvar = u.stora.filter(function (nv) {
            return kandidater.some(function (r) { return r[0] === nv; });
          });
          return Promise.all(storaKvar.map(function (nv) {
            return hamta(DATA + "/geom/stora/" + nv + ".json")
              .then(function (g) { omraden[nv] = g; }).catch(function () { });
          })).then(function () {
            // Kolumnordning enligt data/bbox-index.json.
            var traffar = kandidater
              .filter(function (r) { return punktIGeometri(lon, lat, omraden[r[0]]); })
              .map(function (r) {
                return { nvrid: r[0], slug: r[1], namn: r[2], skyddstyp: r[3],
                         antalCitat: r[10], luftfartslage: r[11], kommun: r[12] };
              });
            var citat = {};
            Object.assign(citat, u.storningscitat);
            Object.assign(citat, luftfartsCitat);
            // Kommunen tas BARA från ett område man faktiskt står i. Att
            // falla tillbaka på en godtycklig rad i rutan gav fel kommun —
            // Helsingborg fick "Båstad, Halmstad, Höganäs".
            var kommun = (traffar[0] && traffar[0].kommun) || "";
            visaSvar(lon, lat, traffar, u.rader, omraden, citat, acc, kommun);
          });
        })
        .catch(function (e) {
          visaPanel(svarBlock("okant", "Kontrollen kunde inte genomföras",
            "<p>" + esc(e.message) +
            " Tolka inte detta som att inget är reglerat på platsen.</p>"), "2");
        });
    }

    function avstandTillBbox(lon, lat, bb) {
      var dx = Math.max(bb[0] - lon, 0, lon - bb[2]);
      var dy = Math.max(bb[1] - lat, 0, lat - bb[3]);
      var nx = dx ? avstandM(lon, lat, lon + dx, lat) : 0;
      var ny = dy ? avstandM(lon, lat, lon, lat + dy) : 0;
      return Math.sqrt(nx * nx + ny * ny);
    }

    /* Närliggande områden hämtas ur bbox-rutorna, inte ur det som råkar vara
     * utritat. Den tidigare varianten läste de ritade ytorna och påstod att
     * närmaste område låg 3,3 km bort när det låg 940 m bort. */
    function narliggande(lon, lat, uteslut, rader, geometrier) {
      var lista = [], sedda = {};
      rader.forEach(function (r) {
        if (uteslut.indexOf(r[0]) !== -1 || sedda[r[0]]) return;
        sedda[r[0]] = true;
        var g = geometrier[r[0]];
        var d = g ? narmasteHorn(lon, lat, g) : avstandTillBbox(lon, lat, r.slice(6, 10));
        if (d < 10000) {
          lista.push({ nvrid: r[0], slug: r[1], namn: r[2], skyddstyp: r[3],
                       luftfartslage: r[11], d: d });
        }
      });
      lista.sort(function (a, b) { return a.d - b.d; });
      return lista.slice(0, 6);
    }

    function visaSvar(lon, lat, traffar, rader, geometrier, citat, acc, kommun) {
      sistaSvar = [lon, lat, traffar, rader, geometrier, citat, acc, kommun];
      var res = bedom(lon, lat, traffar, citat, kommun);
      var lage = LAGE[res.niva];
      var rubrik = (lage.svag && !foreskriftBelagd(res)) ? lage.svag : lage.rubrik;
      // Niva 2 delas av "beslut ej last" och "luftrumszon ovanfor dig". De ar
      // inte samma besked och far inte ha samma rubrik.
      if (res.niva === 2 && res.poster.length && res.poster[0].typ === "lfv") {
        rubrik = "Luftrumszon ovanför dig — beror på vilken höjd du flyger på";
      }
      var h = "";

      var underrubrik;
      if (res.niva === 4) {
        underrubrik = "Positionen ligger varken i ett skyddat område eller i en " +
                      "luftrumszon i tjänstens databas.";
      } else {
        var antalZon = res.zoner.length;
        var antalOmr = traffar.length;
        var delar = [];
        if (antalZon) delar.push(antalZon + (antalZon === 1 ? " luftrumszon" : " luftrumszoner"));
        if (antalOmr) delar.push(antalOmr + (antalOmr === 1 ? " skyddat område" : " skyddade områden"));
        underrubrik = "Du står i " + delar.join(" och ") + ".";
      }
      h += '<div class="svar svar-' + lage.klass + '">' +
           "<strong>" + esc(rubrik) + "</strong><p>" + esc(underrubrik) + "</p></div>";

      h += handlingsHtml(handlingsoversikt(res.poster));

      res.poster.forEach(function (p) {
        h += p.typ === "lfv" ? zonHtml(p.zon) : omradeHtml(p);
      });

      if (res.niva === 4) {
        h += '<div class="post post-ingen"><p>Tjänsten täcker skyddad natur ur ' +
             'Naturvårdsregistret och luftrummet ur LFV:s DAIM. Den täcker inte ' +
             'NOTAM, tillfälliga restriktioner, militära områden eller markägarens ' +
             'medgivande.</p>' +
             '<p><a href="/regler/">De regler som gäller överallt</a> gäller ändå.</p>' +
             "</div>";
      }

      h += regelinventering();

      var nara = narliggande(lon, lat, traffar.map(function (o) { return o.nvrid; }),
                             rader || [], geometrier || {});
      if (nara.length) {
        h += '<h2 class="narrubrik">Närliggande</h2><ul class="lista-omraden">' +
          nara.map(function (n) {
            return '<li><span class="prick prick-' + esc(n.luftfartslage) + '"></span>' +
              '<a href="/omrade/' + n.nvrid + "-" + n.slug + '/">' + esc(n.namn) +
              '</a> <span class="avstand">' + formatAvstand(n.d) + "</span></li>";
          }).join("") + "</ul>";
      }

      if (acc && acc > 50) {
        h += '<p class="notis">GPS-osäkerhet ±' + Math.round(acc) +
             " m. Står du nära en gräns kan svaret gälla fel sida av den.</p>";
      }
      h += luckorHtml(kommun);
      h += '<p class="ansvar">' + (window.DK_ANSVARSTEXT || "") + "</p>";
      visaPanel(h, String(res.niva));
      return res;
    }

    /* ------------------------------------------------------- profilbyte */
    var sistaSvar = null;      // för omritning när profilen ändras

    function visaProfilval() {
      var lista = profiler.lista.map(function (p, i) {
        var uk = underkategori(p);
        return '<li><button class="profilval' + (i === profiler.aktiv ? " vald" : "") +
          '" data-i="' + i + '">' + esc(p.namn || "Drönare") + " — " +
          esc(p.klass || "omärkt") + " · " + esc(p.gram) + " g" +
          (uk && uk.kat ? " · " + esc(uk.kat) : "") +
          '</button> <button class="profilbort" data-bort="' + i + '">×</button></li>';
      }).join("");
      visaPanel(
        '<div class="profilform"><h2>Vilken drönare flyger du med?</h2>' +
        '<p class="notis">Avståndsreglerna beror på klassmärkning och startmassa. ' +
        "Uppgiften står på drönaren eller i dess manual.</p>" +
        (lista ? "<ul class='profillista'>" + lista + "</ul>" : "") +
        '<label>Namn<input id="pnamn" placeholder="t.ex. Mini 4 Pro"></label>' +
        '<label>Klassmärkning<select id="pklass">' +
        ['omärkt', 'C0', 'C1', 'C2', 'C3', 'C4'].map(function (k) {
          return '<option value="' + (k === "omärkt" ? "" : k) + '">' + k + "</option>";
        }).join("") + "</select></label>" +
        '<label>Startmassa i gram<input id="pgram" type="number" inputmode="numeric" ' +
        'placeholder="249"></label>' +
        '<button class="storknapp primar" id="plagg">Lägg till</button>' +
        (profiler.lista.length
          ? '<button class="storknapp" id="pingen">Använd ingen profil</button>' : "") +
        "</div>");
    }

    function ritaOmSvar() {
      if (sistaSvar) {
        visaSvar.apply(null, sistaSvar);
      } else {
        panelEl.classList.remove("oppen");
      }
    }

    // Delegerad hantering — panelens innehåll byts ut hela tiden.
    panelEl.addEventListener("click", function (e) {
      var t2 = e.target.closest ? e.target.closest("button") : null;
      if (!t2) return;
      if (t2.id === "profilknapp") { visaProfilval(); return; }
      if (t2.classList.contains("profilval")) {
        profiler.aktiv = Number(t2.getAttribute("data-i"));
        sparaProfiler(); ritaOmSvar(); return;
      }
      if (t2.hasAttribute("data-bort")) {
        var i = Number(t2.getAttribute("data-bort"));
        profiler.lista.splice(i, 1);
        if (profiler.aktiv === i) profiler.aktiv = -1;
        else if (profiler.aktiv > i) profiler.aktiv--;
        sparaProfiler(); visaProfilval(); return;
      }
      if (t2.id === "pingen") { profiler.aktiv = -1; sparaProfiler(); ritaOmSvar(); return; }
      if (t2.id === "plagg") {
        var gram = Number(document.getElementById("pgram").value);
        if (!gram) { document.getElementById("pgram").focus(); return; }
        profiler.lista.push({
          namn: document.getElementById("pnamn").value || "Drönare",
          klass: document.getElementById("pklass").value,
          gram: gram
        });
        profiler.aktiv = profiler.lista.length - 1;
        sparaProfiler(); ritaOmSvar();
      }
    });

    /* --------------------------------------------------------- vaktläget */
    var vakt = { pa: false, id: null, senasteNyckel: null };
    var vaktknapp = document.getElementById("vaktknapp");

    function nyckelFor(res) {
      return res.poster.filter(function (p) { return p.rang <= 1; })
        .map(function (p) { return p.typ === "lfv" ? p.zon.id : p.omrade.nvrid; })
        .sort().join("|");
    }

    function vaktTick(pos) {
      var lon = pos.coords.longitude, lat = pos.coords.latitude;
      markera(lon, lat, pos.coords.accuracy);
      karta.panTo([lat, lon], { animate: true });

      /* Snabbsvaret går helt i minnet: LFV-zonerna och luftfartscitaten ligger
       * redan där. Det gör att varningen kommer direkt och även utan nät. Det
       * fullständiga svaret hämtas sedan och ersätter panelen. */
      var snabb = bedom(lon, lat, [], luftfartsCitat);
      var nyckel = nyckelFor(snabb);
      if (nyckel && nyckel !== vakt.senasteNyckel) {
        pip(3);
      }
      vakt.senasteNyckel = nyckel;
      svaraForPosition(lon, lat, pos.coords.accuracy);
    }

    function slaPaVakt() {
      if (!navigator.geolocation) return;
      forberedLjud();          // måste ske i användarens tryck (iOS)
      pip(1);                  // kvitto på att ljudet fungerar
      vakt.pa = true;
      vakt.senasteNyckel = null;
      vaktknapp.classList.add("aktiv");
      vaktknapp.setAttribute("aria-pressed", "true");
      vaktknapp.querySelector(".vakttext").textContent = "Vakt på";
      vakt.id = navigator.geolocation.watchPosition(vaktTick, function (err) {
        visaPanel(svarBlock("okant", "Vakten tappade positionen",
          "<p>" + esc(err.message) + "</p>"), "2");
      }, { enableHighAccuracy: true, timeout: 20000, maximumAge: 5000 });
    }
    function slaAvVakt() {
      vakt.pa = false;
      if (vakt.id != null) navigator.geolocation.clearWatch(vakt.id);
      vakt.id = null;
      vaktknapp.classList.remove("aktiv");
      vaktknapp.setAttribute("aria-pressed", "false");
      vaktknapp.querySelector(".vakttext").textContent = "Vakt";
    }
    if (vaktknapp) {
      vaktknapp.addEventListener("click", function () {
        if (vakt.pa) slaAvVakt(); else slaPaVakt();
      });
    }

    /* ------------------------------------------------------------ lager */
    var lfvRuta = document.getElementById("lfvtoggle");
    if (lfvRuta) {
      lfvRuta.addEventListener("change", function () {
        if (lfvRuta.checked) lfvLager.addTo(karta); else karta.removeLayer(lfvLager);
      });
    }
    document.getElementById("lagerknapp").addEventListener("click", function () {
      document.getElementById("lagerpanel").classList.toggle("oppen");
    });

    laddaForVy();
    Promise.all([laddaLfv(), laddaLuftfart(), laddaRegler(), laddaKommunala()])
      .then(function () {
      ritaLfv();
      if (lfvMeta) {
        var el = document.getElementById("lfvmeta");
        if (el) {
          el.textContent = lfvMeta.zoner.length + " zoner, hämtade " +
                           lfvMeta.hamtad + ". " + lfvMeta.attribution;
        }
      }
      return grindKlar;
    }).then(function () { hamtaPosition(true); });
    karta.on("zoomend", function () { setTimeout(laddaForVy, 0); });
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
      laddaLfv().then(function (zoner) {
        var b = lager.getBounds();
        zoner.forEach(function (z) {
          if (z.bbox[2] < b.getWest() || z.bbox[0] > b.getEast() ||
              z.bbox[3] < b.getSouth() || z.bbox[1] > b.getNorth()) return;
          var c = LFVFARG[z.lager] || "#8e0000";
          L.geoJSON(z.geometri, {
            style: { color: c, weight: 1.4, fillColor: c, fillOpacity: 0.12 }
          }).bindPopup(esc(z.namn) + " (" + esc(z.lager) + ")").addTo(karta);
        });
      });
    }

    hamta(DATA + "/geom/" + rid + ".json").then(function (d) {
      var geom = (d.omraden || {})[nvrid];
      if (geom) return rita(geom);
      return hamta(DATA + "/geom/stora/" + nvrid + ".json").then(rita);
    }).catch(function () { rita(null); });
  }

  /* ======================================================= friskrivning ==
   * Grinden ersätter inte reservationerna på sidorna, men den gör att de kan
   * hållas korta: den som kvitterat vet redan vad tjänsten är. Nyckeln bär en
   * version — ändras texten måste den kvitteras om. */
  var GRIND_NYCKEL = "dk-friskrivning-1";
  /* Löser ut när grinden är kvitterad. Kartan får ladda under tiden, men
   * positionen frågas INTE efter förrän dess: en behörighetsdialog som poppar
   * upp bakom en modal man ännu inte läst är både påträngande och obegriplig.
   * Första körningen gjorde precis det. */
  var grindKlar = new Promise(function (losUt) {
    document.addEventListener("DOMContentLoaded", function () {
      var el = document.getElementById("grind");
      if (!el) { losUt(); return; }
      var kvitterat;
      try { kvitterat = localStorage.getItem(GRIND_NYCKEL); } catch (e) { kvitterat = null; }
      if (kvitterat) { el.remove(); losUt(); return; }
      document.body.classList.add("grindad");
      el.hidden = false;
      var knapp = document.getElementById("grindknapp");
      var ruta = document.getElementById("grindkryss");
      knapp.disabled = true;
      ruta.addEventListener("change", function () { knapp.disabled = !ruta.checked; });
      knapp.addEventListener("click", function () {
        try { localStorage.setItem(GRIND_NYCKEL, new Date().toISOString()); } catch (e) { }
        document.body.classList.remove("grindad");
        el.remove();
        losUt();
      });
    });
  });

  /* ============================================================== PWA === */
  function pwa() {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(function () { });
    }
    var installknapp = document.getElementById("installknapp");
    var vantande = null;
    window.addEventListener("beforeinstallprompt", function (e) {
      e.preventDefault();
      vantande = e;
      if (installknapp) installknapp.hidden = false;
    });
    if (installknapp) {
      installknapp.addEventListener("click", function () {
        if (!vantande) return;
        vantande.prompt();
        vantande.userChoice.then(function () {
          vantande = null;
          installknapp.hidden = true;
        });
      });
    }
    window.addEventListener("appinstalled", function () {
      if (installknapp) installknapp.hidden = true;
    });

    function natstatus() {
      var el = document.getElementById("natstatus");
      if (!el) return;
      el.hidden = navigator.onLine;
    }
    window.addEventListener("online", natstatus);
    window.addEventListener("offline", natstatus);
    natstatus();
  }

  document.addEventListener("DOMContentLoaded", function () {
    laddaProfiler();
    pwa();
    if (document.getElementById("karta")) kartappen();
    if (document.getElementById("minikarta")) minikarta();
  });
})();
