#!/usr/bin/env python3
"""Steg 1 — Ingest.

Hämtar samtliga skyddade områden inom vald omfattning (config.json:
lan_kort = null ger hela landet) ur Naturvårdsverkets
naturvårdsregister (WFS, CC0) samt detaljposter ur Kartverktyget Skyddad natur
(REST) med beslutsdokumentlänkar och föreskriftsområden (säsongsdata).

Rå-svar sparas oförändrade i cache/raw/ så att varje geometri och varje
dokumentlänk är spårbar till exakt det API-svar den kom ur (Regel R3).
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.common import (CACHE, CONFIG, DATA, ensure_dir, fetch, log, read_json,
                        sha256_bytes, today, write_json)
from lib.geom import esri_samling_till_geojson, ring_area_m2

WFS = "https://geodata.naturvardsverket.se/naturvardsregistret/wfs"
SKNAT = "https://skyddadnatur.naturvardsverket.se"
SKNAT_AREA_PAGE = SKNAT + "/sknat/?nvrid={nvrid}"

# Skyddstyper i scope enligt uppdraget. Nyckel = SKYDDSTYP-värdet i NVR,
# värde = (lager-id, i_kärnlagret).  Kärnlagret laddas alltid; extralager
# är default av i UI:t.
SKYDDSTYPER = {
    "Nationalpark": ("nationalpark", True),
    "Naturreservat": ("naturreservat", True),
    "Naturreservat (kommunalt beslutat)": ("naturreservat-kommunalt", True),
    "Naturvårdsområde": ("naturvardsomrade", True),
    "Djur- och växtskyddsområde": ("djur-och-vaxtskydd", True),
    "Kulturreservat": ("kulturreservat", True),
    "Interimistiskt förbud": ("interimistiskt-forbud", True),
    "Naturminne": ("naturminne", True),
    # Extralager: default av i UI:t enligt uppdraget.
    "Vattenskyddsområde": ("vattenskyddsomrade", False),
    "Landskapsbildsskyddsområde": ("landskapsbildsskydd", False),
    "Övrigt biotopskyddsområde": ("biotopskydd", False),
}

# Lager som saknar egen SKYDDSTYP i NVR men som härleds ur beslutsmyndighet.
HARLEDDA_LAGER = {"naturreservat-kommunalt": True}

FES = 'xmlns:fes="http://www.opengis.net/fes/2.0"'
# Serverns hårda tak är 500 objekt per svar, men taket är inte det enda
# problemet: ett svar med några hundra fjällreservat blir tiotals megabyte och
# kommer tillbaka avhugget mitt i JSON-strukturen. Vi håller därför våra egna
# celler under serverns tak, och delar dessutom upp varje cell som ändå kommer
# tillbaka avhuggen (se CellenAvhuggen). Trunkeringen är det som styr, inte en
# gissad storleksgräns.
TAK = 400

LAN_FILTER = (
    '<fes:PropertyIsLike wildCard="*" singleChar="?" escapeChar="\\">'
    "<fes:ValueReference>LAN</fes:ValueReference>"
    "<fes:Literal>*{lan}*</fes:Literal></fes:PropertyIsLike>"
)
TYP_FILTER = ("<fes:PropertyIsEqualTo><fes:ValueReference>SKYDDSTYP</fes:ValueReference>"
              "<fes:Literal>{typ}</fes:Literal></fes:PropertyIsEqualTo>")
# Datumliteralen måste vara ett rent datum. Med tidsstämpel ("1975-12-31T00:00:00Z")
# svarar tjänsten HTTP 500.
DATUM_FILTER = (
    "<fes:PropertyIsBetween><fes:ValueReference>URSPR_BESLUTSDATUM</fes:ValueReference>"
    "<fes:LowerBoundary><fes:Literal>{fran}-01-01</fes:Literal></fes:LowerBoundary>"
    "<fes:UpperBoundary><fes:Literal>{till}-12-31</fes:Literal></fes:UpperBoundary>"
    "</fes:PropertyIsBetween>")

# Länen används som andra partitionsnivå när en skyddstyp ensam spränger taket.
LAN_LISTA = [
    "Norrbotten", "Västerbotten", "Jämtland", "Västernorrland", "Gävleborg",
    "Dalarna", "Värmland", "Örebro", "Västmanland", "Uppsala", "Stockholm",
    "Södermanland", "Östergötland", "Jönköping", "Kronoberg", "Kalmar",
    "Gotland", "Blekinge", "Skåne", "Halland", "Västra Götaland",
]


def build_filter(predikat):
    """Sätt ihop ett FES-filter av noll eller flera predikat."""
    predikat = [p for p in predikat if p]
    if not predikat:
        return None
    if len(predikat) == 1:
        return f"<fes:Filter {FES}>{predikat[0]}</fes:Filter>"
    return f"<fes:Filter {FES}><fes:And>{''.join(predikat)}</fes:And></fes:Filter>"


def wfs_url(typename, filt, **extra):
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": f"Naturvardsregistret_WFS:{typename}",
        # ESRIGEOJSON, inte GEOJSON. Tjänstens GeoJSON-utskrift är trasig på två
        # sätt: den kapar svaret mitt i JSON-strukturen för celler med stora
        # geometrier, och den plattar ihop flerdelade områden till EN polygon där
        # de övriga delarna hamnar som "hål". Naturreservatet Verkeån blev då
        # 374 ha i stället för registrets egna 1 424 ha. ESRI-formatet levererar
        # hela svar och separata ringar; konverteringen sker i lib/geom.py.
        "outputFormat": "ESRIGEOJSON",
        "srsName": "urn:ogc:def:crs:EPSG::4326",
        "filter": filt,
    }
    params.update(extra)
    params = {k: v for k, v in params.items() if v is not None}
    return WFS + "?" + urllib.parse.urlencode(params)


def wfs_hits(typename, filt):
    # resultType=hits måste köras utan outputFormat=GEOJSON — annars svarar
    # tjänsten med en tom FeatureCollection i stället för antalet träffar.
    url = wfs_url(typename, filt, resultType="hits", outputFormat=None)
    body = fetch(url, min_interval=CONFIG["throttle_api_s"], timeout=180).decode("utf-8")
    m = re.search(r'numberMatched="(\d+)"', body)
    if not m:
        raise RuntimeError(f"kunde inte läsa numberMatched ur hits-svar: {body[:300]}")
    return int(m.group(1))


class CellenAvhuggen(Exception):
    """Svaret kom tillbaka trunkerat — cellen måste delas ytterligare."""


def hamta_direkt(typename, filt, etikett, forvantat):
    """Hämtar en cell och kräver komplett, parsbar GeoJSON med rätt antal."""
    raw = fetch(wfs_url(typename, filt, count=500),
                min_interval=CONFIG["throttle_docs_s"], timeout=900)
    try:
        doc = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise CellenAvhuggen(
            f"{etikett}: svaret avhugget efter {len(raw)} byte ({exc})") from exc
    got = esri_samling_till_geojson(doc)["features"]
    if len(got) != forvantat:
        raise CellenAvhuggen(
            f"{etikett}: hits sa {forvantat} men GetFeature gav {len(got)}")
    return got


def hamta_cell(typename, predikat, etikett, djup=0):
    """Hämtar en partition, och delar upp den vidare om den spränger taket.

    Tjänsten kapar hårt vid 500 objekt och ignorerar startIndex, så vanlig
    WFS-paginering går inte att använda. I stället partitioneras uttaget:

      nivå 1  SKYDDSTYP
      nivå 2  LÄN            — behövs t.ex. för naturreservat (6 027 i landet)
      nivå 3  BESLUTSDATUM   — halveras rekursivt tills cellen ryms

    Nivå 3 är en bisektion på datum i stället för en kommunlista, eftersom den
    alltid terminerar och inte kräver något externt register.

    Varje cell stäms av mot resultType=hits. Stämmer inte antalet avbryts
    bygget hellre än att en tyst lucka slinker igenom till kartan.
    """
    filt = build_filter(predikat)
    n = wfs_hits(typename, filt)
    if n == 0:
        return [], n
    if n <= TAK:
        try:
            return hamta_direkt(typename, filt, etikett, n), n
        except CellenAvhuggen as exc:
            log(f"    {exc} — delar cellen vidare")

    # Cellen är för stor — dela upp den.
    if djup == 0:
        log(f"    {etikett}: {n} objekt — över taket, delar per län")
        ut, summa = [], 0
        for lan in LAN_LISTA:
            f, k = hamta_cell(typename,
                              predikat + [LAN_FILTER.format(lan=lan)],
                              f"{etikett}/{lan}", djup + 1)
            ut.extend(f)
            summa += k
        if summa == n:
            return ut, n
        # Länsuppdelningen täcker inte allt. Det är inte ett datafel utan en
        # egenskap hos registret: 414 av de första 500 naturminnena har tomt
        # LAN-fält och fångas därför inte av något länsfilter. Fall tillbaka på
        # datumbisektion, som inte bygger på något attribut som kan vara tomt.
        log(f"    {etikett}: länsuppdelningen gav {summa} av {n} — "
            "faller tillbaka på datumuppdelning för hela mängden")
        return bisektera_datum(typename, predikat, etikett, n)

    # Nivå 3: bisektion på beslutsdatum.
    return bisektera_datum(typename, predikat, etikett, n)


def bisektera_datum(typename, predikat, etikett, forvantat, fran=1850, till=2100):
    """Halvera beslutsdatumintervallet tills varje del ryms under taket.

    Returnerar (features, antal). Summan stäms av mot `forvantat` av anroparen:
    skulle objekt sakna beslutsdatum, och därmed falla utanför varje intervall,
    upptäcks det som en avvikelse i stället för att bli en tyst lucka.
    """
    if till - fran < 1:
        # Alla objekt i cellen delar samma beslutsdatum — vanligt när ett enda
        # beslut bildat många små områden, t.ex. 124 fågelskyddsområden i
        # Blekinge från 1985. Datum kan inte dela dem, men serverns tak är 500
        # och en sådan cell är oftast liten i byte. Pröva ett direktuttag.
        filt = build_filter(predikat)
        try:
            return hamta_direkt(typename, filt, etikett, forvantat), forvantat
        except CellenAvhuggen as exc:
            raise RuntimeError(
                f"{etikett}: {forvantat} objekt delar beslutsdatum {fran} och "
                f"går varken att dela eller hämta i ett stycke ({exc})") from exc
    mitt = (fran + till) // 2
    ut = []
    for lo, hi in ((fran, mitt), (mitt + 1, till)):
        pred = predikat + [DATUM_FILTER.format(fran=lo, till=hi)]
        filt = build_filter(pred)
        n = wfs_hits(typename, filt)
        if n == 0:
            continue
        hamtad = None
        if n <= TAK:
            try:
                hamtad = hamta_direkt(typename, filt, f"{etikett} {lo}–{hi}", n)
            except CellenAvhuggen as exc:
                log(f"    {exc} — delar vidare")
        if hamtad is not None:
            ut.extend(hamtad)
        else:
            djupare, _ = bisektera_datum(typename, pred, f"{etikett} {lo}–{hi}",
                                         n, lo, hi)
            ut.extend(djupare)
    if len(ut) != forvantat:
        raise RuntimeError(
            f"{etikett}: datumuppdelningen gav {len(ut)} av {forvantat} objekt — "
            "sannolikt objekt utan URSPR_BESLUTSDATUM")
    return ut, len(ut)


def fetch_layer(typename, out_name):
    """Hämtar ett WFS-lager, partitionerat och avstämt mot resultType=hits."""
    omfattning = CONFIG.get("lan_kort") or None
    lanpred = [LAN_FILTER.format(lan=omfattning)] if omfattning else []
    total = wfs_hits(typename, build_filter(lanpred))
    var = omfattning or "hela landet"
    log(f"  {typename}: {total} objekt i {var} enligt resultType=hits")
    features, per_typ = [], {}
    for skyddstyp in SKYDDSTYPER:
        got, n = hamta_cell(typename,
                            lanpred + [TYP_FILTER.format(typ=skyddstyp)],
                            f"{typename}/{skyddstyp}")
        per_typ[skyddstyp] = n
        if n:
            features.extend(got)
            log(f"    {skyddstyp}: {len(got)}")

    utanfor = total - sum(per_typ.values())
    if utanfor:
        log(f"  OBS: {utanfor} objekt har en SKYDDSTYP utanför scope-listan")
    payload = {"type": "FeatureCollection", "features": features}
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path = os.path.join(CACHE, "raw", out_name)
    ensure_dir(os.path.dirname(path))
    with open(path, "wb") as fh:
        fh.write(body)
    return payload, sha256_bytes(body), {
        "antal_i_lanet_enligt_hits": total,
        "antal_hamtade": len(features),
        "per_skyddstyp": per_typ,
        "antal_utanfor_scope": utanfor,
    }


def fetch_detail(nvrid, beslutsstatus):
    """Detaljpost ur Kartverktyget Skyddad natur. Cachas per NVRID."""
    path = os.path.join(CACHE, "raw", "detail", f"{nvrid}.json")
    cached = read_json(path)
    if cached is not None:
        return cached
    ident = urllib.parse.quote(f"{nvrid}#{beslutsstatus}@NVR", safe="")
    try:
        raw = fetch(f"{SKNAT}/rest/detail/{ident}", timeout=120)
        doc = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        log(f"  ! detail misslyckades för {nvrid}: {exc}")
        doc = {"_error": str(exc)}
    write_json(path, doc)
    return doc


def normalise_docs(detail):
    """Plocka ut unika beslutsdokument. Skötselplaner och visningsbilder
    utesluts inte här — klassificeringen görs i steg 3 utifrån dokumentnamn."""
    seen, out = set(), []
    for beslut in detail.get("beslut") or []:
        for dk in beslut.get("beslutsDokument") or []:
            url = dk.get("fileUrl")
            if not url:
                continue
            url = url.replace(":443/", "/")
            if url in seen:
                continue
            seen.add(url)
            out.append({
                "url": url,
                "namn": dk.get("namn"),
                "typ": dk.get("typ"),
                "mime": dk.get("mimeType"),
                "beslutsstatus": dk.get("beslutsstatus"),
                "beslutstyp": dk.get("beslutstyp"),
                "beslutsid": dk.get("beslutsId"),
            })
    return out


def main():
    hamtningsdatum = today()
    log(f"Steg 1 — ingest för {CONFIG['lan']} ({hamtningsdatum})")

    layers = {}
    for typename, out_name in (("SkyddadeOmraden", "nvr_omraden_skane.geojson"),
                               ("SkyddadePunkter", "nvr_punkter_skane.geojson")):
        log(f"WFS {typename} …")
        payload, digest, avstamning = fetch_layer(typename, out_name)
        layers[typename] = {
            "fil": f"cache/raw/{out_name}",
            "antal": len(payload["features"]),
            "svarshash_sha256": digest,
            "hamtningsdatum": hamtningsdatum,
            "avstamning": avstamning,
        }
        log(f"  {typename}: {len(payload['features'])} objekt, sha256 {digest[:12]}…")

    omraden = read_json(os.path.join(CACHE, "raw", "nvr_omraden_skane.geojson"))
    punkter = read_json(os.path.join(CACHE, "raw", "nvr_punkter_skane.geojson"))

    objekt, ur_scope = {}, {}
    for src, feats in (("polygon", omraden["features"]), ("punkt", punkter["features"])):
        for f in feats:
            p = f["properties"]
            styp = (p.get("SKYDDSTYP") or "").strip()
            nvrid = str(p.get("NVRID") or "").strip()
            if not nvrid:
                continue
            if styp not in SKYDDSTYPER:
                ur_scope[styp] = ur_scope.get(styp, 0) + 1
                continue
            lager, karnlager = SKYDDSTYPER[styp]
            myndighet = (p.get("BESLUTSMYNDIGHET") or "").strip()
            # NVR särskiljer inte kommunala naturreservat med egen SKYDDSTYP i
            # Skåne — de skiljs ut på beslutsmyndighet i stället.
            if styp == "Naturreservat" and myndighet.lower().startswith("kommun"):
                lager = "naturreservat-kommunalt"
            rec = objekt.setdefault(nvrid, {
                "nvrid": nvrid,
                "namn": (p.get("NAMN") or "").strip(),
                "skyddstyp": styp,
                "lager": lager,
                "karnlager": karnlager,
                "beslutsstatus": (p.get("BESLUTSSTATUS") or "").strip(),
                "beslutsmyndighet": (p.get("BESLUTSMYNDIGHET") or "").strip() or None,
                "forvaltare": (p.get("FORVALTARE") or "").strip() or None,
                "tillsynsmyndighet": (p.get("TILLSYNSMYNDIGHET") or "").strip() or None,
                "lan": (p.get("LAN") or "").strip(),
                "kommun": (p.get("KOMMUN") or "").strip(),
                "urspr_beslutsdatum": p.get("URSPR_BESLUTSDATUM"),
                "senaste_gallandedatum": p.get("SENASTE_GALLANDEDATUM"),
                "ikrafttradande_foreskrift": p.get("IKRAFTTRADANDEDATUM_FORESKRIFT"),
                "area_ha": p.get("AREA_HA"),
                "geometri_kalla": {
                    "tjanst": "Naturvårdsverket NVR WFS 2.0",
                    "url": WFS,
                    "typeName": "Naturvardsregistret_WFS:SkyddadeOmraden"
                    if src == "polygon" else "Naturvardsregistret_WFS:SkyddadePunkter",
                    "svarshash_sha256": layers["SkyddadeOmraden" if src == "polygon"
                                               else "SkyddadePunkter"]["svarshash_sha256"],
                    "hamtningsdatum": hamtningsdatum,
                    "licens": "CC0",
                },
                "geometrityp": src,
                "sknat_url": SKNAT_AREA_PAGE.format(nvrid=nvrid),
                "dokument": [],
                "foreskriftsomraden": [],
            })
            rec.setdefault("_gmlids", []).append(p.get("GmlID"))

    log(f"{len(objekt)} objekt i scope; utanför scope: {ur_scope}")

    log("Hämtar detaljposter (beslutsdokument + föreskriftsområden) …")
    for i, (nvrid, rec) in enumerate(sorted(objekt.items()), 1):
        detail = fetch_detail(nvrid, rec["beslutsstatus"] or "Gällande")
        rec["dokument"] = normalise_docs(detail)
        rec["foreskriftsomraden"] = detail.get("foreskriftsOmraden") or []
        rec["beslutstyp"] = detail.get("beslutstyp")
        rec["lagrum"] = detail.get("lagrum")
        rec["diarienummer"] = detail.get("diarienummer")
        rec["detalj_kalla"] = {
            "tjanst": "Kartverktyget Skyddad natur (REST)",
            "url": f"{SKNAT}/rest/detail/{nvrid}",
            "hamtningsdatum": hamtningsdatum,
        }
        if i % 50 == 0:
            log(f"  {i}/{len(objekt)} …")

    ndok = sum(len(r["dokument"]) for r in objekt.values())
    utan_dok = sum(1 for r in objekt.values() if not r["dokument"])
    log(f"Klart: {ndok} dokumentlänkar, {utan_dok} objekt utan digitalt dokument")

    manifest = {
        "schema_version": 1,
        "lan": CONFIG["lan"],
        "hamtningsdatum": hamtningsdatum,
        "kallor": {
            "nvr_wfs": {
                "namn": "Naturvårdsverkets naturvårdsregister (NVR)",
                "url": WFS,
                "licens": "CC0",
                "lager": layers,
            },
            "skyddad_natur_rest": {
                "namn": "Kartverktyget Skyddad natur",
                "url": SKNAT + "/rest/detail/{nvrid}",
                "licens": "CC0",
                "hamtningsdatum": hamtningsdatum,
            },
        },
        "skyddstyper_i_scope": {k: v[0] for k, v in SKYDDSTYPER.items()},
        "skyddstyper_utanfor_scope_i_lansdata": ur_scope,
        "antal_objekt": len(objekt),
        "antal_dokumentlankar": ndok,
        "objekt": objekt,
    }
    write_json(os.path.join(DATA, "manifest.json"), manifest)
    log(f"Skrev data/manifest.json ({len(objekt)} objekt)")


if __name__ == "__main__":
    main()
