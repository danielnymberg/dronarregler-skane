#!/usr/bin/env python3
"""Steg 5 — Databygge.

Producerar data/ som en fristående, publicerbar CC0-produkt:

  areas.geojson            förenklad visningsgeometri för kartan
  bbox-index.json          liten omslutande-rektangel-index för positionssvar
  omraden/{nvrid}.json     originalgeometri + allt verifierat innehåll
  manifest.json            proveniens per objekt (skrivs av steg 1–2)
  verification-report.json verifieringsutfall (skrivs av steg 4)
  LICENSE, README.md       licens och schemabeskrivning

Originalgeometrin behålls oförändrad i omraden/{nvrid}.json och är den enda
geometri som används för punkt-i-polygon. areas.geojson är enbart visning.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.common import (CACHE, CONFIG, DATA, ensure_dir, log, read_json,
                        slugify, today, write_json)
from lib.geom import bbox, forenkla_geometri, ring_area_m2

TOL = CONFIG["simplify_tolerance_m"]


def slug_ihop(objekt):
    """Unika slugs; kollisioner får NVRID-suffix."""
    tagna, ut = {}, {}
    for nvrid, rec in sorted(objekt.items()):
        s = slugify(rec["namn"] or "omrade")
        if s in tagna:
            s = f"{s}-{nvrid}"
        tagna[s] = nvrid
        ut[nvrid] = s
    return ut


def sammanfoga_geometrier(features):
    """Slår ihop flera WFS-rader med samma NVRID till en MultiPolygon.

    Ingen ny geometri skapas — delarna läggs bara i samma MultiPolygon.
    """
    per_nvrid = {}
    for f in features:
        nvrid = str(f["properties"].get("NVRID") or "").strip()
        if not nvrid:
            continue
        g = f.get("geometry")
        if not g:
            continue
        polys = per_nvrid.setdefault(nvrid, [])
        if g["type"] == "MultiPolygon":
            polys.extend(g["coordinates"])
        elif g["type"] == "Polygon":
            polys.append(g["coordinates"])
    return {n: {"type": "MultiPolygon", "coordinates": p} for n, p in per_nvrid.items()}


def sortera_dokument(dokument):
    """Nyast beslut först. Tjänsten avgör inte vilken lydelse som gäller —
    men den visar besluten i datumordning så läsaren själv kan se ordningen."""
    def nyckel(d):
        return (d.get("beslutsstatus") != "Gällande",
                -(d.get("beslutsid") and int(d["beslutsid"]) or 0))
    return sorted(dokument, key=nyckel)


LICENS = """CC0 1.0 Universal (CC0 1.0) Public Domain Dedication

Den databas som ligger i den här katalogen — områdesregister, geometrier,
citat, verifieringsutfall och manifest — tillgängliggörs under CC0 1.0.
Du får kopiera, ändra, distribuera och använda materialet, även kommersiellt,
utan att fråga om lov.

Fullständig licenstext: https://creativecommons.org/publicdomain/zero/1.0/legalcode.sv

UNDERLIGGANDE KÄLLOR
- Områdesgeometrier och registeruppgifter kommer ur Naturvårdsverkets
  naturvårdsregister (NVR), som tillgängliggörs under CC0.
- Citaten är ordagranna utdrag ur svenska myndighetsbeslut. Sådana beslut är
  allmänna handlingar och omfattas inte av upphovsrätt enligt 9 § upphovsrätts-
  lagen (1960:729).

INGÅR INTE I DENNA LICENS
- LFV:s luftrumsdata. Den visas i tjänsten enbart som rasterlager direkt från
  LFV:s egen WMS-server under CC BY-NC-ND 4.0 och finns inte i den här
  katalogen. Ingen LFV-geometri har lagrats, bearbetats eller återpublicerats.
"""


def skriv_licens_och_schema(manifest, stats, forenkling):
    with open(os.path.join(DATA, "LICENSE"), "w", encoding="utf-8") as fh:
        fh.write(LICENS)
    readme = f"""# Databas: skyddade områden i Skåne län med föreskriftscitat

Hämtningsdatum: **{manifest['hamtningsdatum']}**
Licens: **CC0 1.0** (se `LICENSE`)
Antal objekt: **{manifest['antal_objekt']}**

Databasen är byggd för att kunna läsas fristående från webbtjänsten. Varje
uppgift går att spåra till det myndighets-API-svar den kom ur.

## Filer

| Fil | Innehåll |
|-----|----------|
| `areas.geojson` | Förenklad visningsgeometri för kartrendering. **Använd inte för punkt-i-polygon.** |
| `bbox-index.json` | Omslutande rektangel per objekt, för snabb kandidatsökning. |
| `omraden/{{nvrid}}.json` | Ett objekt: **oförenklad originalgeometri**, verifierade citat, dokumentlänkar, proveniens. |
| `manifest.json` | Proveniens per objekt och per lager: källa, svarshash, hämtningsdatum. |
| `verification-report.json` | Verifieringens utfall, inklusive varje kasserat citat och orsak. |
| `verifierade-citat.json` | Citaten som klarade verifieringen, per NVRID. |
| `extraktion.json` | Extraktionens råutfall före verifiering (för granskning). |

## Schema: `omraden/{{nvrid}}.json`

```
nvrid                 str    Naturvårdsregistrets objekt-id
slug                  str    URL-slug
namn, skyddstyp       str
lager                 str    lager-id (en rättskälla per lager)
karnlager             bool   ingår i kärnlagret som alltid laddas
kommun, lan           str
beslutsstatus         str
beslutsmyndighet      str|null
forvaltare            str|null
tillsynsmyndighet     str|null
urspr_beslutsdatum    str|null   ISO-datum
senaste_gallandedatum str|null   ISO-datum
area_ha               float|null
sknat_url             str    områdets sida i Kartverktyget Skyddad natur
svarslage             str    "reglerat-las-beslutet" | "lanklage"
ocr                   bool   minst ett dokument är OCR-tolkat
foreskriftsomraden    list   råa poster ur registret
sasongsdata           list   delmängd med franDatum/tillDatum satta
dokument              list   {{namn, url, dokument_id, sha256, sidor, ocr, beslutsstatus, fel}}
dokument_hoppade      list   {{namn, url, orsak}} — dokument tjänsten inte läst
citat                 list   se nedan
geometri              GeoJSON-geometri | null  — OFÖRENKLAD originalgeometri
bbox                  [minx, miny, maxx, maxy] | null
geometri_kalla        {{tjanst, url, typeName, svarshash_sha256, hamtningsdatum, licens}}
hamtningsdatum        str
```

### Citatobjekt

```
citat                 str   ordagrann delsträng ur dokumentets extraherade text
punkt                 str|null  punkt-/paragrafmarkör, t.ex. "7." eller "§ 5"
sidnummer             int   1-indexerat sidnummer i källdokumentet
teckenoffset_pa_sidan int   startposition i sidans text
klassificering        str   uttryckligt-luftfartygsförbud | start-landningsförbud |
                            motorfordon-möjligen-relevant | störningsförbud-djurliv |
                            annat-läs-beslutet
konfidens             str   "hög" om segmentet innehåller ett förbuds- eller
                            tillståndsuttryck, annars "medel"
dokument_id           str
dokument_namn         str
dokument_url          str   direktlänk till källdokumentet
dokument_sha256       str   hash av den nedladdade filen
ocr                   bool  texten är OCR-tolkad ur inskannat original
verifierad            bool  alltid true i denna fil
verifieringsmetod     str
```

## Klassificeringen är en etikett, inte en bedömning

`klassificering` säger vilken sorts nyckelord citatet innehåller. Den säger
ingenting om vad föreskriften betyder eller om en viss flygning är i sin
ordning. Substansen är citatet.

## Geometri

- `omraden/{{nvrid}}.json` → `geometri` är **oförenklad** och identisk med
  WFS-svaret från Naturvårdsverket, bortsett från att flera WFS-rader med
  samma NVRID lagts i samma MultiPolygon. Det är den geometri som ska
  användas för punkt-i-polygon.
- `areas.geojson` är förenklad med Douglas–Peucker, tolerans
  **{forenkling['tolerans_m']} m**, med krympgaranti: en ring vars yta skulle
  bli större av förenklingen behålls oförenklad. Vid detta bygge behölls
  {forenkling['ringar_behallna']} ringar oförenklade, punktantalet gick från
  {forenkling['punkter_fore']} till {forenkling['punkter_efter']}, och största
  ytminskning för ett enskilt objekt var
  {forenkling['max_ytminskning_procent']} %.
- Inga buffertar, cirklar eller uppskattade zoner förekommer någonstans i
  databasen.

## Kända luckor

- Naturvårdsverkets punktlager `SkyddadePunkter` returnerar noll objekt för
  Skåne län vid hämtningen. Naturminnen finns i ytlagret.
- Endast dokument vars filnamn pekar ut dem som beslut, föreskrifter,
  förordnanden, kungörelser eller ändringsbeslut har lästs. Övriga listas per
  objekt i `dokument_hoppade`.
- Ett område kan ha flera beslut där ett senare ändrat ett tidigare.
  Databasen anger inte vilken lydelse som gäller i dag.
- Luftrumsdata (flygplatser, restriktionsområden, NOTAM, geografiska
  UAS-zoner) ingår inte.

## Statistik vid detta bygge

- Objekt med minst ett verifierat citat: {stats['med_verifierade_citat']}
- Objekt i länk-läge: {stats['lanklage']}
- Objekt med OCR-tolkad text: {stats['med_ocr']}
- Objekt med säsongsdata: {stats['med_sasongsdata']}
- Objekt utan geometri: {stats['utan_geometri']}
"""
    with open(os.path.join(DATA, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(readme)


def main():
    manifest = read_json(os.path.join(DATA, "manifest.json"))
    verifierade = read_json(os.path.join(DATA, "verifierade-citat.json"))
    if manifest is None or verifierade is None:
        sys.exit("Kör scripts/01–04 först.")
    citat_per_nvrid = verifierade["citat"]

    raw = read_json(os.path.join(CACHE, "raw", "nvr_omraden_skane.geojson"))
    geometrier = sammanfoga_geometrier(raw["features"])
    log(f"{len(geometrier)} geometrier sammanfogade ur {len(raw['features'])} WFS-rader")

    objekt = manifest["objekt"]
    slugs = slug_ihop(objekt)
    ensure_dir(os.path.join(DATA, "omraden"))

    features, bbox_index = [], []
    forenkling = {"punkter_fore": 0, "punkter_efter": 0, "ringar_behallna": 0,
                  "max_ytminskning_procent": 0.0, "objekt_med_ytokning": 0}
    stats = {"med_verifierade_citat": 0, "lanklage": 0, "utan_geometri": 0,
             "med_ocr": 0, "med_sasongsdata": 0}

    for nvrid, rec in sorted(objekt.items()):
        geom = geometrier.get(nvrid)
        citat = citat_per_nvrid.get(nvrid) or []
        dokument = sortera_dokument(rec.get("dokument") or [])
        ocr = any(d.get("ocr") for d in dokument)
        sasong = [f for f in (rec.get("foreskriftsomraden") or [])
                  if f.get("franDatum") or f.get("tillDatum")]

        if citat:
            svarslage = "reglerat-las-beslutet"
            stats["med_verifierade_citat"] += 1
        else:
            svarslage = "lanklage"
            stats["lanklage"] += 1
        if ocr:
            stats["med_ocr"] += 1
        if sasong:
            stats["med_sasongsdata"] += 1

        omrade = {
            "nvrid": nvrid,
            "slug": slugs[nvrid],
            "namn": rec["namn"],
            "skyddstyp": rec["skyddstyp"],
            "lager": rec["lager"],
            "karnlager": rec["karnlager"],
            "kommun": rec["kommun"],
            "lan": rec["lan"],
            "beslutsstatus": rec["beslutsstatus"],
            "beslutsmyndighet": rec["beslutsmyndighet"],
            "forvaltare": rec["forvaltare"],
            "tillsynsmyndighet": rec.get("tillsynsmyndighet"),
            "urspr_beslutsdatum": rec.get("urspr_beslutsdatum"),
            "senaste_gallandedatum": rec.get("senaste_gallandedatum"),
            "area_ha": rec.get("area_ha"),
            "sknat_url": rec["sknat_url"],
            "svarslage": svarslage,
            "ocr": ocr,
            "foreskriftsomraden": rec.get("foreskriftsomraden") or [],
            "sasongsdata": sasong,
            "dokument": [{
                "namn": d.get("namn"),
                "url": d.get("url"),
                "dokument_id": d.get("dokument_id"),
                "sha256": d.get("sha256"),
                "sidor": d.get("sidor"),
                "ocr": bool(d.get("ocr")),
                "beslutsstatus": d.get("beslutsstatus"),
                "fel": d.get("fel"),
            } for d in dokument],
            "dokument_hoppade": [{"namn": d.get("namn"), "url": d.get("url"),
                                  "orsak": d.get("orsak")}
                                 for d in rec.get("dokument_hoppade") or []],
            "citat": citat,
            "geometri_kalla": rec["geometri_kalla"],
            "hamtningsdatum": rec["geometri_kalla"]["hamtningsdatum"],
        }

        if geom is None:
            stats["utan_geometri"] += 1
            omrade["geometri"] = None
            omrade["bbox"] = None
        else:
            omrade["geometri"] = geom       # OFÖRENKLAD — används för punkt-i-polygon
            bb = bbox(geom)
            omrade["bbox"] = bb
            enkel, fstat = forenkla_geometri(geom, TOL)
            forenkling["punkter_fore"] += fstat["punkter_fore"]
            forenkling["punkter_efter"] += fstat["punkter_efter"]
            forenkling["ringar_behallna"] += fstat["ringar_behallna"]
            if fstat["yta_fore_m2"] > 0:
                minskning = (1 - fstat["yta_efter_m2"] / fstat["yta_fore_m2"]) * 100
                forenkling["max_ytminskning_procent"] = max(
                    forenkling["max_ytminskning_procent"], round(minskning, 3))
                if minskning < -0.0001:
                    forenkling["objekt_med_ytokning"] += 1
            features.append({
                "type": "Feature",
                "geometry": enkel,
                "properties": {
                    "nvrid": nvrid,
                    "namn": rec["namn"],
                    "skyddstyp": rec["skyddstyp"],
                    "lager": rec["lager"],
                    "karnlager": rec["karnlager"],
                    "slug": slugs[nvrid],
                    "svarslage": svarslage,
                    "antal_citat": len(citat),
                    "ocr": ocr,
                    "sasong": bool(sasong),
                },
            })
            bbox_index.append([nvrid, slugs[nvrid], rec["namn"], rec["skyddstyp"],
                               rec["lager"], svarslage] + bb)

        write_json(os.path.join(DATA, "omraden", f"{nvrid}.json"), omrade, compact=True)

    write_json(os.path.join(DATA, "areas.geojson"),
               {"type": "FeatureCollection",
                "hamtningsdatum": manifest["hamtningsdatum"],
                "forenkling_tolerans_m": TOL,
                "features": features}, compact=True)
    write_json(os.path.join(DATA, "bbox-index.json"), {
        "schema_version": 1,
        "hamtningsdatum": manifest["hamtningsdatum"],
        "kolumner": ["nvrid", "slug", "namn", "skyddstyp", "lager", "svarslage",
                     "minx", "miny", "maxx", "maxy"],
        "rader": bbox_index,
    }, compact=True)

    skriv_licens_och_schema(manifest, stats, forenkling)

    manifest["databygge"] = {
        "datum": today(),
        "forenkling": {**forenkling, "tolerans_m": TOL,
                       "regel": "Douglas–Peucker med krympgaranti: en ring vars "
                                "yta skulle växa av förenklingen behålls oförenklad"},
        "statistik": stats,
    }
    write_json(os.path.join(DATA, "manifest.json"), manifest)
    log(f"Steg 5 klart: {len(features)} ytor, {stats}")
    log(f"  förenkling: {forenkling['punkter_fore']} → {forenkling['punkter_efter']} "
        f"punkter, max ytminskning {forenkling['max_ytminskning_procent']} %, "
        f"objekt med ytökning: {forenkling['objekt_med_ytokning']}")


if __name__ == "__main__":
    main()
