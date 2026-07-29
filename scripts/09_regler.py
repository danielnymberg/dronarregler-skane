#!/usr/bin/env python3
"""Steg 9 — de regler som gäller överallt, ordagrant ur författningarna.

Varför sidan finns
------------------
Tjänsten svarade tidigare bara på vad som gäller på en viss PLATS. Men de flesta
reglerna som fäller en drönarpilot är inte platsbundna: höjdgränsen, kravet på
att se farkosten, klarering i kontrollzon, tillståndet för R-område. De står i
en EU-förordning och i en TSFS, och de är inte lätta att hitta i.

Vad som INTE görs här
---------------------
Ingen förenkling av regeltexten. R1 gäller: allt som visas som regel är ett
ordagrant citat med författning, paragraf och länk till originalet. Det som
görs för läsbarhetens skull är att citaten ordnas under FRÅGOR på vanlig
svenska — "Hur högt får jag flyga?" — och en fråga är en rubrik som hjälper dig
hitta, inte en sammanfattning av svaret. Rubriken påstår ingenting; den pekar.

Verifiering
-----------
Samma grind som för naturbesluten (R7). Varje utdrag matchas ordagrant mot den
normaliserade källtexten efter extraktionen. Går det inte att belägga skrivs
det inte ut — bygget avbryts hellre än att en regeltext publiceras som ingen
kan spåra tillbaka till källan.

Normalisering, dokumenterad
---------------------------
PDF-text bryter rader mitt i ord och strör in sidhuvuden. Före matchning:
avstavning över radbrytning fogas ihop, sidnummer och löpande författningsnamn
tas bort, all vitrymd blir enkla mellanslag. HTML avkodas och taggas av. Inget
annat rörs — ordföljd och ordval lämnas som de står.
"""
from __future__ import annotations

import html
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.common import (CACHE, DATA, ensure_dir, fetch, log, sha256_bytes,
                        today, write_json)

# --------------------------------------------------------------- källorna --
KALLOR = {
    "eu947": {
        "titel": "Kommissionens genomförandeförordning (EU) 2019/947",
        "kortnamn": "EU 2019/947",
        "beskrivning": "om regler och förfaranden för drift av obemannade luftfartyg",
        "myndighet": "Europeiska kommissionen",
        # EUR-Lex webbgränssnitt svarar 202 på maskinella anrop. Publikations-
        # byråns cellar-API levererar samma officiella text utan omväg.
        "url": ("https://publications.europa.eu/resource/cellar/"
                "819291ca-8c1c-11e9-9369-01aa75ed71a1"),
        "accept": "application/xhtml+xml",
        "sprak": "swe",
        "lasbar_url": ("https://eur-lex.europa.eu/legal-content/SV/TXT/"
                       "?uri=CELEX:32019R0947"),
        "format": "html",
    },
    "tsfs2017110": {
        "titel": "Transportstyrelsens föreskrifter om obemannade luftfartyg",
        "kortnamn": "TSFS 2017:110",
        "beskrivning": "de svenska tilläggsreglerna, bl.a. om luftrum",
        "myndighet": "Transportstyrelsen",
        "url": "https://www.transportstyrelsen.se/TSFS/TSFS%202017_110.pdf",
        "lasbar_url": "https://www.transportstyrelsen.se/TSFS/TSFS%202017_110.pdf",
        "format": "pdf",
    },
    "skyddslagen": {
        "titel": "Skyddslag (2010:305)",
        "kortnamn": "Skyddslagen",
        "beskrivning": "skyddsobjekt, tillträdes- och avbildningsförbud",
        "myndighet": "Sveriges riksdag",
        "url": "https://rkrattsbaser.gov.se/sfst?bet=2010:305",
        "lasbar_url": "https://rkrattsbaser.gov.se/sfst?bet=2010:305",
        "format": "html",
    },
    "miljobalken": {
        "titel": "Miljöbalk (1998:808)",
        "kortnamn": "Miljöbalken",
        "beskrivning": "bl.a. tillståndsplikt i Natura 2000-områden",
        "myndighet": "Sveriges riksdag",
        "url": "https://rkrattsbaser.gov.se/sfst?bet=1998:808",
        "lasbar_url": "https://rkrattsbaser.gov.se/sfst?bet=1998:808",
        "format": "html",
    },
    "geoinfolagen": {
        "titel": "Lag (2016:319) om skydd för geografisk information",
        "kortnamn": "Lag 2016:319",
        "beskrivning": "spridningstillstånd för bilder tagna från luften",
        "myndighet": "Sveriges riksdag",
        "url": "https://rkrattsbaser.gov.se/sfst?bet=2016:319",
        "lasbar_url": "https://rkrattsbaser.gov.se/sfst?bet=2016:319",
        "format": "html",
    },
    "luftfartslagen": {
        "titel": "Luftfartslag (2010:500)",
        "kortnamn": "Luftfartslagen",
        "beskrivning": "grundläggande krav på luftfart över svenskt område",
        "myndighet": "Sveriges riksdag",
        "url": "https://rkrattsbaser.gov.se/sfst?bet=2010:500",
        "lasbar_url": "https://rkrattsbaser.gov.se/sfst?bet=2010:500",
        "format": "html",
    },
}

# ------------------------------------------------------------- passagerna --
# `fran` och `till` är ordagranna markörer i den normaliserade källtexten.
# Utdraget är allt från och med `fran` till (men inte med) `till`.
# `fraga` och `grupp` är statisk gränssnittstext — de sammanfattar inte svaret.
AVSNITT = [
    {"grupp": "Grunderna", "id": "oppen-kategori",
     "fraga": "Vad krävs för att flygningen ska räknas som ”öppen kategori”?",
     "kalla": "eu947", "referens": "Artikel 4.1",
     "fran": "Drift av UAS ska klassificeras som drift i den ”öppna” kategorin enbart om",
     "till": "2. Drift av UAS i den ”öppna” kategorin ska delas upp"},

    {"grupp": "Grunderna", "id": "hojd",
     "fraga": "Hur högt får jag flyga?",
     "kalla": "eu947", "referens": "Bilagan, del A, UAS.OPEN.010 punkt 2–4",
     "fran": "Om driften av UAS inbegriper att ett obemannat luftfartyg startar",
     "till": "UAS.OPEN.020 Drift av UAS i underkategori A1"},

    # Avstånds- och underkategorireglerna är de som faktiskt biter i tätbebyggt
    # område, och de saknades i första versionen. Sidan citerade artikel 4 och
    # höjdgränsen men inte A1/A2/A3 — alltså inte den regel som avgör om man
    # över huvud taget får flyga i en hamn eller ett villaområde. Att den luckan
    # fanns märktes först när frågan ställdes rakt ut.
    {"grupp": "Avstånd till människor och bebyggelse", "id": "a1",
     "fraga": "Underkategori A1 — vad gäller närmast människor?",
     "kalla": "eu947", "referens": "Bilagan, del A, UAS.OPEN.020 punkt 1–3",
     "fran": "UAS.OPEN.020 Drift av UAS i underkategori A1 Drift av UAS i "
             "underkategori A1 ska uppfylla samtliga följande villkor:",
     "till": "4. Driften ska utföras av en fjärrpilot som"},

    {"grupp": "Avstånd till människor och bebyggelse", "id": "a2",
     "fraga": "Underkategori A2 — hur nära människor får jag flyga?",
     "kalla": "eu947", "referens": "Bilagan, del A, UAS.OPEN.030 punkt 1",
     "fran": "UAS.OPEN.030 Drift av UAS i underkategori A2 Drift av UAS i "
             "underkategori A2 ska uppfylla samtliga följande villkor:",
     "till": "2. Den ska utföras av en fjärrpilot som har satt sig in"},

    {"grupp": "Avstånd till människor och bebyggelse", "id": "a3",
     "fraga": "Underkategori A3 — får jag flyga över villaområden och hamnar?",
     "kalla": "eu947", "referens": "Bilagan, del A, UAS.OPEN.040 punkt 1–2",
     "fran": "UAS.OPEN.040 Drift av UAS i underkategori A3 Drift av UAS i "
             "underkategori A3 ska uppfylla samtliga följande villkor:",
     "till": "3. Den ska utföras av en fjärrpilot som har fullbordat"},

    {"grupp": "Avstånd till människor och bebyggelse", "id": "aldre-dronare",
     "fraga": "Min drönare saknar klassmärkning — vilken underkategori gäller då?",
     "kalla": "eu947", "referens": "Artikel 20",
     "fran": "UAS-typer i den betydelse som avses i Europaparlamentets och rådets "
             "beslut nr 768/2008/EG",
     "till": "Artikel 21"},

    {"grupp": "Luftrum", "id": "luftrum",
     "fraga": "Vad gäller i en kontrollzon (CTR)?",
     "kalla": "tsfs2017110", "referens": "3 §",
     "fran": "3 § Flygning i kontrollerat luftrum får bara ske",
     "till": "4 § För flygning i trafikinformationszon"},

    {"grupp": "Luftrum", "id": "tiz",
     "fraga": "Vad gäller i trafikinformationszon (TIZ) och trafikinformationsområde (TIA)?",
     "kalla": "tsfs2017110", "referens": "4 §",
     "fran": "4 § För flygning i trafikinformationszon",
     "till": "Tilläggsbestämmelser"},

    {"grupp": "Luftrum", "id": "rd-omrade",
     "fraga": "Vad gäller i restriktionsområde (R) och farligt område (D)?",
     "kalla": "tsfs2017110", "referens": "10 §",
     "fran": "10 § Flygning i restriktionsområde (R-område)",
     "till": "11 § Flygning i trafikzon"},

    {"grupp": "Luftrum", "id": "atz",
     "fraga": "Vad gäller i trafikzon (ATZ)?",
     "kalla": "tsfs2017110", "referens": "11 §",
     "fran": "11 § Flygning i trafikzon (ATZ)",
     "till": "12 § Konstruktion, tillverkning"},

    {"grupp": "Zoner och skyddsobjekt", "id": "geozoner",
     "fraga": "Vem bestämmer var det finns drönarzoner, och vad får de bestämma?",
     "kalla": "eu947", "referens": "Artikel 15",
     "fran": "1. När medlemsstaterna för ändamål som avser säkerhet",
     "till": "Artikel 16"},

    {"grupp": "Zoner och skyddsobjekt", "id": "natura2000",
     "fraga": "Vad gäller i ett Natura 2000-område?",
     "kalla": "miljobalken", "referens": "7 kap 28 a §",
     "fran": "28 a § Tillstånd krävs för att bedriva verksamheter",
     "till": "28 b § Tillstånd enligt 28 a §"},

    {"grupp": "Efter flygningen", "id": "spridning",
     "fraga": "Får jag publicera bilderna?",
     "kalla": "geoinfolagen", "referens": "9 §",
     "fran": "9 § Om inte något annat följer av 10 eller 11 §, är det förbjudet "
             "att sprida en sammanställning av geografisk information",
     "till": "10 §"},

    {"grupp": "Zoner och skyddsobjekt", "id": "skyddsobjekt",
     "fraga": "Vad gäller vid ett skyddsobjekt?",
     "kalla": "skyddslagen", "referens": "7 §",
     "fran": "7 § Ett beslut om skyddsobjekt innebär att obehöriga",
     "till": "8 § Om ett förbud som avses i 7 §"},
]

# Fraser som MÅSTE överleva hela vägen ut. Samma sorts spärr som golden-testen
# för naturbesluten: ändras extraktionen så att en bärande formulering faller
# bort ska bygget säga ifrån, inte tyst leverera en tunnare text.
MASTE_FINNAS = {
    "hojd": ["120 meter från den närmsta punkten på jordytan"],
    "luftrum": ["klarering", "50 meter över marken", "5 km"],
    # "D-område" står här för att avstavningsregeln en gång åt bindestrecket och
    # gjorde citatet oordagrant utan att något larmade.
    "rd-omrade": ["bara ske efter tillstånd", "(R-område)", "(D-område)"],
    "tiz": ["dubbelriktad radioförbindelse"],
    "atz": ["samråd med berörd flygplats"],
    "geozoner": ["geografiska UAS-zoner"],
    "skyddsobjekt": ["tillträde med hjälp av en obemannad farkost"],
    "oppen-kategori": ["inom synhåll", "120 meter"],
    # 150-metersregeln är den enskilt viktigaste meningen på hela sidan för den
    # som står i en hamn eller ett bostadsområde. Faller den bort ska bygget
    # avbrytas, inte leverera en tunnare text.
    "a3": ["minst 150 meter från bostads-, affärs-, industri- eller "
           "rekreationsområden"],
    "a2": ["minst 30 meter"],
    "a1": ["folksamlingar"],
    "aldre-dronare": ["under 250 gram", "underkategori A3"],
    "natura2000": ["på ett betydande sätt kan påverka miljön"],
    "spridning": ["förbjudet att sprida"],
}


# Inventeringsraden i appen: kort kategorinamn (statisk gränssnittstext som
# säger VAD raden handlar om, inte vad regeln säger) och vilken underkategori
# avsnittet gäller. `nyckelfras` tas ur MASTE_FINNAS, alltså en formulering som
# redan är verifierad ordagrann — inventeringsraden citerar därför källan i
# stället för att sammanfatta den.
KATEGORI = {
    "oppen-kategori": ("Öppen kategori", None),
    "hojd": ("Höjd", None),
    "a1": ("Avstånd till människor", ["A1"]),
    "a2": ("Avstånd till människor", ["A2"]),
    "a3": ("Avstånd till bebyggelse", ["A3"]),
    "aldre-dronare": ("Underkategori", None),
    "luftrum": ("Luftrum — kontrollzon", None),
    "tiz": ("Luftrum — TIZ och TIA", None),
    "rd-omrade": ("Luftrum — R- och D-område", None),
    "atz": ("Luftrum — trafikzon", None),
    "geozoner": ("Geografiska UAS-zoner", None),
    "skyddsobjekt": ("Skyddsobjekt", None),
    "natura2000": ("Natura 2000", None),
    "spridning": ("Publicering", None),
}


def normalisera_html(ra: bytes) -> str:
    t = ra.decode("utf-8", "replace")
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<(p|div|br|tr|td|li|h[1-6])[^>]*>", "\n", t, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    t = t.replace("­", "")                       # mjukt bindestreck
    return re.sub(r"\s+", " ", t).strip()


def normalisera_pdf(ra: bytes) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "k.pdf")
        with open(p, "wb") as fh:
            fh.write(ra)
        ut = os.path.join(tmp, "k.txt")
        subprocess.run(["pdftotext", "-layout", p, ut], check=True,
                       capture_output=True)
        with open(ut, encoding="utf-8", errors="replace") as fh:
            t = fh.read()
    # Sidfot/sidhuvud: ensamt sidnummer, eller författningsbeteckningen ensam.
    rader = [r for r in t.split("\n")
             if not re.fullmatch(r"\s*\d{1,3}\s*", r)
             and not re.fullmatch(r"\s*TSFS\s+\d{4}:\d+\s*", r)]
    t = "\n".join(rader)
    t = t.replace("­", "")
    # Bindestreck vid radslut är två skilda saker och får inte behandlas lika.
    # "luft-\nrummet" är avstavning och ska fogas ihop. "(D-\nområde)" är ett
    # ÄKTA bindestreck som råkade hamna sist på raden — fogas det ihop står det
    # "Dområde" i citatet, och då är citatet inte längre ordagrant. Det gick
    # först fel just så. Enstaka versal före bindestrecket är signalen.
    t = re.sub(r"(?<![A-Za-zÅÄÖåäö])([A-ZÅÄÖ])-\n\s*(?=\w)", r"\1-", t)
    t = re.sub(r"(\w)-\n\s*(\w)", r"\1\2", t)         # avstavning över radbrytning
    return re.sub(r"\s+", " ", t).strip()


def hamta_kalla(nyckel, spec):
    katalog = ensure_dir(os.path.join(CACHE, "regler"))
    rafil = os.path.join(katalog, nyckel + (".pdf" if spec["format"] == "pdf" else ".html"))
    if os.path.exists(rafil):
        with open(rafil, "rb") as fh:
            ra = fh.read()
        hamtad = "ur cache"
    else:
        extra = {"Accept-Language": spec["sprak"]} if spec.get("sprak") else None
        ra = fetch(spec["url"], min_interval=1.0,
                   accept=spec.get("accept"), headers=extra)
        with open(rafil, "wb") as fh:
            fh.write(ra)
        hamtad = "hämtad"
    text = (normalisera_pdf if spec["format"] == "pdf" else normalisera_html)(ra)
    log(f"  {spec['kortnamn']}: {len(ra) // 1024} kB {hamtad}, "
        f"{len(text)} tecken normaliserad text")
    return ra, text


def klipp(text, spec):
    i = text.find(spec["fran"])
    if i < 0:
        raise RuntimeError(f"{spec['id']}: startmarkören hittades inte i källan")
    j = text.find(spec["till"], i + len(spec["fran"]))
    if j < 0:
        raise RuntimeError(f"{spec['id']}: slutmarkören hittades inte efter start")
    return text[i:j].strip()


def main():
    ensure_dir(DATA)
    kalltext, kallmeta = {}, {}
    for nyckel, spec in KALLOR.items():
        ra, text = hamta_kalla(nyckel, spec)
        kalltext[nyckel] = text
        kallmeta[nyckel] = {
            "titel": spec["titel"], "kortnamn": spec["kortnamn"],
            "beskrivning": spec["beskrivning"], "myndighet": spec["myndighet"],
            "url": spec["lasbar_url"], "hamtad_fran": spec["url"],
            "sha256": sha256_bytes(ra), "hamtad": today(),
            "tecken": len(text),
        }

    avsnitt, fel = [], []
    for spec in AVSNITT:
        text = kalltext[spec["kalla"]]
        try:
            utdrag = klipp(text, spec)
        except RuntimeError as exc:
            fel.append(str(exc))
            continue
        # R7: utdraget ska gå att belägga ordagrant i källan.
        if utdrag not in text:
            fel.append(f"{spec['id']}: utdraget återfinns inte ordagrant i källtexten")
            continue
        saknade = [f for f in MASTE_FINNAS.get(spec["id"], []) if f not in utdrag]
        if saknade:
            fel.append(f"{spec['id']}: bärande formulering saknas i utdraget: {saknade}")
            continue
        if len(utdrag) < 40:
            fel.append(f"{spec['id']}: utdraget för kort ({len(utdrag)} tecken)")
            continue
        kategori, underkat = KATEGORI.get(spec["id"], (spec["grupp"], None))
        avsnitt.append({
            "kategori": kategori,
            "galler_underkategori": underkat,
            "nyckelfras": (MASTE_FINNAS.get(spec["id"]) or [None])[0],
            "id": spec["id"], "grupp": spec["grupp"], "fraga": spec["fraga"],
            "kalla": spec["kalla"], "referens": spec["referens"],
            "text": utdrag, "tecken": len(utdrag),
            "verifierad": True,
            "verifieringsmetod": ("ordagrann strängmatchning mot normaliserad "
                                  "källtext efter extraktion"),
        })

    if fel:
        for f in fel:
            log(f"  FEL: {f}")
        raise SystemExit(
            f"Steg 9 avbryter: {len(fel)} av {len(AVSNITT)} avsnitt kunde inte "
            f"beläggas. En regeltext som inte går att spåra till källan skrivs "
            f"inte ut.")

    write_json(os.path.join(DATA, "regler.json"), {
        "schema_version": 1,
        "hamtningsdatum": today(),
        "kallor": kallmeta,
        "avsnitt": avsnitt,
    })
    log(f"Steg 9 klart: {len(avsnitt)} avsnitt ur {len(kallmeta)} författningar, "
        f"samtliga belagda ordagrant")
    return 0


if __name__ == "__main__":
    sys.exit(main())
