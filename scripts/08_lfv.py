#!/usr/bin/env python3
"""Steg 8 — LFV:s luftrum som vektordata.

Varför vektor och inte den ostylade WMS-rutan vi började med
------------------------------------------------------------
Första versionen visade LFV:s drönarkarta som ett rasterlager man kunde tända
och släcka, utan att tjänsten själv visste vad som fanns i det. Det var
juridiskt försiktigt och praktiskt värdelöst: appen kunde inte svara på om du
stod i en kontrollzon, bara rita en bild du fick tolka själv.

Hela Sveriges luftrum är 259 polygoner. Det ryms i en fil som telefonen kan
bära offline, och som gör att positionssvaret kan avges utan nät — vilket är
själva förutsättningen för att appen ska hinna varna innan du lyfter.

Licens
------
LFV:s data är CC BY-NC-ND 4.0. Två konsekvenser som är inbyggda i koden:

  * Attributionen följer med i datafilen och renderas i gränssnittet.
  * LFV-lagret är UNDANTAGET från tjänstens CC0-deklaration. Vi vidarelicensierar
    inte någon annans ND-data. Se `licens`-fältet i utdatafilen och /kallor/.

ND (inga bearbetningar) är den kläm som gör det här till ett medvetet
ställningstagande snarare än en självklarhet: vi konverterar formatet och
stylar om. Beslutet är fattat för en pre-beta med en enda användare och utan
kommersiellt syfte, och det är avstängbart med `lfv_vektor: false` i
config.json — då faller gränssnittet tillbaka på WMS-lagret. Vill man göra
tjänsten publik är det här den första frågan att ta om.

Höjdnotation
------------
LFV publicerar undre och övre gräns som i AIP: `GND` för marken, `FL nnn` för
flygnivå, i övrigt fot. Källvärdet skrivs alltid vidare ORDAGRANT.

Första versionen räknade medvetet INTE om till meter, med motiveringen att en
avrundning i fel riktning är en tyst felkälla. Det var överdrivet försiktigt.
1 ft = 0,3048 m är exakt aritmetik, inte en bedömning, och ett oomräknat "400"
är oanvändbart för den vars drönare visar meter. Källvärdet står först, metern
inom parentes. Avrundning sker UPPÅT i bägge ändar — då överdrivs zonens
utsträckning marginellt, vilket är den säkra riktningen.

Vad de numeriska värdena är höjd ÖVER är avgörande och belagt ur LFV:s egen
text: kommentarsfälten skriver ut "AMSL" 34 gånger ("The lower limit of
Västerås CTR in this part is 1000 ft AMSL"), "AGL" 16 gånger och "GND" 14.
Numeriska gränser är alltså höjd över HAVET, medan en drönares 120-metersgräns
i öppen kategori är höjd över MARKEN.

Det betyder att en zon med underkant 400 ft inte utan vidare kan jämföras med
120 m: ligger terrängen 50 m över havet är 400 ft AMSL bara ~72 m över marken.
Tjänsten redovisar därför bägge talen och säger att jämförelsen kräver
terränghöjden — den drar inte slutsatsen åt någon.

`nar_marken` (LOWER == "GND") är den enda helt entydiga flaggan: når zonen
marken berör den varje höjd, oavsett terräng.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.common import (CONFIG, DATA, ensure_dir, log, sha256_bytes, fetch,
                        today, write_json)

WFS = "https://daim.lfv.se/geoserver/wfs"

# Lagren dronechart.lfv.se visar för drönarflygning. Beskrivningarna säger vad
# lagret ÄR (AIP-terminologi), inte vad som gäller där — regeltexten hämtas
# ordagrant på /regler/ och zonträffen länkar dit.
LAGER = [
    ("CTR", "Kontrollzon (CTR)",
     "Kontrollerat luftrum kring en flygplats, från marken och upp."),
    ("TIZ", "Trafikinformationszon (TIZ)",
     "Zon med trafikinformationstjänst kring en flygplats, från marken och upp."),
    ("ATZ", "Flygplatstrafikzon (ATZ)",
     "Trafikzon kring en flygplats, från marken och upp."),
    ("RSTA", "Restriktionsområde (R-område)",
     "Område där luftrummet är begränsat, ofta kring skyddsobjekt."),
    ("DNGA", "Fararområde (D-område)",
     "Område med verksamhet som kan vara farlig för luftfart."),
    ("TIA", "Trafikinformationsområde (TIA)",
     "Trafikinformationstjänst ovanför en TIZ. Underkanten ligger högt."),
]


def wfs_url(typnamn, **extra):
    p = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": f"mais:{typnamn}",
        "srsName": "EPSG:4326",
        "outputFormat": "application/json",
    }
    p.update({k: v for k, v in extra.items() if v is not None})
    return f"{WFS}?{urllib.parse.urlencode(p)}"


def antal_enligt_servern(typnamn):
    """Avstämning: fråga servern hur många den anser sig ha.

    Samma disciplin som mot Naturvårdsregistret. Där visade den att servern
    tyst kapade svaret vid 500 poster; här är volymerna små, men en tyst
    kapning är inte mindre farlig för att den är osannolik.
    """
    url = wfs_url(typnamn, resultType="hits", outputFormat=None)
    xml = fetch(url, min_interval=CONFIG["throttle_api_s"]).decode("utf-8", "replace")
    for nyckel in ("numberMatched=", "numberOfFeatures="):
        if nyckel in xml:
            bit = xml.split(nyckel, 1)[1][1:]
            tal = bit.split('"', 1)[0]
            if tal.isdigit():
                return int(tal)
    return None


def stad(varde):
    if varde is None:
        return ""
    s = str(varde).strip()
    return "" if s.lower() in ("", "null", "none") else s


FOT_TILL_METER = 0.3048          # exakt per definition


def hojd(varde):
    """Tolka en AIP-höjd till (meter, referens, ordagrant källvärde).

    Returnerar meter = None när värdet inte går att tolka. Då skriver
    gränssnittet bara ut källvärdet — hellre inget tal än ett påhittat.

    Avrundning uppåt: en zon som redovisas något högre än den är gör att man
    tror sig vara inne i den lite längre. Det är den säkra riktningen.
    """
    s = stad(varde)
    if not s:
        return None, "okänd", s
    if s.upper() == "GND":
        return 0, "GND", s
    m = re.match(r"^FL\s*(\d+)$", s, re.I)
    if m:
        # Flygnivå: hundratals fot, tryckhöjd. Långt ovanför drönarhöjd, men
        # räknas om ändå så att talet går att förstå.
        return math.ceil(int(m.group(1)) * 100 * FOT_TILL_METER), "FL", s
    if re.fullmatch(r"\d+", s):
        return math.ceil(int(s) * FOT_TILL_METER), "AMSL", s
    return None, "okänd", s


def kommentar(props):
    """COMMENT_1 och COMMENT_2 sammanfogade, ordagrant.

    COMMENT_1 är oftast en luftrumsklassbokstav ("C", "G"), COMMENT_2 den
    förklarande texten på svenska ("Kärnkraftverk. Särskilda tillstånd från
    Transportstyrelsen krävs."). Bägge behålls som de står.
    """
    delar = [stad(props.get("COMMENT_1")), stad(props.get("COMMENT_2"))]
    return " — ".join(d for d in delar if d)


def hamta_lager(typnamn):
    vantat = antal_enligt_servern(typnamn)
    ra = fetch(wfs_url(typnamn), min_interval=CONFIG["throttle_api_s"])
    d = json.loads(ra.decode("utf-8"))
    features = d.get("features", [])
    if vantat is not None and len(features) != vantat:
        raise RuntimeError(
            f"{typnamn}: servern uppger {vantat} objekt men levererade "
            f"{len(features)}. Avbryter hellre än att visa ett ofullständigt "
            f"luftrum.")
    return features, sha256_bytes(ra), vantat


def bbox_for(geom):
    xs, ys = [], []

    def gaa(c):
        if isinstance(c[0], (int, float)):
            xs.append(c[0])
            ys.append(c[1])
        else:
            for d in c:
                gaa(d)

    gaa(geom["coordinates"])
    return [round(min(xs), 6), round(min(ys), 6), round(max(xs), 6), round(max(ys), 6)]


def avrunda(geom, dec=5):
    """~1 m upplösning. Ingen förenkling av formen — bara färre decimaler."""
    def g(c):
        if isinstance(c[0], (int, float)):
            return [round(c[0], dec), round(c[1], dec)]
        return [g(d) for d in c]

    return {"type": geom["type"], "coordinates": g(geom["coordinates"])}


def main():
    if not CONFIG.get("lfv_vektor", True):
        log("lfv_vektor = false i config.json — hoppar över LFV-hämtningen.")
        return 0

    zoner = []
    lagermeta = []
    for kod, rubrik, beskrivning in LAGER:
        features, hashv, vantat = hamta_lager(kod)
        nar_marken = 0
        for f in features:
            g = f.get("geometry")
            if not g or g["type"] not in ("Polygon", "MultiPolygon"):
                continue
            p = f["properties"]
            lower = stad(p.get("LOWER"))
            marken = lower.upper() == "GND"
            nar_marken += 1 if marken else 0
            und_m, und_ref, und_kalla = hojd(p.get("LOWER"))
            ovr_m, ovr_ref, ovr_kalla = hojd(p.get("UPPER"))
            zoner.append({
                "id": f"{kod}-{stad(p.get('IDNR')) or len(zoner)}",
                "lager": kod,
                "namn": stad(p.get("NAMEOFAREA")) or stad(p.get("LOCATION")),
                "plats": stad(p.get("LOCATION")),
                "icao": stad(p.get("POSITIONINDICATOR")),
                "underkant": und_kalla,
                "overkant": ovr_kalla,
                "underkant_m": und_m,
                "overkant_m": ovr_m,
                "hojdreferens": und_ref,
                "nar_marken": marken,
                "kommentar": kommentar(p),
                "galler_fran": stad(p.get("WEF")),
                "bbox": bbox_for(g),
                "geometri": avrunda(g),
            })
        lagermeta.append({
            "kod": kod,
            "rubrik": rubrik,
            "beskrivning": beskrivning,
            "antal": sum(1 for z in zoner if z["lager"] == kod),
            "antal_nar_marken": nar_marken,
            "svarshash": hashv,
            "serverns_antal": vantat,
        })
        log(f"  {kod}: {lagermeta[-1]['antal']} zoner "
            f"({nar_marken} når marken), hash {hashv[:12]}")

    ut = {
        "kalla": "LFV DAIM WFS",
        "kalla_url": WFS,
        "kalla_karta": CONFIG["lfv_wms"]["dronechart"],
        "hamtad": today(),
        "attribution": CONFIG["lfv_wms"]["attribution"],
        "licens": "CC BY-NC-ND 4.0",
        "licensnot": ("LFV:s data ingår INTE i tjänstens CC0-publicering. "
                      "Vidarelicensiera inte det här lagret."),
        "hojdnotation": ("Undre och övre gräns skrivs som i LFV:s AIP: GND = marken, "
                         "FL nnn = flygnivå, övriga tal i fot. Källvärdet står "
                         "ordagrant i `underkant`/`overkant`; `underkant_m`/`overkant_m` "
                         "är samma värde i meter (1 ft = 0,3048 m exakt, avrundat "
                         "uppåt). `hojdreferens` säger vad talet är höjd över: GND = "
                         "marken, AMSL = havet, FL = tryckhöjd. En numerisk gräns är "
                         "AMSL — den kan därför inte jämföras rakt av med en drönares "
                         "120 m över MARKEN utan att terrängens höjd vägs in."),
        "lager": lagermeta,
        "zoner": zoner,
    }
    sokvag = os.path.join(ensure_dir(DATA), "lfv.json")
    write_json(sokvag, ut, compact=True)
    storlek = os.path.getsize(sokvag)
    log(f"Steg 8 klart: {len(zoner)} zoner, {storlek // 1024} kB "
        f"({sum(1 for z in zoner if z['nar_marken'])} når marken)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
