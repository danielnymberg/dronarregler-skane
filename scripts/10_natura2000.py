#!/usr/bin/env python3
"""Steg 10 — Natura 2000-områden.

Varför lagret ser annorlunda ut än naturreservaten
--------------------------------------------------
Ett naturreservat har egna föreskrifter i ett beslutsdokument. Ett Natura
2000-område har det INTE. Regimen är en enda paragraf i miljöbalken:

  7 kap 28 a § — "Tillstånd krävs för att bedriva verksamheter eller vidta
  åtgärder som på ett betydande sätt kan påverka miljön i ett naturområde som
  har förtecknats enligt 27 § första stycket 1 eller 2."

Det betyder att lagret är GEOMETRI plus ETT citat, inte 4 136 dokument att
hämta och OCR:a. Regeltexten hämtas och verifieras i steg 9 som alla andra
författningstexter; här hämtas bara var områdena ligger.

Villkoret "på ett betydande sätt kan påverka miljön" visas ordagrant och lämnas
till läsaren. Tjänsten bedömer det inte — men den döljer det inte heller, vilket
var det ursprungliga (och felaktiga) skälet att skjuta upp lagret.

Licens
------
Metadatan anger "Villkor okända", vilket först behandlades som en blockerare.
Det var inkonsekvent: naturvårdsregistrets metadata säger ordagrant samma sak
("Villkor okända" två gånger, "Inga begränsningar" för åtkomst), och den datan
publicerar tjänsten redan som CC0. Bägge kommer från samma myndighet och samma
öppna data-portal. Antingen är de fria eller ingendera — att behandla dem olika
gick inte att motivera.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.common import (CACHE, CONFIG, DATA, ensure_dir, fetch, log,
                        sha256_bytes, today, write_json)
from lib.geom import bbox, esri_till_geojson
from lib.rutnat import GEOMETRI_GRADER, rutor_for_bbox

WFS = "https://geodata.naturvardsverket.se/n2000/wfs"
TYPNAMN = "N2000_WFS:N2000"

# Områden större än den här gränsen läggs i egna filer och refereras från rutan,
# av samma skäl som fjällreservaten i steg 5: annars kopieras en enda stor
# polygon in i dussintals rutor.
STOR_GRANS = 10 * 1024

# Cloudflare Pages tar max 25 MB per fil. "Torne och Kalix älvsystem" har 3 980
# delar och 1,95 miljoner punkter spridda över hela Norrbotten och blir 37 MB
# som en fil. Områden över den här gränsen lagras DEL FÖR DEL i rutnätet: varje
# polygondel läggs bara i de rutor dess egen bbox rör. Ingen geometri ändras —
# det är samma polygoner, bara indexerade finare.
DELA_GRANS = 5 * 1024 * 1024

DECIMALER = 5          # ~1 m, långt under GPS-osäkerheten


def avrunda(c):
    if isinstance(c[0], (int, float)):
        return [round(c[0], DECIMALER), round(c[1], DECIMALER)]
    return [avrunda(x) for x in c]


def ring_bbox(ring):
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return [min(xs), min(ys), max(xs), max(ys)]


def wfs_url(**extra):
    p = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": TYPNAMN,
        "srsName": "EPSG:4326",
        "outputFormat": "ESRIGEOJSON",
    }
    # None BETYDER "ta bort parametern", inte "hoppa över argumentet". Den
    # skillnaden var en tyst bugg: resultType=hits skickades med outputFormat
    # kvar, servern svarade JSON i stället för XML, antalet gick inte att läsa
    # och avstämningen mot tyst kapning passerade utan att ha körts. En
    # säkerhetskontroll som inte kan köra ska säga ifrån, inte tiga.
    for k, v in extra.items():
        if v is None:
            p.pop(k, None)
        else:
            p[k] = v
    return f"{WFS}?{urllib.parse.urlencode(p)}"


def antal_enligt_servern():
    """Samma avstämning som mot naturvårdsregistret.

    Den disciplinen finns för att servern där tyst kapade svaret vid 500 poster.
    En tyst kapning här skulle ge områden som saknas på kartan utan att något
    larmar — den farligaste sortens fel tjänsten kan göra.
    """
    xml = fetch(wfs_url(resultType="hits", outputFormat=None),
                min_interval=1.0).decode("utf-8", "replace")
    for nyckel in ("numberMatched=", "numberOfFeatures="):
        if nyckel in xml:
            tal = xml.split(nyckel, 1)[1][1:].split('"', 1)[0]
            if tal.isdigit():
                return int(tal)
    return None


def stad(v):
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("", "null", "none") else s


def main():
    vantat = antal_enligt_servern()
    if vantat is None:
        raise SystemExit(
            "Steg 10 avbryter: kunde inte läsa antalet objekt från servern. "
            "Utan det går avstämningen mot tyst kapning inte att göra, och en "
            "kontroll som inte kan köra får inte passera som godkänd.")
    log(f"  servern uppger {vantat} Natura 2000-områden")
    # Råsvaret cachas. Steg 10 gjorde det INTE i första versionen, till
    # skillnad från steg 1 och 2, och när lagringsmodellen visade sig behöva
    # göras om kostade det en timmes omhämtning. Ett steg vars utdata kan behöva
    # byggas om ska aldrig behöva fråga källan igen.
    rafil = os.path.join(ensure_dir(os.path.join(CACHE, "n2000")), "n2000.json")
    if os.path.exists(rafil):
        with open(rafil, "rb") as fh:
            ra = fh.read()
        log(f"  {len(ra) // 1048576} MB ur cache")
    else:
        ra = fetch(wfs_url(), min_interval=1.0, timeout=900)
        with open(rafil, "wb") as fh:
            fh.write(ra)
        log(f"  {len(ra) // 1048576} MB hämtat och cachat")
    hashv = sha256_bytes(ra)
    d = json.loads(ra.decode("utf-8"))
    features = d.get("features", [])
    if vantat is not None and len(features) != vantat:
        raise SystemExit(
            f"Steg 10 avbryter: servern uppger {vantat} områden men levererade "
            f"{len(features)}. Ett ofullständigt lager är värre än inget lager.")

    ensure_dir(os.path.join(DATA, "n2000"))
    ensure_dir(os.path.join(DATA, "n2000", "stora"))
    per_ruta, stora, index, delade = {}, {}, [], {}
    utan_geometri = 0

    for f in features:
        a = f.get("attributes") or f.get("properties") or {}
        # esri_till_geojson returnerar en FEATURE, inte en geometri.
        geom = (esri_till_geojson(f) or {}).get("geometry")
        if not geom:
            utan_geometri += 1
            continue
        geom = {"type": geom["type"], "coordinates": avrunda(geom["coordinates"])}
        kod = stad(a.get("OMRADESKOD"))
        post = {
            "kod": kod,
            "namn": stad(a.get("OMRADESNAMN")),
            "typ": stad(a.get("OMRADESTYP")),        # SPA / SCI / SAC
            "lan": stad(a.get("LAN")),
            "kommun": stad(a.get("KOMMUN")),
            "area_ha": a.get("AREA_HA"),
            "naturtyper": stad(a.get("NATURTYPER")),
        }
        bb = [round(x, 6) for x in bbox(geom)]
        index.append([kod, post["namn"], post["typ"], post["lan"],
                      post["kommun"]] + bb)

        rutor = rutor_for_bbox(bb, GEOMETRI_GRADER)
        kompakt = json.dumps(geom, separators=(",", ":"))
        if len(kompakt) > DELA_GRANS:
            # Jättar delas per polygondel, annars spränger de filtaket.
            delar = (geom["coordinates"] if geom["type"] == "MultiPolygon"
                     else [geom["coordinates"]])
            delade[kod] = len(delar)
            for d in delar:
                for rid in rutor_for_bbox(ring_bbox(d[0]), GEOMETRI_GRADER):
                    cell = per_ruta.setdefault(rid, {"omraden": {}, "stora": []})
                    cell["omraden"].setdefault(kod, {"type": "MultiPolygon",
                                                     "coordinates": []})
                    cell["omraden"][kod]["coordinates"].append(d)
        elif len(kompakt) > STOR_GRANS and len(rutor) > 1:
            stora[kod] = geom
            for rid in rutor:
                per_ruta.setdefault(rid, {"omraden": {}, "stora": []})["stora"].append(kod)
        else:
            for rid in rutor:
                per_ruta.setdefault(rid, {"omraden": {}, "stora": []})["omraden"][kod] = geom

    for kod, geom in stora.items():
        write_json(os.path.join(DATA, "n2000", "stora", f"{kod}.json"), geom,
                   compact=True)
    for rid, v in per_ruta.items():
        write_json(os.path.join(DATA, "n2000", f"{rid}.json"), v, compact=True)

    # Egenskaperna i en egen fil: rutnätet bär geometri, indexet bär namnen.
    write_json(os.path.join(DATA, "n2000-index.json"), {
        "schema_version": 1,
        "kalla": "Naturvårdsverket, Natura 2000",
        "kalla_url": WFS,
        "hamtad": today(),
        "svarshash": hashv,
        "regel": ("Natura 2000-områden har inga egna föreskrifter. "
                  "Tillståndsplikten följer av 7 kap 28 a § miljöbalken, som "
                  "citeras ordagrant på /regler/."),
        "kolumner": ["kod", "namn", "typ", "lan", "kommun",
                     "minx", "miny", "maxx", "maxy"],
        "rader": index,
    }, compact=True)

    total = sum(os.path.getsize(os.path.join(DATA, "n2000", f))
                for f in os.listdir(os.path.join(DATA, "n2000"))
                if f.endswith(".json"))
    storsta = max((os.path.getsize(os.path.join(DATA, "n2000", f)), f)
                  for f in os.listdir(os.path.join(DATA, "n2000"))
                  if f.endswith(".json"))
    log(f"Steg 10 klart: {len(index)} områden i {len(per_ruta)} rutor "
        f"({len(stora)} i egna filer, {len(delade)} delade per polygondel), "
        f"{total // 1048576} MB, största ruta {storsta[0] // 1048576} MB, "
        f"{utan_geometri} utan geometri")
    if delade:
        log(f"  delade: {', '.join(f'{k} ({n} delar)' for k, n in delade.items())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
