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

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.common import (CACHE, CONFIG, DATA, ensure_dir, log, read_json,
                        slugify, today, write_json)
from lib.geom import bbox, forenkla_geometri, ring_area_m2
from lib.rutnat import GEOMETRI_GRADER, VISNING_GRADER, rutor_for_bbox

TOL = CONFIG["simplify_tolerance_m"]


MAX_YTFORLUST_PROCENT = 2.0
TOLERANSTRAPPA = (TOL, TOL / 3.0, TOL / 10.0)


def forenkla_med_ytgrans(geom):
    """Förenkla, men aldrig mer än MAX_YTFORLUST_PROCENT av objektets nettoyta.

    En fast tolerans på 15 m är rimlig för ett stort reservat men äter en
    orimlig andel av ett litet, avlångt objekt — vid mätning tappade ett objekt
    18,6 % av sin yta. Toleransen trappas därför ned per objekt tills förlusten
    ligger under gränsen, och i sista hand behålls originalgeometrin.

    Returnerar (geometri, statistik, använd_tolerans_m). Använd tolerans 0
    betyder att originalet behölls.
    """
    for tol in TOLERANSTRAPPA:
        enkel, fstat = forenkla_geometri(geom, tol)
        fore = fstat["yta_fore_m2"]
        if fore <= 0:
            return enkel, fstat, tol
        forlust = (1 - fstat["yta_efter_m2"] / fore) * 100
        if forlust <= MAX_YTFORLUST_PROCENT:
            return enkel, fstat, tol
    _, fstat = forenkla_geometri(geom, 0.0)
    fstat["punkter_efter"] = fstat["punkter_fore"]
    fstat["yta_efter_m2"] = fstat["yta_fore_m2"]
    return geom, fstat, 0.0


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


def netto_yta_m2(geom):
    """Nettoyta: ytterringar minus hål."""
    if not geom:
        return 0.0
    polys = (geom["coordinates"] if geom["type"] == "MultiPolygon"
             else [geom["coordinates"]])
    return sum(ring_area_m2(r) * (1 if j == 0 else -1)
               for rings in polys for j, r in enumerate(rings))


def arealavstamning(geom, area_ha):
    """Stäm av den hämtade geometrin mot registrets eget arealfält.

    Den här kontrollen finns för att en tyst geometrifel annars är osynlig.
    Tjänstens GeoJSON-utskrift plattade ihop flerdelade områden till en polygon
    där de övriga delarna blev "hål": naturreservatet Verkeån blev 374 ha i
    stället för registrets 1 424 ha, och en position i den bortplattade delen
    fick svaret "ingen restriktion hittad". AREA_HA är en oberoende uppgift ur
    samma post och avslöjar sådant direkt.

    Returnerar (beraknad_ha, avvikelse_procent) eller (None, None).
    """
    if not area_ha or area_ha <= 0 or not geom:
        return None, None
    beraknad = netto_yta_m2(geom) / 10_000.0
    return round(beraknad, 2), round((beraknad - area_ha) / area_ha * 100, 2)


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
- LFV:s luftrumsdata i filen `lfv.json`. Den kommer ur LFV:s DAIM och står
  under CC BY-NC-ND 4.0 — © LFV. Den filen omfattas alltså INTE av CC0-
  upplåtelsen ovan, och får inte vidarelicensieras. Vill du använda
  luftrumsdata, hämta den från LFV: https://daim.lfv.se/
  Allt annat i katalogen är CC0.
"""


# Klassificeringar ur steg 3, grupperade efter vad de betyder för en drönare.
# Grupperingen avgör bara vilken rubrik citatet hamnar under — själva texten
# visas alltid ordagrant, och rubriken påstår aldrig något som citatet inte
# säger.
LUFTFART_KLASSER = {"uttryckligt-luftfartygsförbud", "start-landningsförbud"}
STORNING_KLASSER = {"störningsförbud-djurliv"}


def luftfartslage(citat, dokument):
    """Vad tjänsten faktiskt vet om luftfart i det här området.

    Det gamla `svarslage` gick inte att förstå för den som stod på marken. Det
    hade tre värden, och det mest folkrika — `lanklage`, 5 485 områden — dolde
    två helt olika besked bakom samma ord: "beslutet är läst och innehöll
    ingenting om luftfart" och "det finns inget beslut att läsa". Den första är
    ett svar. Den andra är en lucka. Att visa dem likadant är precis den sortens
    otydlighet tjänsten finns till för att slippa.

    Sex lägen, ordnade efter hur säkert tjänsten kan uttala sig. Inget av dem
    är ett klartecken; de skiljer på vad som är läst och vad som inte är det.
    """
    klasser = {c.get("klassificering") for c in citat}
    if klasser & LUFTFART_KLASSER:
        return "luftfart"            # föreskriften nämner luftfart ordagrant
    if klasser & STORNING_KLASSER:
        return "storning"            # störningsförbud — kan träffa en drönare
    if citat:
        return "last-annat"          # läst, föreskrifter finns, inget av ovan
    lasta = [d for d in dokument if d.get("sidor") is not None]
    if lasta:
        return "last-tomt"           # läst, inget föreskriftsliknande hittat
    if dokument:
        return "olast"               # dokument finns men är inte lästa
    return "utan-dokument"           # inget digitalt beslut att läsa


def bygg_rutnat(features, bbox_rader, geometrier):
    """Skriver visningsrutorna, geometrirutorna och bbox-rutorna.

    Se lib/rutnat.py för varför det är två rutstorlekar.

    Det fanns tidigare också en översiktsfil för hela landet, tänkt för utzoomat
    läge. Den togs bort. Rikstäckande geometri går inte att förenkla hårt utan
    att bryta kravet att en förenkling aldrig får påstå större utbredning än
    myndighetens geometri — mätt blev filen 13 MB, och de varianter som var
    små nog tappade 1 460 av 4 697 områden helt. En karta som tyst utelämnar
    en tredjedel av områdena är sämre än en karta som säger "zooma in".
    """
    for katalog in ("rutor", "geom", "bbox"):
        ensure_dir(os.path.join(DATA, katalog))

    # --- visningsrutor (1°), förenklad geometri ---
    per_ruta = {}
    for f in features:
        for rid in rutor_for_bbox(bbox(f["geometry"]), VISNING_GRADER):
            per_ruta.setdefault(rid, []).append(f)
    for rid, fs in per_ruta.items():
        write_json(os.path.join(DATA, "rutor", f"{rid}.json"),
                   {"type": "FeatureCollection", "features": fs}, compact=True)

    # --- geometrirutor (0,25°), ORIGINALGEOMETRI för punkt-i-polygon ---
    #
    # Storleksfördelningen är extremt skev: av 10 681 områden är medianen 1,9 kB
    # medan 50 stycken — fjällreservaten — är över 100 kB och tillsammans 30 av
    # 76 MB. Läggs de i varje ruta de rör kopieras Vindelfjällen in i dussintals
    # rutor och katalogen växer till 386 MB med enskilda rutor på 5,7 MB.
    #
    # De stora får därför egna filer och refereras från rutan. Gränsen 10 kB är
    # mätt: den ger 435 egna filer och 81 MB totalt, mot 41 filer och 118 MB vid
    # 100 kB. Geometrin
    # förenklas inte: en förenkling som krymper ytan skulle kunna ge svaret
    # "ingen restriktion hittad" åt någon som står strax innanför gränsen, och
    # en som växer ytan går inte att kombinera med meningsfull komprimering.
    ensure_dir(os.path.join(DATA, "geom", "stora"))
    STOR_GRANS = 10 * 1024
    stora, per_geom = {}, {}
    for nvrid, geom in geometrier.items():
        if not geom:
            continue
        rutor = rutor_for_bbox(bbox(geom), GEOMETRI_GRADER)
        if len(json.dumps(geom, separators=(",", ":"))) > STOR_GRANS and len(rutor) > 1:
            stora[nvrid] = geom
            for rid in rutor:
                per_geom.setdefault(rid, {"omraden": {}, "stora": []})["stora"].append(nvrid)
        else:
            for rid in rutor:
                per_geom.setdefault(rid, {"omraden": {}, "stora": []})["omraden"][nvrid] = geom
    for nvrid, geom in stora.items():
        write_json(os.path.join(DATA, "geom", "stora", f"{nvrid}.json"), geom,
                   compact=True)
    for rid, d in per_geom.items():
        write_json(os.path.join(DATA, "geom", f"{rid}.json"), d, compact=True)

    # --- bbox-rutor (0,25°), kandidatsökning inför positionssvaret ---
    per_bbox = {}
    for rad in bbox_rader:
        for rid in rutor_for_bbox(rad[6:10], GEOMETRI_GRADER):
            per_bbox.setdefault(rid, []).append(rad)
    for rid, rader in per_bbox.items():
        write_json(os.path.join(DATA, "bbox", f"{rid}.json"),
                   {"rader": rader}, compact=True)

    return {
        "visning_grader": VISNING_GRADER,
        "geometri_grader": GEOMETRI_GRADER,
        "antal_visningsrutor": len(per_ruta),
        "antal_geometrirutor": len(per_geom),
        "antal_stora_geometrifiler": len(stora),
        "stor_grans_byte": STOR_GRANS,
        "antal_bboxrutor": len(per_bbox),
    }


def _kompakt_citat(c):
    """Ett citat nedbantat till det panelen behöver — utan att röra texten."""
    return {
        "t": c["citat"],                      # ordagrann text
        "i": c.get("inledning") or "",        # föreskriftens inledning
        "p": c.get("punkt"),
        "s": c.get("sidnummer"),
        "k": c["klassificering"],
        "u": c.get("dokument_url"),
        "d": c.get("dokument_namn"),
        "o": bool(c.get("ocr")),
        "w": c.get("traffade_ord") or [],   # orden som gjorde att citatet valdes
    }


def bygg_citatlager(citat_per_nvrid, bbox_rader):
    """Gör citaten läsbara direkt i kartsvaret i stället för en klick bort.

    Det var den skarpaste bristen i förra versionen: kartan sa "Reglerat område
    — läs beslutet" och lämnade dig där. Om föreskriften faktiskt sa "förbjudet
    att starta och landa med luftfarkost" fick du inte veta det förrän du öppnat
    en sida till. Svaret på frågan fanns i datan men nådde aldrig fram.

    Två filer av två skäl:

      data/luftfart.json    De 298 områden vars föreskrifter nämner luftfart
                            ordagrant. 274 kB — litet nog att bäras i telefonen
                            och besvaras UTAN NÄT, vilket är förutsättningen för
                            att vaktläget ska hinna varna innan du lyfter.
      data/citat/{ruta}.json Störningsförbuden. 3 044 områden, 3,5 MB — för
                            mycket att bära, men de hämtas ändå bara för rutan
                            man står i.
    """
    ensure_dir(os.path.join(DATA, "citat"))

    luftfart = {}
    storning = {}
    for nvrid, lista in citat_per_nvrid.items():
        luft = [_kompakt_citat(c) for c in lista
                if c["klassificering"] in LUFTFART_KLASSER]
        stor = [_kompakt_citat(c) for c in lista
                if c["klassificering"] in STORNING_KLASSER]
        if luft:
            luftfart[nvrid] = luft
        # Nämns luftfart uttryckligen är det den träffen som gäller; att också
        # visa störningsförbudet gör svaret längre utan att göra det klarare.
        elif stor:
            storning[nvrid] = stor

    write_json(os.path.join(DATA, "luftfart.json"), {
        "schema_version": 1,
        "hamtningsdatum": today(),
        "beskrivning": ("Verifierade citat ur beslut vars föreskrifter nämner "
                        "luftfart ordagrant. Bärs offline av appen."),
        "citat": luftfart,
    }, compact=True)

    per_ruta = {}
    for rad in bbox_rader:
        nvrid = rad[0]
        if nvrid not in storning:
            continue
        for rid in rutor_for_bbox(rad[6:10], GEOMETRI_GRADER):
            per_ruta.setdefault(rid, {})[nvrid] = storning[nvrid]
    for rid, d in per_ruta.items():
        write_json(os.path.join(DATA, "citat", f"{rid}.json"), {"citat": d},
                   compact=True)

    return {
        "omraden_med_luftfartscitat": len(luftfart),
        "omraden_med_storningscitat": len(storning),
        "luftfartsfil_byte": os.path.getsize(os.path.join(DATA, "luftfart.json")),
        "antal_citatrutor": len(per_ruta),
    }


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
inledning             str|null  föreskriftsblockets rubrik ("Det är förbjudet att:"),
                            en EGEN sammanhängande delsträng ur samma dokument —
                            aldrig hopskarvad med citatet — separat verifierad.
                            null om ingen rubrik kunde verifieras.
inledning_sidnummer   int|null  sidan rubriken står på (kan vara sidan före)
punkt                 str|null  punkt-/paragrafmarkör, t.ex. "7." eller "§ 5"
sidnummer             int   1-indexerat sidnummer i källdokumentet
teckenoffset_pa_sidan int   startposition i sidans text
klassificering        str   uttryckligt-luftfartygsförbud | start-landningsförbud |
                            motorfordon-möjligen-relevant | störningsförbud-djurliv |
                            annat-läs-beslutet
konfidens             str   "hög"  = förbudsuttryck står i punkten själv
                            "medel" = förbudsuttryck står i den verifierade
                                      rubriken, eller punkten är numrerad
                            "låg"   = varken eller; texten kan lika gärna komma
                                      ur beslutets skäl som ur dess föreskrifter
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
- `areas.geojson` är förenklad med Douglas–Peucker, utgångstolerans
  **{forenkling['tolerans_m']} m**, koordinater avrundade till
  {forenkling['koordinatdecimaler']} decimaler (~1 m), med två garantier:

  1. **Ytan växer aldrig.** Ytterringar får inte bli större, hålringar inte
     mindre, och skulle en polygons nettoyta ändå ha vuxit behålls polygonen
     oförenklad. Garantin kontrolleras på exakt de koordinater som hamnar i
     filen, efter avrundning.
  2. **Ytförlusten per objekt är högst
     {forenkling['max_ytforlust_procent_per_objekt']} %.** Toleransen trappas
     ned per objekt tills kravet är uppfyllt, och i sista hand behålls
     originalgeometrin. En fast tolerans är rimlig för ett stort reservat men
     äter en orimlig andel av ett litet, avlångt objekt.

  Vid detta bygge: punktantalet gick från {forenkling['punkter_fore']} till
  {forenkling['punkter_efter']}, {forenkling['ringar_behallna']} ringar behölls
  oförenklade, största ytminskning för ett enskilt objekt var
  {forenkling['max_ytminskning_procent']} % (NVRID
  {forenkling['max_ytminskning_procent_objekt']}), och hela datamängdens yta
  gick från {forenkling['total_yta_fore_m2']:.0f} till
  {forenkling['total_yta_efter_m2']:.0f} m².
  Använd tolerans per objekt: {forenkling['objekt_per_anvand_tolerans']}.
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
    forenkling = {"tolerans_m": TOL, "koordinatdecimaler": 5,
                  "punkter_fore": 0, "punkter_efter": 0, "ringar_behallna": 0,
                  "total_yta_fore_m2": 0.0, "total_yta_efter_m2": 0.0,
                  "max_ytminskning_procent": 0.0,
                  "max_ytminskning_procent_objekt": None,
                  "objekt_med_ytokning": 0,
                  "max_ytforlust_procent_per_objekt": MAX_YTFORLUST_PROCENT,
                  "objekt_per_anvand_tolerans": {},
                  "_matgrans_ha": 1.0}
    stats = {"med_verifierade_citat": 0, "lanklage": 0, "beslut_ej_last": 0,
             "utan_geometri": 0, "med_ocr": 0, "med_sasongsdata": 0}
    # Riktningen betyder olika saker. Geometri MINDRE än registrerad areal är
    # farligt: delar av området saknas på kartan, och en position där får svaret
    # "ingen restriktion hittad". Geometri STÖRRE är i praktiken registrets egen
    # inkonsekvens — naturreservatet Norra Ljunghusen har AREA_HA 15,64 medan
    # dess egen polygon är 186 ha, i både GEOJSON- och ESRI-utskriften. Det är
    # inget vi kan rätta, men det ska redovisas.
    areal = {"provade": 0, "mindre_an_registrerat_over_25_procent": 0,
             "storre_an_registrerat_over_25_procent": 0,
             "avvikelse_over_5_procent": 0, "varsta_mindre": [], "varsta_storre": [],
             "regel": "geometrins nettoyta stäms av mot registrets AREA_HA"}

    for nvrid, rec in sorted(objekt.items()):
        geom = geometrier.get(nvrid)
        citat = citat_per_nvrid.get(nvrid) or []
        dokument = sortera_dokument(rec.get("dokument") or [])
        ocr = any(d.get("ocr") for d in dokument)
        sasong = [f for f in (rec.get("foreskriftsomraden") or [])
                  if f.get("franDatum") or f.get("tillDatum")]

        # Ett område vars beslutsdokument ännu inte hämtats och lästs får INTE
        # påstå att ingen luftfartsföreskrift finns — den utsagan är inte
        # förtjänad förrän texten är läst. Det är inte ett fjärde svarsläge utan
        # ett datatillstånd: sidan säger att beslutet inte lästs och länkar dit.
        lasta = [d for d in dokument if d.get("sidor") is not None]
        if citat:
            svarslage = "reglerat-las-beslutet"
            stats["med_verifierade_citat"] += 1
        elif dokument and not lasta:
            svarslage = "beslut-ej-last"
            stats["beslut_ej_last"] = stats.get("beslut_ej_last", 0) + 1
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
            "luftfartslage": luftfartslage(citat, dokument),
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
            ber, avv = arealavstamning(geom, rec.get("area_ha"))
            omrade["beraknad_area_ha"] = ber
            omrade["arealavvikelse_procent"] = avv
            if avv is not None:
                areal["provade"] += 1
                if abs(avv) > 5:
                    areal["avvikelse_over_5_procent"] += 1
                if abs(avv) > 25:
                    post = {"nvrid": nvrid, "namn": rec["namn"],
                            "area_ha_register": rec.get("area_ha"),
                            "beraknad_ha": ber, "avvikelse_procent": avv}
                    if avv < 0:
                        areal["mindre_an_registrerat_over_25_procent"] += 1
                        areal["varsta_mindre"].append(post)
                    else:
                        areal["storre_an_registrerat_over_25_procent"] += 1
                        areal["varsta_storre"].append(post)
            bb = bbox(geom)
            omrade["bbox"] = bb
            enkel, fstat, anvand_tol = forenkla_med_ytgrans(geom)
            forenkling["punkter_fore"] += fstat["punkter_fore"]
            forenkling["punkter_efter"] += fstat["punkter_efter"]
            forenkling["ringar_behallna"] += fstat["ringar_behallna"]
            nyckel = f"{anvand_tol:g} m" if anvand_tol else "oförenklad"
            forenkling["objekt_per_anvand_tolerans"][nyckel] = (
                forenkling["objekt_per_anvand_tolerans"].get(nyckel, 0) + 1)
            forenkling["total_yta_fore_m2"] += fstat["yta_fore_m2"]
            forenkling["total_yta_efter_m2"] += fstat["yta_efter_m2"]
            if fstat["yta_efter_m2"] > fstat["yta_fore_m2"] + 1e-6:
                forenkling["objekt_med_ytokning"] += 1
            # Relativ minskning mäts bara för objekt över 1 ha. Under det blir
            # procenttalet meningslöst: ett objekt vars hål nästan äter upp
            # ytterringen har en nettoyta nära noll, och då exploderar kvoten
            # utan att geometrin flyttat sig mer än någon meter.
            if fstat["yta_fore_m2"] >= 10_000:
                minskning = round(
                    (1 - fstat["yta_efter_m2"] / fstat["yta_fore_m2"]) * 100, 3)
                if minskning > forenkling["max_ytminskning_procent"]:
                    forenkling["max_ytminskning_procent"] = minskning
                    forenkling["max_ytminskning_procent_objekt"] = nvrid
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
                    "area_ha": rec.get("area_ha"),
                    "antal_citat": len(citat),
                    "luftfartslage": luftfartslage(citat, dokument),
                    "ocr": ocr,
                    "sasong": bool(sasong),
                },
            })
            bbox_index.append([nvrid, slugs[nvrid], rec["namn"], rec["skyddstyp"],
                               rec["lager"], svarslage] + bb +
                              [len(citat), luftfartslage(citat, dokument)])

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
                     "minx", "miny", "maxx", "maxy", "antal_citat",
                     "luftfartslage"],
        "rader": bbox_index,
    }, compact=True)

    rutnat = bygg_rutnat(features, bbox_index, geometrier)
    log(f"  rutnät: {rutnat['antal_visningsrutor']} visningsrutor, "
        f"{rutnat['antal_geometrirutor']} geometrirutor, "
        f"{rutnat['antal_stora_geometrifiler']} stora geometrier i egna filer")

    citatlager = bygg_citatlager(citat_per_nvrid, bbox_index)
    log(f"  citatlager: {citatlager['omraden_med_luftfartscitat']} områden med "
        f"luftfartscitat ({citatlager['luftfartsfil_byte'] // 1024} kB, bärs "
        f"offline), {citatlager['omraden_med_storningscitat']} med "
        f"störningsförbud i {citatlager['antal_citatrutor']} rutor")

    skriv_licens_och_schema(manifest, stats, forenkling)

    manifest["databygge"] = {
        "datum": today(),
        "forenkling": {**forenkling,
                       "regel": "Douglas–Peucker med krympgaranti: en ring vars "
                                "yta skulle växa av förenklingen behålls oförenklad"},
        "statistik": stats,
        "arealavstamning": areal,
        "rutnat": rutnat,
        "citatlager": citatlager,
    }
    write_json(os.path.join(DATA, "manifest.json"), manifest)
    areal["varsta_mindre"] = sorted(areal["varsta_mindre"],
                                    key=lambda x: x["avvikelse_procent"])[:25]
    areal["varsta_storre"] = sorted(areal["varsta_storre"],
                                    key=lambda x: -x["avvikelse_procent"])[:25]
    log(f"Steg 5 klart: {len(features)} ytor, {stats}")
    log(f"  arealavstämning mot AREA_HA: {areal['provade']} prövade, "
        f"{areal['mindre_an_registrerat_over_25_procent']} mindre än registrerat "
        f"(>25 %), {areal['storre_an_registrerat_over_25_procent']} större "
        f"(>25 %, registrets egen inkonsekvens)")
    log(f"  förenkling: {forenkling['punkter_fore']} → {forenkling['punkter_efter']} "
        f"punkter, max ytminskning {forenkling['max_ytminskning_procent']} %, "
        f"objekt med ytökning: {forenkling['objekt_med_ytokning']}")


if __name__ == "__main__":
    main()
