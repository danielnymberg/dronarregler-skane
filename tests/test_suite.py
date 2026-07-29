#!/usr/bin/env python3
"""Testsvit A–E. Grönt är krav för att dist/ ska få skrivas.

  A  Citaträkenskap        varje citat i dist/ strängmatchar sitt källdokument
  B  Geometriproveniens    varje geometri spåras till en källhash; giltiga ringar
  C  Golden tests          fallen i tests/golden.json
  D  Länkhälsa             stickprov av dokumentlänkar
  E  Visuell granskning    körs separat, se scripts/07_visuell_granskning.py

Körs som `make test` eller `python3 tests/test_suite.py [--snabb]`.
`--snabb` hoppar över D (nätberoende).
"""
from __future__ import annotations

import argparse
import html as html_mod
import importlib.util
import json
import os
import random
import re
import sys
import urllib.request

HAR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HAR)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from lib.common import CONFIG, DATA, DIST, read_json  # noqa: E402
from lib.geom import har_sjalvskarning, punkt_i_geometri, ring_area_m2  # noqa: E402
from lib.textnorm import normalisera  # noqa: E402

# Steg 4:s verifieringsfunktion importeras och körs om — testet får inte vara
# en egen, avvikande implementation av samma kontroll.
_spec = importlib.util.spec_from_file_location(
    "steg04", os.path.join(ROOT, "scripts", "04_verify.py"))
_steg04 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_steg04)
verifiera_citat = _steg04.verifiera_citat

CACHE_TEXT = os.path.join(ROOT, "cache", "text")


class Resultat:
    def __init__(self):
        self.fel = []
        self.notiser = []

    def kolla(self, villkor, meddelande):
        if not villkor:
            self.fel.append(meddelande)
        return bool(villkor)

    def notera(self, meddelande):
        self.notiser.append(meddelande)


def dokumenttext(dokument_id):
    path = os.path.join(CACHE_TEXT, dokument_id + ".txt")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------- A
def test_a_citatrakenskap(r):
    """Omkörning av steg 4 mot dist/-innehållet. 100 % krävs.

    Testet importerar och kör steg 4:s egen verifieringsfunktion i stället för
    att implementera om den. Skälet är konkret: en tidigare version av testet
    matchade mot hela dokumentet medan steg 4 matchade mot den angivna sidan.
    De två gav olika svar för ett citat som slutade i ett avstavat ord vid en
    sidbrytning — och när grinden och dess kontroll inte är samma kontroll vet
    man inte längre vilken som gäller.

    Utöver omkörningen kontrolleras att citaten dessutom går att hitta i den
    byggda HTML-sidan, och att de matchar hela dokumentets text — den senare
    är en strängare kontroll som fångar just avstavningsfallet.
    """
    provade = godkanda = 0
    saknade_text = 0
    for fil in sorted(os.listdir(os.path.join(DATA, "omraden"))):
        o = read_json(os.path.join(DATA, "omraden", fil))
        for c in o.get("citat") or []:
            provade += 1
            txt = dokumenttext(c["dokument_id"])
            if txt is None:
                saknade_text += 1
                r.fel.append(f"A: källtext saknas för {c['dokument_id']} "
                             f"(NVRID {o['nvrid']})")
                continue
            # 1. Steg 4:s egen grind, omkörd.
            ok, orsak, _ = verifiera_citat(c)
            if not ok:
                r.fel.append(f"A: steg 4:s verifiering underkänner citatet — "
                             f"NVRID {o['nvrid']}, dok {c['dokument_id']}: {orsak}")
            # 2. Strängare kontroll: citatet ska finnas i hela dokumentets text,
            #    inte bara på sin sida.
            txt_norm = normalisera(txt)
            if normalisera(c["citat"]) in txt_norm:
                if ok:
                    godkanda += 1
            else:
                r.fel.append(f"A: citat matchar inte källan — NVRID {o['nvrid']}, "
                             f"dok {c['dokument_id']}: {c['citat'][:80]!r}")
            # 3. Föreskriftsinledningen är också text som visas för användaren
            #    och måste därför vara ordagrann.
            if c.get("inledning") and normalisera(c["inledning"]) not in txt_norm:
                r.fel.append(f"A: föreskriftsinledning matchar inte källan — "
                             f"NVRID {o['nvrid']}: {c['inledning'][:80]!r}")

    # Samma citat ska också gå att hitta i den byggda HTML-sidan.
    kontrollerade_sidor = 0
    for fil in sorted(os.listdir(os.path.join(DATA, "omraden")))[:60]:
        o = read_json(os.path.join(DATA, "omraden", fil))
        if not o.get("citat"):
            continue
        sidpath = os.path.join(DIST, "omrade", f"{o['nvrid']}-{o['slug']}", "index.html")
        if not os.path.exists(sidpath):
            r.fel.append(f"A: områdessida saknas för NVRID {o['nvrid']}")
            continue
        with open(sidpath, encoding="utf-8") as fh:
            sidhtml = fh.read()
        # <mark> märker ut de ord som gjorde att citatet valdes ut. Taggen är
        # ren inline-annotering och tillför ingen text — den tas därför bort
        # helt innan jämförelsen, inte ersätts med mellanslag. Att den finns
        # får aldrig räknas som en avvikelse i ordalydelsen, och att den inte
        # ÄNDRAR ordalydelsen kontrolleras separat i C11.
        html_norm = normalisera(sidhtml.replace("<mark>", "").replace("</mark>", ""))
        for c in o["citat"]:
            import html as htmlmod
            if normalisera(htmlmod.escape(c["citat"])) not in html_norm:
                r.fel.append(f"A: citat saknas i byggd HTML för NVRID {o['nvrid']}")
        kontrollerade_sidor += 1

    r.kolla(provade == godkanda,
            f"A: {godkanda}/{provade} citat matchade källdokumenten (krav 100 %)")
    r.notera(f"A: {provade} citat prövade mot källdokument, {godkanda} godkända, "
             f"{saknade_text} utan källtext; {kontrollerade_sidor} HTML-sidor korsprövade")


# ---------------------------------------------------------------- B
def test_b_geometriproveniens(r):
    manifest = read_json(os.path.join(DATA, "manifest.json"))
    kallhashar = set()
    for lager in manifest["kallor"]["nvr_wfs"]["lager"].values():
        kallhashar.add(lager["svarshash_sha256"])

    areas = read_json(os.path.join(DATA, "areas.geojson"))
    nvrid_i_areas = {f["properties"]["nvrid"] for f in areas["features"]}
    tol = manifest["databygge"]["forenkling"]["tolerans_m"]

    max_forlust = manifest["databygge"]["forenkling"]["max_ytforlust_procent_per_objekt"]
    trasiga_i_kallan = set()

    def _original_ringar_ok(geom):
        """Sant om originalgeometrin saknar självskärande ringar."""
        if not geom:
            return True
        polys = (geom["coordinates"] if geom["type"] == "MultiPolygon"
                 else [geom["coordinates"]])
        for rings in polys:
            for ring in rings:
                if har_sjalvskarning(ring):
                    return False
        return True

    foraldralosa = 0
    ogiltiga = 0
    vaxta = 0
    kollade_ringar = 0
    storsta_forlust = 0.0
    for fil in sorted(os.listdir(os.path.join(DATA, "omraden"))):
        o = read_json(os.path.join(DATA, "omraden", fil))
        g = o.get("geometri")
        if g is None:
            continue
        kh = (o.get("geometri_kalla") or {}).get("svarshash_sha256")
        if kh not in kallhashar:
            foraldralosa += 1
            r.fel.append(f"B: geometri utan spårbar källhash — NVRID {o['nvrid']}")
        if o["nvrid"] not in nvrid_i_areas:
            r.fel.append(f"B: NVRID {o['nvrid']} har geometri men saknas i areas.geojson")

    # Ringgiltighet och krympgaranti på visningsgeometrin.
    original = {}
    for fil in sorted(os.listdir(os.path.join(DATA, "omraden"))):
        o = read_json(os.path.join(DATA, "omraden", fil))
        if o.get("geometri"):
            original[o["nvrid"]] = o["geometri"]

    for f in areas["features"]:
        nvrid = f["properties"]["nvrid"]
        polys = (f["geometry"]["coordinates"]
                 if f["geometry"]["type"] == "MultiPolygon" else [f["geometry"]["coordinates"]])
        ny_yta = 0.0
        for rings in polys:
            for j, ring in enumerate(rings):
                kollade_ringar += 1
                if len(ring) < 4 or ring[0] != ring[-1]:
                    ogiltiga += 1
                    r.fel.append(f"B: ogiltig ring (osluten/för kort) i NVRID {nvrid}")
                    continue
                if har_sjalvskarning(ring):
                    # Bara ett fel om förenklingen orsakade det. Källdatan
                    # innehåller enstaka ringar som korsar sig själva redan i
                    # registret; dem kan tjänsten inte rätta, bara redovisa.
                    if _original_ringar_ok(original.get(nvrid)):
                        ogiltiga += 1
                        r.fel.append("B: självskärande ring efter förenkling i "
                                     f"NVRID {nvrid}")
                    else:
                        trasiga_i_kallan.add(nvrid)
                ny_yta += ring_area_m2(ring) * (1 if j == 0 else -1)
        org = original.get(nvrid)
        if org:
            opolys = (org["coordinates"] if org["type"] == "MultiPolygon"
                      else [org["coordinates"]])
            org_yta = 0.0
            for rings in opolys:
                for j, ring in enumerate(rings):
                    org_yta += ring_area_m2(ring) * (1 if j == 0 else -1)
            if org_yta > 0 and ny_yta > org_yta * 1.0001:
                vaxta += 1
                r.fel.append(f"B: förenklad yta större än originalet — NVRID {nvrid} "
                             f"({ny_yta:.0f} > {org_yta:.0f} m²)")
            if org_yta > 0:
                forlust = (1 - ny_yta / org_yta) * 100
                if forlust > max_forlust + 0.01:
                    r.fel.append(f"B: ytförlusten {forlust:.2f} % överskrider den "
                                 f"dokumenterade gränsen {max_forlust} % — "
                                 f"NVRID {nvrid}")
                storsta_forlust = max(storsta_forlust, forlust)

    # Oberoende arealavstämning mot registrets eget AREA_HA. Fångar tysta
    # geometrifel som inget annat test ser — t.ex. när flerdelade områden
    # plattas ihop och delar av dem försvinner ur kartan.
    grova = []
    provade_areal = 0
    sma_undantagna = 0
    for fil in sorted(os.listdir(os.path.join(DATA, "omraden"))):
        o = read_json(os.path.join(DATA, "omraden", fil))
        avv = o.get("arealavvikelse_procent")
        if avv is None:
            continue
        # Objekt under ett halvt hektar undantas. Naturminnen — ett enskilt
        # träd, ett flyttblock — lagras i registret som en symbolisk cirkel med
        # en halv meters radie, medan AREA_HA är en nominell uppgift. Att jämföra
        # 0,79 m² geometri med 100 m² registrerad areal säger ingenting om
        # täckning, bara att geometrin är en punktmarkering.
        if (o.get("area_ha") or 0) < 0.5:
            sma_undantagna += 1
            continue
        provade_areal += 1
        # En geometri som täcker mindre än halva den registrerade arealen är
        # inte en avrundningsfråga utan ett strukturfel.
        if avv < -50:
            grova.append(f"{o['nvrid']} {o['namn']}: {o.get('beraknad_area_ha')} ha "
                         f"mot registrets {o.get('area_ha')} ha ({avv} %)")
    r.kolla(not grova,
            f"B: {len(grova)} geometrier täcker under halva registrerad areal "
            f"— sannolikt tappade delområden: {grova[:5]}")
    if not grova:
        r.notera(f"B: arealavstämning mot AREA_HA gjord för {provade_areal} objekt "
                 f"({sma_undantagna} punktmarkeringar under 0,5 ha undantagna), "
                 "ingen geometri under halva registrerad areal ✓")

    r.kolla(foraldralosa == 0, f"B: {foraldralosa} föräldralösa geometrier")
    r.kolla(ogiltiga == 0, f"B: {ogiltiga} ogiltiga ringar efter förenkling")
    r.kolla(vaxta == 0, f"B: {vaxta} objekt där förenklingen ökade ytan "
                        "(krympgarantin bruten)")
    r.notera(f"B: {len(areas['features'])} visningsgeometrier, {kollade_ringar} ringar "
             f"kontrollerade, utgångstolerans {tol} m, största uppmätta ytförlust "
             f"{storsta_forlust:.2f} % (gräns {max_forlust} %), ingen yta växte, "
             f"alla spårbara till källhash"
             + (f"; {len(trasiga_i_kallan)} objekt har självskärande ringar redan "
                "i registret" if trasiga_i_kallan else ""))


# ---------------------------------------------------------------- C
FORBJUDNA = [
    r"till[åa]tet\s+att\s+flyga", r"fritt\s+fram", r"du\s+f[åa]r\s+flyga",
    r"OK\s+att\s+flyga", r"ok[ae]j\s+att\s+flyga", r"g[åa]r\s+bra\s+att\s+flyga",
    r"ing[ea]t?\s+hinder\s+f[öo]r\s+att\s+flyga", r"flygning\s+[äa]r\s+till[åa]ten",
    r"here\s+you\s+can\s+fly", r"drone\s+friendly",
]
CITAT_BLOCK = re.compile(r"<blockquote>.*?</blockquote>", re.S)
TAGGAR = re.compile(r"<[^>]+>")


def test_c_golden(r):
    golden = read_json(os.path.join(HAR, "golden.json"))
    if golden is None:
        r.fel.append("C: tests/golden.json saknas")
        return

    # C1 Anti-ESMH: punkt i Höganäs tätort ska ge svarsläge 2.
    for fall in golden["punkter_utan_traff"]:
        lon, lat = fall["lon"], fall["lat"]
        traffar = []
        for fil in sorted(os.listdir(os.path.join(DATA, "omraden"))):
            o = read_json(os.path.join(DATA, "omraden", fil))
            g = o.get("geometri")
            bb = o.get("bbox")
            if not g or not bb:
                continue
            if not (bb[0] <= lon <= bb[2] and bb[1] <= lat <= bb[3]):
                continue
            if punkt_i_geometri(lon, lat, g):
                traffar.append(f"{o['namn']} ({o['nvrid']})")
        r.kolla(not traffar,
                f"C1 {fall['id']}: förväntade noll zonträffar, fick {traffar}")
        if not traffar:
            r.notera(f"C1 {fall['id']}: noll zonträffar i egna lager → svarsläge 2 ✓")

    # C2 Kullaberg: sida finns, ≥1 verifierat citat, PDF-länk svarar 200.
    for fall in golden["omraden_med_citat"]:
        nvrid = fall["nvrid"]
        o = read_json(os.path.join(DATA, "omraden", f"{nvrid}.json"))
        if not r.kolla(o is not None, f"C2 {nvrid}: områdesdata saknas"):
            continue
        sidpath = os.path.join(DIST, "omrade", f"{nvrid}-{o['slug']}", "index.html")
        r.kolla(os.path.exists(sidpath), f"C2 {nvrid}: områdessida saknas ({sidpath})")
        r.kolla(len(o.get("citat") or []) >= fall.get("min_citat", 1),
                f"C2 {nvrid} ({o['namn']}): förväntade minst "
                f"{fall.get('min_citat', 1)} verifierade citat, fick "
                f"{len(o.get('citat') or [])}")
        pdf = next((d["url"] for d in o.get("dokument") or [] if d.get("url")), None)
        r.kolla(pdf is not None, f"C2 {nvrid}: ingen dokumentlänk")
        if pdf and not r_snabb:
            r.kolla(http_ok(pdf), f"C2 {nvrid}: PDF-länken svarade inte 200: {pdf}")
        if o and o.get("citat"):
            r.notera(f"C2 {nvrid} ({o['namn']}): {len(o['citat'])} verifierade citat ✓")

    # C2b Citat som måste överleva hela vägen ut i byggd HTML — särskilt de
    # som bär höjdband och undantag, eftersom det är dem en sammanfattning
    # skulle ha tappat.
    for fall in golden.get("citat_som_maste_finnas", []):
        nvrid = fall["nvrid"]
        o = read_json(os.path.join(DATA, "omraden", f"{nvrid}.json"))
        if not r.kolla(o is not None, f"C2b {fall['id']}: områdesdata saknas"):
            continue
        norm_del = normalisera(fall["delstrang"])
        i_data = any(norm_del in normalisera(c["citat"]) for c in o.get("citat") or [])
        r.kolla(i_data, f"C2b {fall['id']} ({fall['namn']}): delsträngen "
                        f"{fall['delstrang']!r} saknas i något verifierat citat")
        sidpath = os.path.join(DIST, "omrade", f"{nvrid}-{o['slug']}", "index.html")
        if os.path.exists(sidpath):
            with open(sidpath, encoding="utf-8") as fh:
                # <mark> tas bort helt, inte ersätts med mellanslag — se
                # motiveringen i test A. Märkningen tillför ingen text.
                sida = normalisera(fh.read().replace("<mark>", "")
                                   .replace("</mark>", ""))
            r.kolla(norm_del in sida,
                    f"C2b {fall['id']}: delsträngen syns inte på den byggda sidan")
        if i_data:
            r.notera(f"C2b {fall['id']} ({fall['namn']}): {fall['delstrang']!r} "
                     "bevarad ordagrant hela vägen ut ✓")

    # C2c Regressioner i hur föreskriftsinledningen hittas.
    reg = golden.get("inledning_regressioner") or {}
    for fall in reg.get("maste_ha_citat", []):
        o = read_json(os.path.join(DATA, "omraden", f"{fall['nvrid']}.json"))
        if not r.kolla(o is not None, f"C2c {fall['nvrid']}: områdesdata saknas"):
            continue
        med_inledning = [c for c in o.get("citat") or [] if c.get("inledning")]
        r.kolla(med_inledning,
                f"C2c {fall['namn']} ({fall['nvrid']}): inget citat med verifierad "
                "föreskriftsinledning — kolonfri listrubrik hittas inte längre")
        if med_inledning:
            r.notera(f"C2c {fall['namn']}: kolonfri listrubrik hittad, "
                     f"{len(med_inledning)} citat med inledning ✓")

    # Varje verifierad inledning ska sluta som en listrubrik, inte som brödtext.
    AVSLUT = re.compile(r"(?::|\batt)\s*:?\s*$", re.I)
    dåliga = []
    antal_inledningar = 0
    for fil in sorted(os.listdir(os.path.join(DATA, "omraden"))):
        o = read_json(os.path.join(DATA, "omraden", fil))
        for c in o.get("citat") or []:
            if not c.get("inledning"):
                continue
            antal_inledningar += 1
            if not AVSLUT.search(c["inledning"].strip()):
                dåliga.append(f"{o['nvrid']}: {c['inledning'][:70]!r}")
    r.kolla(not dåliga,
            f"C2c: {len(dåliga)} föreskriftsinledningar slutar inte på kolon eller "
            f"'att' — brödtext har accepterats som listrubrik: {dåliga[:5]}")
    if not dåliga:
        r.notera(f"C2c: alla {antal_inledningar} verifierade föreskriftsinledningar "
                 "slutar som listrubrik ✓")

    # C3 Säsong: minst ett område visar datumperioder ur källdata.
    med_sasong = []
    for fil in sorted(os.listdir(os.path.join(DATA, "omraden"))):
        o = read_json(os.path.join(DATA, "omraden", fil))
        if o.get("sasongsdata"):
            med_sasong.append(o)
    r.kolla(len(med_sasong) >= golden.get("min_omraden_med_sasong", 1),
            f"C3: förväntade minst {golden.get('min_omraden_med_sasong', 1)} områden "
            f"med säsongsdata, fick {len(med_sasong)}")
    if med_sasong:
        o = med_sasong[0]
        sidpath = os.path.join(DIST, "omrade", f"{o['nvrid']}-{o['slug']}", "index.html")
        with open(sidpath, encoding="utf-8") as fh:
            sida = fh.read()
        datum = [f.get("franDatum") for f in o["sasongsdata"] if f.get("franDatum")]
        for dd in datum[:3]:
            r.kolla(dd in sida, f"C3: datum {dd} saknas på sidan för {o['namn']}")
        # Datumen får inte vara hårdkodade i sajtbyggaren.
        with open(os.path.join(ROOT, "scripts", "06_build_site.py"), encoding="utf-8") as fh:
            byggare = fh.read()
        for dd in datum[:3]:
            r.kolla(f'"{dd}"' not in byggare,
                    f"C3: datumet {dd} är hårdkodat i sajtbyggaren")
        r.notera(f"C3: {len(med_sasong)} områden med säsongsdata ur källdata "
                 f"(exempel: {o['namn']} {datum[:2]}) ✓")

    # C4 Täckningsdeklaration.
    kallor = os.path.join(DIST, "kallor", "index.html")
    r.kolla(os.path.exists(kallor), "C4: /kallor/ saknas")
    if os.path.exists(kallor):
        with open(kallor, encoding="utf-8") as fh:
            sida = fh.read()
        manifest = read_json(os.path.join(DATA, "manifest.json"))
        datum = manifest["hamtningsdatum"]
        r.kolla(datum in sida, "C4: hämtningsdatum saknas på /kallor/")
        lager_i_bruk = set()
        for fil in os.listdir(os.path.join(DATA, "omraden")):
            o = read_json(os.path.join(DATA, "omraden", fil))
            if o:
                lager_i_bruk.add(o["lager"])
        # Varje lager i scope ska stå på /kallor/ med hämtningsdatum — även de
        # som saknar objekt i länet. Ett tomt lager får inte tyst försvinna.
        LAGER_I_SCOPE = {
            "nationalpark": "Nationalparker",
            "naturreservat": "Naturreservat (statligt beslutade)",
            "naturreservat-kommunalt": "Naturreservat (kommunalt beslutade)",
            "naturvardsomrade": "Naturvårdsområden",
            "djur-och-vaxtskydd": "Djur- och växtskyddsområden",
            "kulturreservat": "Kulturreservat",
            "interimistiskt-forbud": "Interimistiska förbud",
            "naturminne": "Naturminnen",
            "vattenskyddsomrade": "Vattenskyddsområden",
            "landskapsbildsskydd": "Landskapsbildsskyddsområden",
            "biotopskydd": "Övriga biotopskyddsområden",
        }
        for lager, namn in LAGER_I_SCOPE.items():
            r.kolla(namn in sida,
                    f"C4: lagret '{namn}' saknas i täckningsdeklarationen")
        tomma = [l for l in LAGER_I_SCOPE if l not in lager_i_bruk]
        if tomma:
            r.kolla("Denna källa täcks inte här" in sida,
                    "C4: tomma lager saknar svarsläge 3 på /kallor/")
        r.notera(f"C4: /kallor/ listar lager med hämtningsdatum {datum}; "
                 f"tomma lager i scope: {tomma or 'inga'} ✓")

    # C5 Förbjudna ord i dist/ utanför citatblock.
    traffar = []
    for dirpath, _, filnamn in os.walk(DIST):
        if os.path.sep + "data" + os.path.sep in dirpath + os.path.sep:
            continue
        for fn in filnamn:
            if not fn.endswith((".html", ".js", ".css", ".xml", ".txt")):
                continue
            p = os.path.join(dirpath, fn)
            with open(p, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            utan_citat = CITAT_BLOCK.sub(" ", text)
            utan_taggar = TAGGAR.sub(" ", utan_citat)
            for m in FORBJUDNA:
                for hit in re.finditer(m, utan_taggar, re.I):
                    traffar.append(f"{os.path.relpath(p, DIST)}: {hit.group(0)!r}")
    r.kolla(not traffar, f"C5: förbjudna formuleringar i dist/: {traffar[:10]}")
    if not traffar:
        r.notera("C5: inga tillåtelseformuleringar i dist/ utanför citatblock ✓")

    # C6 LFV-licensen.
    #
    # Testet vaktade förut regeln "rör aldrig LFV:s vektordata". Den regeln är
    # medvetet upphävd — se toppen av scripts/08_lfv.py — eftersom ett
    # rasterlager tjänsten själv inte kan läsa gjorde den ur stånd att svara på
    # zonfrågan, och därmed värdelös offline.
    #
    # Det som ÄR kvar av R5 är licensdisciplinen, och den kontrolleras här. Att
    # ta bort testet i stället för att skriva om det hade tappat bort just den
    # delen som fortfarande spelar roll.
    lfvfil = os.path.join(DIST, "data", "lfv.json")
    if not os.path.exists(lfvfil):
        r.notera("C6: inget LFV-lager byggt — hoppar över licenskontrollen")
    else:
        with open(lfvfil, encoding="utf-8") as fh:
            lfv = json.load(fh)
        r.kolla(lfv.get("licens") == "CC BY-NC-ND 4.0",
                f"C6: LFV-lagret saknar korrekt licensangivelse: {lfv.get('licens')!r}")
        r.kolla(bool(lfv.get("attribution")),
                "C6: LFV-lagret saknar attribution")

        # LFV:s data får inte hamna under tjänstens CC0-deklaration.
        licensfil = os.path.join(DIST, "data", "LICENSE")
        if os.path.exists(licensfil):
            with open(licensfil, encoding="utf-8") as fh:
                licenstext = fh.read()
            r.kolla("LFV" in licenstext,
                    "C6: data/LICENSE nämner inte LFV — CC0-texten måste "
                    "uttryckligen undanta lagret, annars vidarelicensieras "
                    "någon annans ND-data")

        # Deras egna tiles får aldrig cachas om och serveras vidare.
        swfil = os.path.join(DIST, "sw.js")
        if os.path.exists(swfil):
            with open(swfil, encoding="utf-8") as fh:
                sw = fh.read()
            r.kolla("lfv.se" in sw and "return" in sw,
                    "C6: service workern måste uttryckligen lämna lfv.se orörd")
        r.notera(f"C6: LFV-lagret licensmärkt {lfv.get('licens')}, "
                 f"undantaget CC0, tiles ej omcachade ✓")

    # C9 Rubriken får inte påstå mer än citatet.
    #
    # Ett citat utan verifierad föreskriftsinledning kan stå i beslutets SKÄL
    # och inte i dess föreskrifter. Västra Kullaberg visade det: citatet lyder
    # "I området finns miljöer som kan utgöra häckningsplatser för fåglar …",
    # medan rubriken sa "Föreskriften förbjuder störning". 483 av 3 044 områden
    # gjorde samma övertramp. Där inledningen saknas ska rubriken säga
    # "Beslutet nämner", inte "Föreskriften".
    LU = {"uttryckligt-luftfartygsförbud", "start-landningsförbud"}
    ST = {"störningsförbud-djurliv"}
    overtramp, provade = [], 0
    for fil in sorted(os.listdir(os.path.join(DATA, "omraden")))[:4000]:
        o = read_json(os.path.join(DATA, "omraden", fil))
        lage = (o or {}).get("luftfartslage")
        if lage not in ("luftfart", "storning"):
            continue
        relevanta = LU if lage == "luftfart" else ST
        belagd = any(c.get("inledning") for c in (o.get("citat") or [])
                     if c.get("klassificering") in relevanta)
        if belagd:
            continue
        sida = os.path.join(DIST, "omrade", f"{o['nvrid']}-{o['slug']}", "index.html")
        if not os.path.exists(sida):
            continue
        provade += 1
        with open(sida, encoding="utf-8") as fh:
            huvud = fh.read().split("Ur beslutet")[0]
        if "Föreskriften" in huvud or "Föreskrifterna" in huvud:
            overtramp.append(o["nvrid"])
    r.kolla(not overtramp,
            f"C9: rubriken kallar text 'föreskrift' utan belagd "
            f"föreskriftsinledning: {overtramp[:8]}")
    if not overtramp:
        r.notera(f"C9: {provade} områden utan belagd föreskriftsinledning — "
                 f"ingen rubrik påstår mer än citatet ✓")

    # C10 Inget beslut får kastas för att det också innehåller skötselplanen.
    #
    # Filtret prövade SKIP_DOC före RULE_DOC. "beslut med skötselplan
    # lerberget.pdf" matchar bägge, kastades som "ej föreskriftsbärande", och
    # 961 områden fick beskedet "inget digitalt beslut att läsa" när beslutet
    # fanns och låg länkat. Länsstyrelser och kommuner buntar rutinmässigt
    # beslut och skötselplan i samma fil.
    RULE_DOC = re.compile(
        r"beslut|f[oö]reskrift|f[oö]rordnand|kung[oö]rels|stadga|bildand|"
        r"utvidgn|[aä]ndr|reservatsbeslut|tilltr[aä]desf[oö]rbud|interimist", re.I)
    BILD = re.compile(r"\.(jpe?g|png|tiff?|gif)$", re.I)
    felkastade, utan_dok = [], 0
    for fil in sorted(os.listdir(os.path.join(DATA, "omraden"))):
        o = read_json(os.path.join(DATA, "omraden", fil))
        if not o:
            continue
        for h in (o.get("dokument_hoppade") or []):
            namn = h.get("namn") or ""
            if RULE_DOC.search(namn) and not BILD.search(namn):
                felkastade.append(f"{o['nvrid']} {namn[:50]}")
        if o.get("luftfartslage") == "utan-dokument":
            utan_dok += 1
    r.kolla(not felkastade,
            f"C10: {len(felkastade)} beslutshandlingar kastade trots att namnet "
            f"pekar ut dem som beslut: {felkastade[:5]}")
    if not felkastade:
        r.notera(f"C10: ingen beslutshandling kastad av dokumentfiltret; "
                 f"{utan_dok} områden saknar verkligen digitalt beslut ✓")

    # C11 Träffordsmärkningen får inte ändra citatet.
    #
    # Märkningen visar VARFÖR ett citat valdes ut. Den lägger till <mark> och
    # ingenting annat — tas taggarna bort ska texten vara identisk med den i
    # datan, annars har renderingen börjat röra vid ordalydelsen.
    prov, avvikande = 0, []
    for fil in sorted(os.listdir(os.path.join(DATA, "omraden")))[:600]:
        o = read_json(os.path.join(DATA, "omraden", fil))
        citat = (o or {}).get("citat") or []
        if not citat:
            continue
        sida = os.path.join(DIST, "omrade", f"{o['nvrid']}-{o['slug']}", "index.html")
        if not os.path.exists(sida):
            continue
        with open(sida, encoding="utf-8") as fh:
            sidhtml = fh.read()
        # Ta bort märkningen och normalisera; citatet ska då finnas ordagrant.
        utan_mark = sidhtml.replace("<mark>", "").replace("</mark>", "")
        text = re.sub(r"\s+", " ", html_mod.unescape(re.sub(r"<[^>]+>", " ", utan_mark)))
        for c in citat[:3]:
            prov += 1
            if re.sub(r"\s+", " ", c["citat"]).strip() not in text:
                avvikande.append(f"{o['nvrid']} s.{c.get('sidnummer')}")
                break
    r.kolla(not avvikande,
            f"C11: citat som inte återfinns ordagrant efter att märkningen "
            f"tagits bort: {avvikande[:5]}")
    if not avvikande:
        r.notera(f"C11: {prov} citat oförändrade av träffordsmärkningen ✓")

    # C12 Fot till meter, och zonunderkanten.
    #
    # Daniel fick en skärmdump där hela Helsingborg låg rött som
    # RESTRIKTIONSOMRÅDE. ES R129 börjar på 400 ft = 122 m, medan öppen kategori
    # tillåter högst 120 m. Omräkningen måste stämma exakt och underkanten måste
    # följa med, annars är hela anti-larmningen verkningslös.
    lfvfil2 = os.path.join(DIST, "data", "lfv.json")
    if os.path.exists(lfvfil2):
        with open(lfvfil2, encoding="utf-8") as fh:
            lfv2 = json.load(fh)
        fel_omrakning, saknar_m = [], []
        for z in lfv2["zoner"]:
            for kant, kant_m in (("underkant", "underkant_m"),
                                 ("overkant", "overkant_m")):
                v = str(z.get(kant) or "")
                m = z.get(kant_m)
                if re.fullmatch(r"\d+", v):
                    vantat = -(-int(v) * 3048 // 10000)   # ceil(v * 0.3048)
                    if m != vantat:
                        fel_omrakning.append(f"{z['namn']} {kant}={v} → {m} (väntat {vantat})")
                    continue
                if v.upper() == "GND" and m != 0:
                    fel_omrakning.append(f"{z['namn']} GND → {m}, väntat 0")
                elif v and m is None and not re.match(r"^FL", v, re.I):
                    saknar_m.append(f"{z['namn']} {kant}={v}")
        r.kolla(not fel_omrakning,
                f"C12: felaktig fot→meter-omräkning: {fel_omrakning[:5]}")

        r129 = [z for z in lfv2["zoner"] if z["namn"] == "ES R129"]
        if r129:
            z = r129[0]
            r.kolla(z["nar_marken"] is False and z["underkant_m"] == 122,
                    f"C12: ES R129 måste ha underkant 122 m och nar_marken=False, "
                    f"har {z.get('underkant_m')} / {z.get('nar_marken')}")
        bromma = [z for z in lfv2["zoner"] if z["namn"] == "BROMMA CTR"]
        if bromma:
            r.kolla(bromma[0]["nar_marken"] is True,
                    "C12: BROMMA CTR måste fortsatt räknas som markberörande")
        antal_hoga = sum(1 for z in lfv2["zoner"] if not z["nar_marken"])
        r.notera(f"C12: {len(lfv2['zoner'])} zoner omräknade fot→meter, "
                 f"{antal_hoga} når inte marken och dämpas i svaret ✓")

    # C13 De allmänna reglerna finns offline och är kompletta.
    #
    # Ingen punkt i Sverige är regelfri. Svaret listar därför alltid de
    # generella reglerna, även vid tom träff — och då måste de finnas i
    # service workerns offlinecache, inte bara på nätet.
    reglerfil2 = os.path.join(DIST, "data", "regler.json")
    swfil2 = os.path.join(DIST, "sw.js")
    if os.path.exists(reglerfil2) and os.path.exists(swfil2):
        with open(reglerfil2, encoding="utf-8") as fh:
            reg2 = json.load(fh)
        with open(swfil2, encoding="utf-8") as fh:
            sw2 = fh.read()
        r.kolla("/data/regler.json" in sw2,
                "C13: regler.json saknas i service workerns offlinedata")
        utan_kategori = [a["id"] for a in reg2["avsnitt"] if not a.get("kategori")]
        utan_fras = [a["id"] for a in reg2["avsnitt"] if not a.get("nyckelfras")]
        r.kolla(not utan_kategori, f"C13: avsnitt utan kategori: {utan_kategori}")
        # Nyckelfrasen är det inventeringsraden VISAR. Saknas den faller raden
        # tillbaka på frågan, vilket är en rubrik och inte författningstext.
        r.kolla(not utan_fras, f"C13: avsnitt utan verifierad nyckelfras: {utan_fras}")
        for a in reg2["avsnitt"]:
            if a.get("nyckelfras") and a["nyckelfras"] not in a["text"]:
                r.fel.append(f"C13: nyckelfrasen för {a['id']} står inte i citatet")
        r.notera(f"C13: {len(reg2['avsnitt'])} regelavsnitt med kategori och "
                 f"ordagrann nyckelfras, cachade offline ✓")

    # C7 Regelsidans citat.
    reglerfil = os.path.join(DIST, "data", "regler.json")
    if not os.path.exists(reglerfil):
        r.kolla(False, "C7: data/regler.json saknas — kör scripts/09_regler.py")
    else:
        with open(reglerfil, encoding="utf-8") as fh:
            regler = json.load(fh)
        regelsida = os.path.join(DIST, "regler", "index.html")
        r.kolla(os.path.exists(regelsida), "C7: /regler/ byggdes inte")
        if os.path.exists(regelsida):
            with open(regelsida, encoding="utf-8") as fh:
                sidtext = html_mod.unescape(re.sub(r"<[^>]+>", " ", fh.read()))
            sidtext = re.sub(r"\s+", " ", sidtext)
            saknade = [a["id"] for a in regler["avsnitt"]
                       if re.sub(r"\s+", " ", a["text"]) not in sidtext]
            r.kolla(not saknade,
                    f"C7: författningstext förvanskad på väg ut till sidan: {saknade}")
            r.notera(f"C7: {len(regler['avsnitt'])} författningsavsnitt ur "
                     f"{len(regler['kallor'])} källor, ordagranna hela vägen ut ✓")

    # C8 PWA-skalet hänger ihop.
    manifestfil = os.path.join(DIST, "manifest.webmanifest")
    swfil = os.path.join(DIST, "sw.js")
    if not (os.path.exists(manifestfil) and os.path.exists(swfil)):
        r.kolla(False, "C8: manifest eller service worker saknas i dist/")
    else:
        with open(manifestfil, encoding="utf-8") as fh:
            mf = json.load(fh)
        saknade_ikoner = [i["src"] for i in mf.get("icons", [])
                          if not os.path.exists(os.path.join(DIST, i["src"].lstrip("/")))]
        r.kolla(not saknade_ikoner, f"C8: manifestet pekar på ikoner som saknas: {saknade_ikoner}")
        with open(swfil, encoding="utf-8") as fh:
            sw = fh.read()
        r.kolla("__VERSION__" not in sw,
                "C8: service workern har kvar versionsplatshållaren — cachen "
                "skulle aldrig bytas ut vid utrullning")
        # Det service workern lovar cacha måste finnas.
        lovade = re.findall(r'"(/[^"]*?)"', sw.split("var OFFLINEDATA")[0])
        brutna = [u for u in lovade
                  if u.endswith((".json", ".png", ".css", ".js", ".webmanifest"))
                  and not os.path.exists(os.path.join(DIST, u.split("?")[0].lstrip("/")))]
        r.kolla(not brutna, f"C8: service workern förcachar filer som saknas: {brutna}")
        r.notera(f"C8: manifest, {len(mf.get('icons', []))} ikoner och versionsstämplad "
                 f"service worker på plats ✓")


# ---------------------------------------------------------------- D
def http_ok(url, timeout=30):
    for metod in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, method=metod,
                                         headers={"User-Agent": CONFIG["user_agent"]})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if 200 <= resp.status < 400:
                    return True
        except Exception:  # noqa: BLE001
            continue
    return False


def test_d_lankhalsa(r, antal=50):
    lankar = []
    for fil in sorted(os.listdir(os.path.join(DATA, "omraden"))):
        o = read_json(os.path.join(DATA, "omraden", fil))
        for d in o.get("dokument") or []:
            if d.get("url"):
                lankar.append((o["nvrid"], d["url"]))
    random.seed(20260727)
    urval = random.sample(lankar, min(antal, len(lankar)))
    brustna = [(n, u) for n, u in urval if not http_ok(u)]
    r.kolla(not brustna, f"D: {len(brustna)} av {len(urval)} dokumentlänkar brustna: "
                         f"{brustna[:5]}")
    if not brustna:
        r.notera(f"D: {len(urval)} slumpvis valda dokumentlänkar av {len(lankar)} "
                 "svarade 200/redirect ✓")


# ---------------------------------------------------------------- kör
r_snabb = False


def main():
    global r_snabb
    ap = argparse.ArgumentParser()
    ap.add_argument("--snabb", action="store_true",
                    help="hoppa över nätberoende länkhälsotest (D)")
    args = ap.parse_args()
    r_snabb = args.snabb

    if not os.path.isdir(DIST):
        sys.exit("dist/ saknas — kör hela pipelinen först (make build).")

    r = Resultat()
    test_a_citatrakenskap(r)
    test_b_geometriproveniens(r)
    test_c_golden(r)
    if not args.snabb:
        test_d_lankhalsa(r)
    else:
        r.notera("D: hoppades över (--snabb)")

    print("\n".join("  " + n for n in r.notiser))
    if r.fel:
        print(f"\nFEL ({len(r.fel)}):")
        for f in r.fel[:40]:
            print("  ✗ " + f)
        if len(r.fel) > 40:
            print(f"  … och {len(r.fel) - 40} till")
        sys.exit(1)
    print("\nAlla tester gröna.")


if __name__ == "__main__":
    main()
