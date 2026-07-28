#!/usr/bin/env python3
"""Steg 6 — Sajtbygge.

Bygger dist/ enbart ur data/ — aldrig ur cache eller ad hoc-hämtningar.
Allt innehåll renderas som statisk HTML: sidorna är fullt läsbara och
indexerbara utan JavaScript. JavaScript används bara till kartan och
positionssvaret.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.common import (CONFIG, DATA, DIST, ROOT, ensure_dir, log, read_json,
                        slugify, write_json)
from lib.rutnat import GEOMETRI_GRADER, ruta_id
from lib.ikon import rita

# Samma färger som kartklienten använder.
LAGERFARG = {
    "nationalpark": "#1b6b3a", "naturreservat": "#2e7d32",
    "naturreservat-kommunalt": "#4a7c1f", "naturvardsomrade": "#00695c",
    "djur-och-vaxtskydd": "#b34700", "kulturreservat": "#6a4f9c",
    "interimistiskt-forbud": "#8e0000", "naturminne": "#00695c",
    "vattenskyddsomrade": "#0d6e9c", "landskapsbildsskydd": "#7a6a2f",
    "biotopskydd": "#4c6b2f",
}

SITE = os.path.join(ROOT, "site")
NAMN = CONFIG["site_name"]
BAS = CONFIG["base_url"].rstrip("/")
OMRADE = CONFIG.get("lan") or "Sverige"

# ---------------------------------------------------------------------------
# Fast ansvarstext (§7 i uppdraget). Får inte kortas, mjukas upp eller
# "förbättras" — den beskriver tjänstens faktiska beteende.
#
# EN mening är ändrad, och bara därför att den blev SAKLIGT FALSK när LFV:s
# luftrum lades in som eget lager. Originalet löd:
#
#   "Luftrumsregler (flygplatser, restriktionsområden, NOTAM, geografiska
#    UAS-zoner) omfattas inte av tjänstens databas — kontrollera alltid LFV:s
#    drönarkarta före flygning."
#
# Delar av det omfattas nu. Att låta texten stå kvar hade varit att underdriva
# vad tjänsten gör, vilket är lika vilseledande som att överdriva det — och
# varningen försvinner inte, den blir preciserad: det som fortfarande INTE
# täcks räknas upp. Ändringen är en rättelse av sakförhållande, inte en
# uppmjukning.
# ---------------------------------------------------------------------------
ANSVARSTEXT = (
    "Den här tjänsten sammanställer och citerar offentliga myndighetsbeslut — "
    "den tolkar dem inte och ger inga klartecken. Citaten är maskinellt "
    "verifierade mot källdokumenten, men fel kan förekomma och regler kan ha "
    "ändrats efter hämtningsdatumet. Luftrumszoner visas som de publiceras av "
    "LFV, men tillfälliga restriktioner, NOTAM och militär verksamhet omfattas "
    "inte — kontrollera alltid LFV:s drönarkarta före flygning. "
    "Som fjärrpilot är du ensam ansvarig för att din flygning följer gällande "
    "regler. Läs alltid det länkade originalbeslutet."
)

# Texten i friskrivningsgrinden. Den kvitteras en gång och sparas lokalt, vilket
# är hela poängen: när den som använder tjänsten vet vad den är, kan
# reservationerna på varje enskild sida hållas korta i stället för att upprepas
# tills de slutar läsas.
GRINDTEXT = [
    ("Tjänsten citerar — den bedömer inte",
     "Allt som visas som regel är ett ordagrant citat ur ett myndighetsbeslut "
     "eller ur en författning, med sidnummer och länk till originalet. Tjänsten "
     "tolkar dem inte, sammanfattar dem inte och ger inga klartecken."),
    ("Tystnad är inte ett besked",
     "”Ingen träff” betyder att tjänstens källor inte innehåller något för den "
     "punkten — inte att något är prövat. Tjänsten skiljer på vad den läst och "
     "vad den inte läst, och säger vilket som är vilket."),
    ("Det som inte täcks",
     "Tillfälliga restriktioner, NOTAM, militär verksamhet, markägares "
     "medgivande och kommunala ordningsföreskrifter finns inte i databasen. "
     "Kontrollera alltid LFV:s drönarkarta före flygning."),
    ("Ansvaret är ditt",
     "Som fjärrpilot är du ensam ansvarig för att flygningen följer gällande "
     "regler. Data kan vara inaktuell och fel kan förekomma."),
]

LFV_RAD = (
    "Luftrum (flygplatser, restriktionsområden, NOTAM) visas i LFV-lagret och på "
    'LFV:s drönarkarta — kontrollera alltid det separat: '
    '<a href="{url}" rel="noopener">{url}</a>.'
).format(url=CONFIG["lfv_wms"]["dronechart"])

FLERA_BESLUT = (
    "Ett område kan ha flera beslut, där ett senare beslut ändrat föreskrifter i "
    "ett tidigare. Tjänsten citerar varje beslut för sig och avgör inte vilken "
    "lydelse som gäller i dag — läs besluten i datumordning."
)

# Etiketterna beskriver vad citatet innehåller — de påstår aldrig vad
# föreskriften betyder eller vad den innebär för en flygning.
KLASS_ETIKETT = {
    "uttryckligt-luftfartygsförbud": "Nämner uttryckligen luftfartyg",
    "start-landningsförbud": "Nämner start eller landning med luftfartyg",
    # Klassen träffar på "motordrivet fordon" men också på "fordon" och
    # "farkost" utan motorbestämning. Etiketten säger därför fordon ELLER
    # farkost — den ska beskriva citatet, inte påstå mer än det står.
    "motorfordon-möjligen-relevant": "Nämner fordon eller farkost — möjligen relevant",
    "störningsförbud-djurliv": "Nämner störning av djurlivet",
    "annat-läs-beslutet": "Nämner tillträde eller framfart — läs beslutet",
}
# Ett citat vars punkt saknar förbudsuttryck, och vars föreskriftsinledning
# inte kunde verifieras, kan lika gärna komma ur beslutets skäl som ur dess
# föreskrifter. Det visas — men det får inte kallas föreskrift.
LAG_KONFIDENS_TILLAGG = (" · står i beslutstexten utan att tjänsten kunnat "
                         "knyta det till en föreskriftspunkt")

LAGER_NAMN = {
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

E = html.escape

# Datafiler som lyfts ut ur dist/ för att de spränger Cloudflares filtak.
# Fylls i av main() innan sidorna byggs; kallsida läser den.
UTELAMNADE = []


def datafil_lank(namn):
    """Länk till en datafil — i utrullningen om den ryms, annars i förrådet."""
    if any(n == namn for n, _ in UTELAMNADE):
        raw = CONFIG["repo_url"].rstrip("/") + "/blob/main/data/" + namn
        return f'<a href="{E(raw)}" rel="noopener">{E(namn)}</a>'
    return f'<a href="/data/{E(namn)}">{E(namn)}</a>'


def _tillgangsversion():
    """Kort innehållshash för app.js och style.css.

    Utan den serverar webbläsaren gammal JavaScript efter en uppdatering — det
    inträffade under utvecklingen och gav ett fel som såg ut att sitta i koden
    men satt i cachen. Stämpeln gör att en ändrad fil får en ny URL.
    """
    h = hashlib.sha256()
    for sokvag in (os.path.join(SITE, "assets", "app.js"),
                   os.path.join(SITE, "assets", "style.css"),
                   # Service workern räknas med, annars kan bara den ändras och
                   # cachenamnen står stilla — då lever den gamla appen kvar i
                   # webbläsaren efter utrullningen, potentiellt i veckor.
                   os.path.join(SITE, "sw.js")):
        if os.path.exists(sokvag):
            with open(sokvag, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()[:8]


VERSION = _tillgangsversion()


def sidmall(*, titel, beskrivning, kanonisk, innehall, bred=False,
            extra_head="", data_datum="", schema=None):
    schema_json = ("\n<script type=\"application/ld+json\">" +
                   json.dumps(schema, ensure_ascii=False) + "</script>") if schema else ""
    wrap = "wrap"
    return f"""<!doctype html>
<html lang="sv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{E(titel)}</title>
<meta name="description" content="{E(beskrivning)}">
<link rel="canonical" href="{E(kanonisk)}">
<meta property="og:type" content="website">
<meta property="og:title" content="{E(titel)}">
<meta property="og:description" content="{E(beskrivning)}">
<meta property="og:url" content="{E(kanonisk)}">
<meta property="og:site_name" content="{E(NAMN)}">
<meta property="og:locale" content="sv_SE">
<link rel="stylesheet" href="/vendor/leaflet/leaflet.css">
<link rel="stylesheet" href="/assets/style.css?v={VERSION}">{extra_head}{schema_json}
</head>
<body>
<header class="topp">
  <div class="{wrap} rad">
    <a class="logotyp" href="/">{E(NAMN)}</a>
    <nav>
      <a href="/">Karta</a>
      <a href="/regler/">Reglerna</a>
      <a href="/omraden/">Alla områden</a>
      <a href="/kallor/">Källor och täckning</a>
      <a href="/om/">Om tjänsten</a>
    </nav>
    <span class="tagline">{E(CONFIG['site_tagline'])}</span>
  </div>
</header>
<div class="dataalder"><div class="{wrap}">Data senast uppdaterad: <strong>{E(data_datum)}</strong></div></div>
<main class="{wrap}">
{innehall}
</main>
<footer class="botten">
  <div class="{wrap}">
    <p>{E(NAMN)} är gratis och reklamfri. Ingen spårning, inga analysverktyg,
    inga cookies utöver tekniskt nödvändiga.</p>
    <p>Områdesdata: Naturvårdsverkets naturvårdsregister (CC0).
    Luftrumslagret: {E(CONFIG['lfv_wms']['attribution'])} — hämtat ur LFV:s DAIM.
    Det lagret omfattas <strong>inte</strong> av tjänstens CC0-publicering.
    Tjänstens egen databas publiceras under CC0.</p>
    <p><a href="{E(CONFIG['repo_url'])}">Källkod och databas</a> ·
    <a href="/kallor/">Källor och täckning</a> ·
    <a href="{E(CONFIG['issue_url'])}">Rapportera fel</a></p>
  </div>
</footer>
</body>
</html>
"""


def avindragen(text: str) -> str:
    """Ta bort den indragning PDF-layouten lagt på varje rad.

    Enbart en visningsåtgärd: `pdftotext -layout` behåller sidans indrag, vilket
    gör citatet svårläst i en smal textspalt. Radernas gemensamma inledande
    blanksteg tas bort. Orden och radbrytningarna är oförändrade, och
    verifieringens normalisering kollapsar ändå all whitespace — den lagrade
    strängen i data/ är orörd.
    """
    rader = text.split("\n")
    if len(rader) < 2:
        return text
    # Citatets första rad har redan fått sitt inledande blanksteg bortklippt vid
    # utskärningen, så den räknas inte med när den gemensamma indragningen mäts
    # — annars blir minsta indrag alltid 0 och raderna under står kvar indragna.
    ovriga = [len(r) - len(r.lstrip()) for r in rader[1:] if r.strip()]
    if not ovriga:
        return text
    minsta = min(ovriga)
    if minsta == 0:
        return text
    return rader[0] + "\n" + "\n".join(
        r[minsta:] if r.strip() else r for r in rader[1:])


def markera(text, ord_lista):
    """Märk ut de ord som gjorde att citatet valdes ut.

    Samma funktion som i kartappen, av samma skäl: ett citat är ofta en halv
    sida beslutstext, och utan märkning måste man läsa alltihop för att se
    varför det kom upp.

    Texten ändras inte — ingenting stryks, ingenting skrivs om, ordföljden är
    orörd. Bara urvalsgrunden görs synlig. Märkningen sker EFTER HTML-escaping
    och söker i den escapade strängen, så att `<mark>`-taggarna aldrig kan
    hamna mitt i en escapesekvens.
    """
    ut = E(text)
    for ord_ in sorted(ord_lista or [], key=len, reverse=True):
        if not ord_ or len(ord_) < 3:
            continue
        n = E(ord_)
        lag_ut, lag_n = ut.lower(), n.lower()
        bit, i = [], 0
        while True:
            p = lag_ut.find(lag_n, i)
            if p < 0:
                bit.append(ut[i:])
                break
            # Hoppa över träffar inuti taggar vi redan lagt in.
            if ut.rfind("<", 0, p) > ut.rfind(">", 0, p):
                bit.append(ut[i:p + len(n)])
                i = p + len(n)
                continue
            # Bara vid ordbörjan. "fordon" inuti "terrängfordon" ska inte ge
            # "terräng[fordon]" — en halv markering läser som ett stavfel.
            if p > 0 and (ut[p - 1].isalpha() or ut[p - 1] == "-"):
                bit.append(ut[i:p + len(n)])
                i = p + len(n)
                continue
            bit.append(ut[i:p])
            bit.append(f"<mark>{ut[p:p + len(n)]}</mark>")
            i = p + len(n)
        ut = "".join(bit)
        lag_ut = ut.lower()
    return ut


def rendera_citat(c):
    """Citatet, dess källa och en länk. Inget mer.

    Tidigare stod här också klassificeringsetikett, konfidensgrad och raden
    "maskinellt verifierad ordagrant mot källdokumentet" — per citat. På en sida
    med tio citat blev det tio upprepningar av samma sak och 1,5 ord förbehåll
    per ord föreskrift. Uppgifterna finns kvar i data/omraden/{nvrid}.json för
    den som vill granska dem; verifieringen redovisas en gång per sida.

    Föreskriftsinledningen renderas inte här utan en gång per grupp — se
    rendera_citatgrupper.
    """
    punkt = f"Punkt {E(c['punkt'])} · " if c.get("punkt") else ""
    return f"""<div class="citat">
<blockquote>{markera(avindragen(c['citat']), c.get('traffade_ord'))}</blockquote>
<div class="kalla">{punkt}sidan {c['sidnummer']} i
<a href="{E(c['dokument_url'])}" rel="noopener">{E(c['dokument_namn'] or 'beslutsdokumentet')}</a></div>
</div>"""


def rendera_citatgrupper(citat):
    """Gruppera citaten efter dokument och föreskriftsinledning.

    Ett beslut har en rubrik — "Utöver föreskrifter och förbud i andra lagar och
    författningar är det förbjudet att:" — och därunder en numrerad lista. Att
    upprepa rubriken före varje punkt, som en tidigare version gjorde, fyllde
    sidan med samma mening fem gånger. Här står den en gång per grupp, precis
    som i beslutet.
    """
    ut, grupper = [], []
    for c in citat:
        nyckel = (c.get("dokument_namn"), c.get("dokument_url"), c.get("inledning"))
        if grupper and grupper[-1][0] == nyckel:
            grupper[-1][1].append(c)
        else:
            grupper.append((nyckel, [c]))

    flera_dokument = len({(g[0][0], g[0][1]) for g in grupper}) > 1
    forra_dokument = None
    for (dok_namn, dok_url, inledning), lista in grupper:
        if flera_dokument and (dok_namn, dok_url) != forra_dokument:
            ut.append(f'<h3><a href="{E(dok_url)}" rel="noopener">{E(dok_namn)}</a></h3>')
            forra_dokument = (dok_namn, dok_url)
        if inledning:
            ut.append('<div class="citat"><blockquote>'
                      f"{E(avindragen(inledning))}</blockquote></div>")
        for c in lista:
            ut.append(rendera_citat(c))
    return "\n".join(ut)


def issue_lank(omrade, svarslage, manifestversion):
    titel = f"Felrapport: {omrade['namn']} (NVRID {omrade['nvrid']})"
    kropp = (
        f"NVRID: {omrade['nvrid']}\n"
        f"Område: {omrade['namn']} ({omrade['skyddstyp']})\n"
        f"Visat svarsläge: {svarslage}\n"
        f"Datamanifestets version: {manifestversion}\n"
        f"Sida: {BAS}/omrade/{omrade['nvrid']}-{omrade['slug']}/\n\n"
        "Beskriv felet:\n"
    )
    import urllib.parse
    q = urllib.parse.urlencode({"title": titel, "body": kropp})
    return f"{CONFIG['issue_url']}?{q}"


# Beskeden på områdessidan. Rubriken säger vad tjänsten VET om just luftfart,
# och skiljer på "läst utan träff" och "inte läst" — de var samma text förut,
# vilket gjorde det omöjligt att se om tystnaden var ett svar eller en lucka.
BESKED = {
    "luftfart": (
        "forbud", "Föreskrifterna nämner luftfart",
        "Nedan står de citat ur beslutet där luftfart nämns ordagrant. "
        "Läs dem i beslutets sammanhang — tjänsten avgör inte vad de innebär "
        "för din flygning."),
    "storning": (
        "storning", "Föreskrifterna förbjuder störning",
        "Beslutet nämner inte drönare, men innehåller förbud mot att störa "
        "djurlivet. En drönare kan träffas av ett sådant förbud. Citaten står "
        "nedan."),
    "last-annat": (
        "last", "Beslutet nämner inget om luftfart",
        "Tjänsten har läst beslutet och funnit föreskrifter, men ingen som "
        "nämner luftfart eller störning. Andra föreskrifter kan ändå vara "
        "relevanta — citaten står nedan."),
    "last-tomt": (
        "okant", "Beslutet är läst men ingen föreskriftstext kunde utläsas",
        "Dokumenten är hämtade och lästa, men tjänsten kunde inte skilja ut "
        "någon föreskriftstext ur dem. Det säger ingenting om vad beslutet "
        "innehåller — läs det."),
    "olast": (
        "okant", "Beslutet har ännu inte lästs av tjänsten",
        "Området finns i registret och beslutsdokumenten är länkade nedan, men "
        "tjänsten har inte hämtat och läst dem. Att inga citat visas betyder "
        "alltså ingenting om vad beslutet säger — läs det."),
    "utan-dokument": (
        "okant", "Inget digitalt beslut att läsa",
        "Registret innehåller inget beslutsdokument för området som tjänsten "
        "kan hämta. Föreskrifter kan ändå finnas — kontakta beslutsmyndigheten "
        "eller se områdets sida hos Naturvårdsverket."),
}


# Samma rubriker, men för de citat som saknar verifierad föreskriftsinledning.
# Se kommentaren vid LAGE i site/assets/app.js: utan inledning kan citatet stå i
# beslutets skäl i stället för i dess föreskrifter, och då får rubriken inte
# säga "föreskriften".
BESKED_SVAG = {
    "luftfart": (
        "forbud", "Beslutet nämner luftfart",
        "Nedan står de citat ur beslutet där luftfart nämns ordagrant. Tjänsten "
        "har inte kunnat knyta dem till en föreskriftspunkt — de kan stå i "
        "beslutets skäl. Läs dem i sitt sammanhang."),
    "storning": (
        "storning", "Beslutet nämner störning av djurlivet",
        "Beslutet nämner inte drönare. Citaten nedan handlar om störning, men "
        "tjänsten har inte kunnat knyta dem till en föreskriftspunkt — de kan "
        "stå i beslutets skäl. Läs dem i sitt sammanhang."),
}


def besked(o):
    lage = o.get("luftfartslage") or "utan-dokument"
    if lage in BESKED_SVAG:
        relevanta = {"luftfart": {"uttryckligt-luftfartygsförbud",
                                  "start-landningsförbud"},
                     "storning": {"störningsförbud-djurliv"}}[lage]
        belagd = any(c.get("inledning") for c in (o.get("citat") or [])
                     if c.get("klassificering") in relevanta)
        if not belagd:
            klass, rubrik, text = BESKED_SVAG[lage]
            return (f'<div class="svar svar-{klass}"><strong>{E(rubrik)}</strong>'
                    f"<p>{E(text)}</p></div>")
    klass, rubrik, text = BESKED.get(lage, BESKED["utan-dokument"])
    return (f'<div class="svar svar-{klass}"><strong>{E(rubrik)}</strong>'
            f"<p>{E(text)}</p></div>")


def omradessida(o, manifest):
    """Områdessidan: en läsbar handling, inte en appvy.

    Ordningen är medveten. Först vad området heter och vad beslutet säger —
    ordagrant. Sedan dokumenten. Förbehållen står samlade sist i stället för
    att skjutas in mellan varje citat, och de tekniska uppgifterna ligger
    hopfällda. Ansvarstexten (§7) står kvar oförändrad, en gång.
    """
    datum = o["hamtningsdatum"]
    citat = o.get("citat") or []
    titel = f"Drönare i {o['namn']} — vad säger föreskrifterna?"
    beskrivning = (f"{o['namn']} ({o['skyddstyp']}, {o['kommun']}). "
                   + (f"{len(citat)} ordagrant verifierade citat ur "
                      "beslutsdokumenten." if citat else
                      "Beslutsdokument länkade — inga citat om luftfartyg hittades.")
                   + f" Hämtat {datum}.")

    d = [f"<h1>{E(o['namn'])}</h1>",
         f'<p class="meta">{E(o["skyddstyp"])} · {E(o["kommun"] or o["lan"])} · '
         f'hämtat {E(datum)}</p>']

    if o.get("ocr"):
        d.append('<div class="ocr-varning">Texten är OCR-tolkad ur inskannat '
                 "original — kontrollera mot källdokumentet.</div>")

    d.append(besked(o))

    if citat:
        d.append("<h2>Ur beslutet, ordagrant</h2>")
        d.append(rendera_citatgrupper(citat))
        if len({(c["dokument_namn"], c["dokument_url"]) for c in citat}) > 1:
            d.append(f'<p class="avstand">{FLERA_BESLUT}</p>')

    dok = [x for x in (o.get("dokument") or []) if x.get("url")]
    d.append("<h2>Beslutsdokument</h2>")
    if dok:
        d.append("<ul>")
        for x in dok:
            extra = []
            if x.get("sidor"):
                extra.append(f"{x['sidor']} sidor")
            if x.get("ocr"):
                extra.append("inskannat")
            if x.get("fel"):
                extra.append(f"kunde inte läsas: {x['fel']}")
            d.append('<li><a href="{}" rel="noopener">{}</a>{}</li>'.format(
                E(x["url"]), E(x.get("namn") or "Beslutsdokument"),
                f' <span class="avstand">({E(", ".join(extra))})</span>' if extra else ""))
        d.append("</ul>")
    else:
        d.append('<div class="svar svar-tacks-ej"><strong>Beslutsdokument ej '
                 "tillgängligt digitalt</strong><p>Se områdets sida hos "
                 "Naturvårdsverket.</p></div>")
    d.append(f'<p><a href="{E(o["sknat_url"])}" rel="noopener">Områdets sida hos '
             "Naturvårdsverket</a></p>")

    if o.get("geometri") is not None:
        rid = ruta_id(o["bbox"][0], o["bbox"][1], GEOMETRI_GRADER)
        d.append(f'<div id="minikarta" data-nvrid="{E(o["nvrid"])}" '
                 f'data-ruta="{E(rid)}" data-farg="{E(LAGERFARG.get(o["lager"], "#2e7d32"))}"></div>')

    if o.get("sasongsdata"):
        d.append("<h2>Tidsbegränsade föreskriftsområden</h2>"
                 '<div class="tabell-scroll"><table><thead><tr><th>Typ</th>'
                 "<th>Undertyp</th><th>Från</th><th>Till</th></tr></thead><tbody>")
        for f in o["sasongsdata"]:
            d.append("<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                E(f.get("foreskriftstyp") or "—"), E(f.get("foreskriftssubtyp") or "—"),
                E(f.get("franDatum") or "—"), E(f.get("tillDatum") or "—")))
        d.append("</tbody></table></div>"
                 '<p class="avstand">Uppgifterna kommer ur Naturvårdsverkets '
                 "register. Vad de innebär framgår av beslutet.</p>")

    # Tekniska uppgifter hopfällda — de är granskningsmaterial, inte läsning.
    rader = [("Skyddstyp", o["skyddstyp"]), ("Kommun", o.get("kommun")),
             ("Län", o.get("lan")), ("Beslutsmyndighet", o.get("beslutsmyndighet")),
             ("Förvaltare", o.get("forvaltare")),
             ("Tillsynsmyndighet", o.get("tillsynsmyndighet")),
             ("Ursprungligt beslutsdatum", o.get("urspr_beslutsdatum")),
             ("Senaste gällandedatum", o.get("senaste_gallandedatum")),
             ("Areal enligt registret (ha)", o.get("area_ha")),
             ("Areal beräknad ur geometrin (ha)", o.get("beraknad_area_ha")),
             ("NVRID", o["nvrid"]),
             ("Geometrins källa", o["geometri_kalla"]["tjanst"] + ", hämtad "
              + o["geometri_kalla"]["hamtningsdatum"] + ", licens "
              + o["geometri_kalla"]["licens"]),
             ("Geometrins svarshash (SHA-256)",
              o["geometri_kalla"]["svarshash_sha256"])]
    d.append("<details><summary>Uppgifter och proveniens</summary>"
             '<div class="tabell-scroll"><table><tbody>')
    for k, v in rader:
        if v in (None, "", " "):
            continue
        d.append(f"<tr><th>{E(k)}</th><td>{E(str(v))}</td></tr>")
    d.append("</tbody></table></div>")
    if o.get("dokument_hoppade"):
        d.append(f"<p>Dokument tjänsten inte läst ({len(o['dokument_hoppade'])} st):</p><ul>")
        for x in o["dokument_hoppade"]:
            d.append('<li><a href="{}" rel="noopener">{}</a> — {}</li>'.format(
                E(x.get("url") or ""), E(x.get("namn") or "dokument"),
                E(x.get("orsak") or "")))
        d.append("</ul>")
    d.append("</details>")

    d.append('<div class="svar svar-tacks-ej"><strong>Denna källa täcks inte här'
             f"</strong><p>{LFV_RAD}</p></div>")
    if citat:
        d.append('<p class="avstand">Samtliga citat på sidan är maskinellt '
                 "kontrollerade ordagrant mot källdokumentets text.</p>")
    d.append(f'<p class="ansvar">{ANSVARSTEXT}</p>')
    d.append('<p><a href="{}" rel="noopener">Rapportera fel på den här sidan</a></p>'
             .format(E(issue_lank(o, "Reglerat område — läs beslutet",
                                  manifest["hamtningsdatum"]))))

    schema = {
        "@context": "https://schema.org", "@type": "FAQPage",
        "mainEntity": [{
            "@type": "Question",
            "name": f"Vad säger föreskrifterna om drönare i {o['namn']}?",
            "acceptedAnswer": {
                "@type": "Answer",
                "text": (("Tjänsten citerar %d punkter ordagrant ur "
                          "beslutsdokumenten för %s. Den tolkar dem inte och ger "
                          "inga klartecken. " % (len(citat), o["namn"])) if citat else
                         ("Ingen föreskrift som uttryckligen nämner luftfartyg "
                          "hittades i beslutet för %s. Andra föreskrifter kan ändå "
                          "vara relevanta — läs beslutet. " % o["namn"]))
                        + "Luftrumsregler omfattas inte av tjänstens databas — "
                          "kontrollera alltid LFV:s drönarkarta före flygning.",
            }}]}
    return sidmall(
        titel=titel, beskrivning=beskrivning,
        kanonisk=f"{BAS}/omrade/{o['nvrid']}-{o['slug']}/",
        innehall="\n".join(d), data_datum=datum, schema=schema,
        extra_head='\n<script defer src="/vendor/leaflet/leaflet.js"></script>'
                   f'\n<script defer src="/assets/app.js?v={VERSION}"></script>'
                   f'\n<script>window.DK_LFV={json.dumps(CONFIG["lfv_wms"])};</script>')


def startsida(manifest, bboxindex):
    """Startsidan är en kartapp, inte en textsida med en liten karta.

    Kartan fyller skärmen, positionen hämtas direkt vid laddning, och svaret
    kommer upp som en panel över kartan. Beskrivande text hör hemma på /om/.
    """
    datum = manifest["hamtningsdatum"]
    huvud = f'''<script>window.DK_LFV={json.dumps(CONFIG["lfv_wms"])};
window.DK_ANSVARSTEXT={json.dumps(ANSVARSTEXT)};</script>
<script defer src="/vendor/leaflet/leaflet.js"></script>
<script defer src="/assets/app.js?v={VERSION}"></script>'''

    grind = "".join(
        f"<li><strong>{E(rub)}</strong><span>{E(txt)}</span></li>"
        for rub, txt in GRINDTEXT)

    kropp = f'''<div id="karta"></div>
<header class="apptopp">
  <a class="namn" href="/om/">{E(NAMN)}</a>
  <div class="hoger">
    <button class="ikonknapp" id="installknapp" hidden>Installera</button>
    <button class="ikonknapp" id="lagerknapp" aria-label="Lager och information">Lager</button>
  </div>
</header>
<div id="natstatus" hidden>Offline — luftrum och luftfartsföreskrifter svarar ändå,
  övrig data bara om den hunnit cachas.</div>
<div id="kartstatus"></div>
<div id="lagerpanel">
  <label><input type="checkbox" id="lfvtoggle" checked> Luftrum (LFV)</label>
  <p class="lagermeta" id="lfvmeta"></p>
  <p class="lagermeta">Områdesdata hämtad {E(datum)}</p>
  <p class="lagermeta"><a href="/regler/">Reglerna som gäller överallt</a> ·
    <a href="/omraden/">Alla områden</a> · <a href="/kallor/">Källor</a> ·
    <a href="/om/">Om tjänsten</a></p>
</div>
<div class="knapprad">
  <button class="storknapp primar" id="positionsknapp">Var står jag?</button>
  <button class="storknapp vakt" id="vaktknapp" aria-pressed="false">
    <span class="vaktprick"></span><span class="vakttext">Vakt</span>
  </button>
</div>
<section id="panel" class="panel" aria-live="polite">
  <div class="handtag"></div>
  <button id="stangpanel" aria-label="Stäng">×</button>
  <div id="svar"></div>
</section>
<div id="grind" class="grind" hidden>
  <div class="grindkort" role="dialog" aria-modal="true" aria-labelledby="grindrubrik">
    <h1 id="grindrubrik">Innan du använder {E(NAMN)}</h1>
    <ul class="grindlista">{grind}</ul>
    <label class="grindval">
      <input type="checkbox" id="grindkryss">
      <span>Jag har läst detta och förstår att tjänsten inte ger klartecken.</span>
    </label>
    <button id="grindknapp" class="storknapp primar">Fortsätt till kartan</button>
    <p class="grindfot"><a href="/om/">Om tjänsten</a> ·
      <a href="/kallor/">Källor och täckning</a> ·
      <a href="/regler/">Reglerna som gäller överallt</a></p>
  </div>
</div>
<noscript>
  <div class="wrap">
    <h1>{E(NAMN)}</h1>
    <p>Kartan kräver JavaScript. Alla områden finns som vanliga sidor:
    <a href="/omraden/">registret över samtliga områden</a>, och
    <a href="/regler/">reglerna som gäller överallt</a> är en vanlig textsida.</p>
  </div>
</noscript>'''
    return f'''<!doctype html>
<html lang="sv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{E(NAMN)} — {E(CONFIG["site_tagline"])}</title>
<meta name="description" content="Karta över skyddade naturområden i Sverige med ordagranna citat ur myndigheternas beslutsföreskrifter om drönare. Gratis, reklamfri, utan spårning.">
<link rel="canonical" href="{BAS}/">
<meta property="og:type" content="website">
<meta property="og:title" content="{E(NAMN)}">
<meta property="og:url" content="{BAS}/">
<meta property="og:locale" content="sv_SE">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#0b2d4a">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="{E(NAMN)}">
<link rel="apple-touch-icon" href="/assets/ikon-192.png">
<link rel="icon" href="/assets/ikon-192.png">
<link rel="stylesheet" href="/vendor/leaflet/leaflet.css">
<link rel="stylesheet" href="/assets/style.css?v={VERSION}">
{huvud}
</head>
<body class="app">
{kropp}
</body>
</html>
'''


def reglersida(regler, manifest):
    """De regler som gäller överallt, ordagrant ur författningarna.

    Frågorna är rubriker, inte svar. De hjälper dig hitta rätt citat — de
    sammanfattar det inte. Under varje fråga står författningstexten som den
    står, med paragraf och länk till originalet.
    """
    if not regler:
        return None
    kallor = regler["kallor"]
    grupper = []
    for a in regler["avsnitt"]:
        if not grupper or grupper[-1][0] != a["grupp"]:
            grupper.append((a["grupp"], []))
        grupper[-1][1].append(a)

    d = ["<h1>Reglerna som gäller överallt</h1>",
         "<p class='ingress'>De flesta reglerna som gäller en drönarflygning är "
         "inte platsbundna. De står i en EU-förordning och i Transportstyrelsens "
         "föreskrifter. Här står de ordagrant, med paragraf och länk till "
         "originalet.</p>",
         "<p class='notis'>Frågorna nedan är rubriker som hjälper dig hitta. "
         "De sammanfattar inte författningstexten — svaret är citatet.</p>"]

    d.append("<nav class='regelnav'><ul>")
    for grupp, avsnitt in grupper:
        for a in avsnitt:
            d.append(f"<li><a href='#{E(a['id'])}'>{E(a['fraga'])}</a></li>")
    d.append("</ul></nav>")

    for grupp, avsnitt in grupper:
        d.append(f"<h2>{E(grupp)}</h2>")
        for a in avsnitt:
            k = kallor[a["kalla"]]
            d.append(f"<section class='regelavsnitt' id='{E(a['id'])}'>")
            d.append(f"<h3>{E(a['fraga'])}</h3>")
            d.append("<figure class='citat'>")
            d.append(f"<blockquote>{E(a['text'])}</blockquote>")
            d.append(
                f"<figcaption>{E(k['kortnamn'])} {E(a['referens'])} · "
                f"<a href='{E(k['url'])}' rel='noopener'>{E(k['titel'])}</a> · "
                f"hämtad {E(k['hamtad'])}</figcaption>")
            d.append("</figure></section>")

    d.append("<h2>Författningarna i sin helhet</h2><ul class='kallista'>")
    for nyckel, k in kallor.items():
        d.append(
            f"<li><a href='{E(k['url'])}' rel='noopener'>{E(k['titel'])}</a> — "
            f"{E(k['beskrivning'])}. {E(k['myndighet'])}. "
            f"Hämtad {E(k['hamtad'])}, sha256 <code>{E(k['sha256'][:16])}…</code></li>")
    d.append("</ul>")

    d.append(
        "<h2>Det här är inte allt</h2>"
        "<p>Sidan täcker de författningar som listas ovan. Den täcker inte "
        "NOTAM, tillfälliga restriktioner, kommunala ordningsföreskrifter, "
        "kamerabevakningsreglerna eller markägares medgivande. Föreskrifter för "
        "en enskild plats står i respektive områdesbeslut och nås via "
        "<a href='/'>kartan</a>.</p>")
    d.append(f"<p class='ansvar'>{ANSVARSTEXT}</p>")

    return sidmall(
        titel=f"Reglerna som gäller överallt — {NAMN}",
        beskrivning=("Ordagranna citat ur EU 2019/947, TSFS 2017:110, "
                     "skyddslagen och luftfartslagen, ordnade under frågor."),
        kanonisk=f"{BAS}/regler/",
        innehall="\n".join(d),
        data_datum=regler["hamtningsdatum"])


def kallsida(manifest, rapport, omraden):
    datum = manifest["hamtningsdatum"]
    per_lager = {}
    for o in omraden:
        per_lager.setdefault(o["lager"], []).append(o)

    d = ["<h1>Källor och täckning</h1>"]
    d.append("<p>Ett lager per rättskälla. Lagren blandas aldrig. Nedan står vad varje "
             "lager innehåller, varifrån det kommer, vilken licens som gäller, när det "
             "hämtades och vilka luckor som är kända.</p>")

    d.append("<h2>Lager i tjänstens egen databas</h2>")
    d.append('<div class="tabell-scroll"><table><thead><tr>'
             f"<th>Lager</th><th>Antal i {OMRADE}</th><th>Varav med verifierade citat</th>"
             "<th>Hämtat</th><th>Licens</th></tr></thead><tbody>")
    for lager, namn in LAGER_NAMN.items():
        lista = per_lager.get(lager, [])
        med = sum(1 for o in lista if o.get("citat"))
        d.append("<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>CC0</td></tr>".format(
            E(namn), len(lista), med, E(datum)))
    d.append("</tbody></table></div>")

    tomma = [LAGER_NAMN[l] for l in LAGER_NAMN if not per_lager.get(l)]
    if tomma:
        d.append('<div class="svar svar-tacks-ej"><span class="svar-rubrik">'
                 "Denna källa täcks inte här</span>"
                 "<p>Följande lager ingår i tjänstens scope men Naturvårdsverkets "
                 f"register returnerar inga objekt av den typen i {OMRADE} vid "
                 f"hämtningen {E(datum)}: <strong>{E(', '.join(tomma))}</strong>. "
                 "En tom kategori är ett besked om registret, inte ett besked om att "
                 "inget är reglerat.</p></div>")

    d.append("<h2>Kända luckor</h2><ul>")
    d.append("<li>Naturvårdsverkets punktlager <em>SkyddadePunkter</em> returnerar noll "
             f"objekt för {OMRADE} ({E(datum)}). Naturminnen finns i "
             "ytlagret i stället.</li>")
    d.append("<li>Tillträdesförbud har enligt Naturvårdsverket kända ajourföringsluckor "
             "i Södermanlands, Örebro, Gotlands och Stockholms län. Antalet objekt "
             "per lager står i tabellen ovan.</li>")
    d.append("<li>Tjänsten läser bara dokument vars filnamn pekar ut dem som beslut, "
             "föreskrifter, förordnanden, kungörelser eller ändringsbeslut. "
             "Skötselplaner, kartbilagor och visningsbilder läses inte. Vilka dokument "
             "som hoppats över för ett enskilt område står på områdets sida.</li>")
    d.append("<li>Ett område kan ha flera beslut där ett senare ändrat ett tidigare. "
             "Tjänsten avgör inte vilken lydelse som gäller i dag.</li>")
    d.append("<li>Luftrum — flygplatser, restriktionsområden, NOTAM och geografiska "
             "UAS-zoner — ingår inte i tjänstens databas. De visas enbart som "
             "LFV:s eget rasterlager.</li>")
    d.append("</ul>")

    d.append("<h2>Luftrumslager (visas, men bearbetas inte)</h2>")
    d.append("<p>LFV:s WMS-tjänst visas som ostylat raster direkt från LFV:s server. "
             "Tjänsten hämtar inte LFV:s vektordata, stylar inte om, cachar inte deras "
             "rutor och gör inga beräkningar mot deras data. "
             f"Attribution: {E(CONFIG['lfv_wms']['attribution'])}. "
             f'Källa: <a href="{E(CONFIG["lfv_wms"]["url"])}" rel="noopener">'
             f'{E(CONFIG["lfv_wms"]["url"])}</a>. '
             f'LFV:s drönarkarta: <a href="{E(CONFIG["lfv_wms"]["dronechart"])}" '
             f'rel="noopener">{E(CONFIG["lfv_wms"]["dronechart"])}</a>.</p>')

    d.append("<h2>Verifieringens utfall</h2>")
    s = rapport["statistik"]
    d.append('<div class="tabell-scroll"><table><tbody>')
    for k, v in [
        ("Objekt i databasen", s["antal_objekt"]),
        ("Objekt med minst ett verifierat citat", s["objekt_med_verifierade_citat"]),
        ("Objekt i länk-läge (inget citat höll eller inget hittades)",
         s["objekt_i_lanklage"] + s["objekt_utan_traffar"]),
        ("Objekt vars beslut ännu inte lästs av tjänsten",
         (manifest.get("databygge", {}).get("statistik", {})
          .get("beslut_ej_last", 0))),
        ("Objekt med OCR-tolkad text", s["objekt_med_ocr"]),
        ("Citat prövade", s["citat_prövade"]),
        ("Citat godkända", s["citat_godkanda"]),
        ("Citat kasserade", s["citat_kasserade"]),
        ("Andel godkända citat", f"{s['andel_citat_godkanda_procent']} %"),
    ]:
        d.append(f"<tr><th>{E(k)}</th><td>{E(str(v))}</td></tr>")
    d.append("</tbody></table></div>")
    d.append("<p>Verifieringen är en ordagrann strängmatchning mot källdokumentets "
             "text efter dokumenterad normalisering. Ingen språkmodell är inblandad i "
             "verifieringen och ingen likhetströskel används — ett citat som inte "
             "förekommer ordagrant kastas.</p>")
    d.append('<p>Fullständiga maskinläsbara utfall: '
             '<a href="/data/verification-report.json">verification-report.json</a>'
             + (f', {datafil_lank("manifest.json")}.</p>'))
    if UTELAMNADE:
        # Länkarna ovan får inte peka på filer som inte ligger här. En bruten
        # länk till det egna underlaget är sämre än att säga var det finns.
        d.append("<p>Följande filer är för stora för utrullningen "
                 "(Cloudflare Pages tar max 25 MB per fil) och finns i "
                 "kodförrådet i stället: "
                 + ", ".join(f"{datafil_lank(n)} ({m} MB)" for n, m in UTELAMNADE)
                 + ".</p>")

    d.append("<h2>Ladda ned databasen</h2>")
    d.append('<p>Tjänstens databas publiceras under CC0. '
             + datafil_lank("oversikt.json") + ' (förenklad '
             'visningsgeometri), rutnätsfiler under <code>/data/rutor/</code>, '
             "oförenklad originalgeometri under <code>/data/geom/</code>, samt "
             '<a href="/data/LICENSE">LICENSE</a> och '
             '<a href="/data/README.md">schemabeskrivning</a>. '
             "Fullständiga per-områdesposter — med citat, dokumentlänkar och "
             "proveniens i en fil per område — finns i kodförrådet under "
             f'<a href="{E(CONFIG["repo_url"])}/tree/main/data/omraden">'
             "data/omraden/</a>.</p>")
    d.append(f'<div class="ansvar">{ANSVARSTEXT}</div>')
    return sidmall(titel=f"Källor och täckning — {NAMN}",
                   beskrivning=("Vilka rättskällor tjänsten täcker, varifrån data "
                                "kommer, hämtningsdatum, licenser och kända luckor."),
                   kanonisk=BAS + "/kallor/", innehall="\n".join(d), data_datum=datum)


def omsida(manifest, rapport):
    datum = manifest["hamtningsdatum"]
    st = rapport["statistik"]
    d = [f"<h1>Om {E(NAMN)}</h1>",
         "<p>Tjänsten visar var det finns skyddade naturområden vars "
         "myndighetsbeslut kan innehålla föreskrifter som berör drönarflygning, "
         "och citerar föreskrifterna <strong>ordagrant</strong> ur besluten. "
         "Den tolkar dem inte och ger inga klartecken. Gratis, reklamfri, "
         "utan spårning.</p>",

         "<h2>Tre svar, aldrig ett fjärde</h2><ol>"
         "<li><strong>Reglerat område — läs beslutet.</strong> Med ordagranna "
         "citat och länk till originalbeslutet.</li>"
         "<li><strong>Ingen restriktion hittad i de källor tjänsten täcker.</strong> "
         "Ett besked om vad databasen innehåller, inte om din flygning.</li>"
         "<li><strong>Denna källa täcks inte här.</strong> Per rättskälla — "
         "framför allt luftrummet, som tjänsten inte har i sin databas.</li>"
         "</ol>",

         "<h2>Så tas citaten fram</h2><ol>"
         "<li>Områdena hämtas ur Naturvårdsverkets naturvårdsregister via öppet "
         "API. Geometrin används precis som myndigheten levererat den.</li>"
         "<li>Beslutsdokumenten laddas ned från Naturvårdsverkets dokumentarkiv. "
         "Inskannade original OCR-tolkas på svenska.</li>"
         "<li>Föreskriftspunkter som kan beröra flygning skärs ut som "
         "sammanhängande delsträngar ur dokumentets text. Ingen text skrivs om "
         "eller sätts ihop från flera ställen.</li>"
         "<li>Ett fristående kontrollskript strängmatchar varje citat mot "
         "källdokumentet. Håller citatet inte måttet kastas det, och området "
         "visas med enbart länken.</li></ol>",

         "<h2>Vad tjänsten aldrig gör</h2><ul>"
         "<li>Ritar egna zoner, buffertar eller cirklar kring något.</li>"
         "<li>Skriver om en föreskrift med egna ord.</li>"
         "<li>Ger ett samlat omdöme om huruvida en flygning är i sin ordning.</li>"
         "<li>Spårar besökare eller visar reklam.</li></ul>",

         "<h2>Siffror vid senaste bygget</h2>"
         '<div class="tabell-scroll"><table><tbody>'
         f'<tr><th>Områden i databasen</th><td>{st["antal_objekt"]}</td></tr>'
         f'<tr><th>Citat som visas</th><td>{st["citat_godkanda"]}</td></tr>'
         f'<tr><th>Citat som klarade ordagrann kontroll</th>'
         f'<td>{st["andel_citat_godkanda_procent"]} %</td></tr>'
         f'<tr><th>Data hämtad</th><td>{E(datum)}</td></tr>'
         "</tbody></table></div>"
         '<p>Mer i <a href="/kallor/">källor och täckning</a>.</p>',

         "<h2>Hitta fel?</h2>"
         f'<p><a href="{E(CONFIG["issue_url"])}" rel="noopener">Öppna ett ärende '
         "i tjänstens öppna kodförråd.</a> Varje bekräftat fel blir ett "
         "regressionstest innan det rättas.</p>",

         f'<p class="ansvar">{ANSVARSTEXT}</p>']
    return sidmall(titel=f"Om tjänsten — {NAMN}",
                   beskrivning=("Hur tjänsten hämtar, citerar och verifierar "
                                "föreskrifter ur myndighetsbeslut — och vad den "
                                "medvetet aldrig gör."),
                   kanonisk=BAS + "/om/", innehall="\n".join(d), data_datum=datum)


def omradeslista(omraden, manifest):
    """Registret. En sida per län — 10 772 poster på en sida är ingen sida."""
    datum = manifest["hamtningsdatum"]
    per_lan = {}
    for o in omraden:
        per_lan.setdefault((o.get("lan") or "Okänt län").strip(), []).append(o)

    sidor = {}
    d = [f"<h1>Alla områden i {OMRADE}</h1>",
         f"<p>{len(omraden)} skyddade områden, ett register per län. Varje "
         "områdessida citerar beslutets föreskrifter ordagrant och länkar till "
         "originaldokumentet.</p>", '<ul class="lista-omraden">']
    for lan in sorted(per_lan):
        lista = per_lan[lan]
        med = sum(1 for x in lista if x.get("citat"))
        d.append(f'<li><a href="/omraden/{slugify(lan)}/">{E(lan)}</a> '
                 f'<span class="avstand">{len(lista)} områden, {med} med citat</span></li>')
    d.append("</ul>")
    sidor["index"] = sidmall(
        titel=f"Alla områden i {OMRADE} — {NAMN}",
        beskrivning=(f"Register över {len(omraden)} skyddade områden i {OMRADE} "
                     "med föreskriftscitat och länk till myndighetsbesluten."),
        kanonisk=BAS + "/omraden/", innehall="\n".join(d), data_datum=datum)

    for lan, lista in per_lan.items():
        per_kommun = {}
        for o in lista:
            for k in (o.get("kommun") or "Okänd kommun").split(","):
                per_kommun.setdefault(k.strip() or "Okänd kommun", []).append(o)
        d = [f"<h1>Skyddade områden i {E(lan)}</h1>",
             f'<p class="meta">{len(lista)} områden · <a href="/omraden/">alla län</a></p>']
        for kommun in sorted(per_kommun):
            rader = sorted(per_kommun[kommun], key=lambda x: x["namn"] or "")
            d.append(f"<h2>{E(kommun)} ({len(rader)})</h2>"
                     '<ul class="lista-omraden">')
            for o in rader:
                d.append('<li><a href="/omrade/{}-{}/">{}</a> '
                         '<span class="avstand">{}{}</span></li>'.format(
                             E(o["nvrid"]), E(o["slug"]), E(o["namn"]),
                             E(o["skyddstyp"]),
                             f" · {len(o['citat'])} citat" if o.get("citat") else ""))
            d.append("</ul>")
        sidor[slugify(lan)] = sidmall(
            titel=f"Skyddade områden i {lan} — {NAMN}",
            beskrivning=(f"{len(lista)} skyddade områden i {lan} med ordagranna "
                         "citat ur myndighetsbeslutens föreskrifter om drönare."),
            kanonisk=f"{BAS}/omraden/{slugify(lan)}/",
            innehall="\n".join(d), data_datum=datum)
    return sidor


def skriv(path, text):
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def skriv_bytes(path, data):
    ensure_dir(os.path.dirname(path))
    with open(path, "wb") as fh:
        fh.write(data)


def skriv_pwa():
    """Manifest, service worker och ikoner.

    Service workern får samma innehållshash som app.js och style.css. Utan det
    lever den gamla versionen kvar i cachen efter en utrullning — precis den
    sortens fel som redan kostat en halvtimmes felsökning en gång, med skillnaden
    att en service worker kan hålla kvar en gammal app i veckor.
    """
    manifest = {
        "name": NAMN,
        "short_name": NAMN,
        "description": CONFIG["site_tagline"],
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait-primary",
        "background_color": "#0b2d4a",
        "theme_color": "#0b2d4a",
        "lang": "sv-SE",
        "categories": ["navigation", "utilities"],
        "icons": [
            {"src": "/assets/ikon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/assets/ikon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/assets/ikon-512-mask.png", "sizes": "512x512",
             "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [
            {"name": "Var står jag?", "url": "/?position=1"},
            {"name": "Reglerna som gäller överallt", "url": "/regler/"},
        ],
    }
    write_json(os.path.join(DIST, "manifest.webmanifest"), manifest)

    with open(os.path.join(SITE, "sw.js"), encoding="utf-8") as fh:
        sw = fh.read()
    skriv(os.path.join(DIST, "sw.js"), sw.replace("__VERSION__", VERSION))

    for storlek, maskbar, namn in ((192, False, "ikon-192.png"),
                                   (512, False, "ikon-512.png"),
                                   (512, True, "ikon-512-mask.png")):
        skriv_bytes(os.path.join(DIST, "assets", namn), rita(storlek, maskbar))


def main():
    manifest = read_json(os.path.join(DATA, "manifest.json"))
    rapport = read_json(os.path.join(DATA, "verification-report.json"))
    bboxindex = read_json(os.path.join(DATA, "bbox-index.json"))
    if not all((manifest, rapport, bboxindex)):
        sys.exit("Kör scripts/01–05 först.")

    if os.path.isdir(DIST):
        shutil.rmtree(DIST)
    ensure_dir(DIST)

    shutil.copytree(os.path.join(SITE, "assets"), os.path.join(DIST, "assets"))
    shutil.copytree(os.path.join(SITE, "vendor"), os.path.join(DIST, "vendor"))
    # Per-områdesfilerna och areas.geojson kopieras INTE till dist/. Med 10 772
    # områden skulle de tillsammans med lika många HTML-sidor spränga Cloudflare
    # Pages tak på 20 000 filer per utrullning. Kartan behöver dem inte heller —
    # den läser geometri ur data/geom/ och citat ur HTML-sidorna. Filerna finns
    # kvar i kodförrådet som CC0-produkt och länkas därifrån på /kallor/.
    shutil.copytree(DATA, os.path.join(DIST, "data"),
                    ignore=shutil.ignore_patterns("omraden", "areas.geojson"))

    # Cloudflare Pages tar max 25 MB per fil. Bulkfilerna — manifest, hela
    # citatkorpusen, råextraktionen — passerade taket när rikstäckningen växte,
    # och utrullningen stoppades helt. De används inte av appen (den läser
    # rutnätet, lfv.json och luftfart.json) utan finns för granskning, så de
    # lyfts ut ur dist/ och länkas i kodförrådet i stället.
    #
    # Gränsen är satt med marginal till taket: en fil som ligger på 24 MB idag
    # spränger det vid nästa registeruppdatering, och då faller utrullningen
    # igen — vid sämsta tänkbara tillfälle.
    global UTELAMNADE
    UTELAMNADE = []
    for namn in sorted(os.listdir(os.path.join(DIST, "data"))):
        p = os.path.join(DIST, "data", namn)
        if not os.path.isfile(p):
            continue
        mb = os.path.getsize(p) / (1024 * 1024)
        if mb > 20:
            os.remove(p)
            UTELAMNADE.append((namn, round(mb, 1)))
    if UTELAMNADE:
        log("  utelämnade ur dist/ (över 20 MB, finns i kodförrådet): " +
            ", ".join(f"{n} {m} MB" for n, m in UTELAMNADE))

    omraden = []
    for nvrid in sorted(manifest["objekt"]):
        o = read_json(os.path.join(DATA, "omraden", f"{nvrid}.json"))
        if o:
            omraden.append(o)

    skriv(os.path.join(DIST, "index.html"), startsida(manifest, bboxindex))
    skriv(os.path.join(DIST, "kallor", "index.html"),
          kallsida(manifest, rapport, omraden))
    skriv(os.path.join(DIST, "om", "index.html"), omsida(manifest, rapport))
    skriv_pwa()

    regler = read_json(os.path.join(DATA, "regler.json"))
    regelsida_html = reglersida(regler, manifest)
    if regelsida_html:
        skriv(os.path.join(DIST, "regler", "index.html"), regelsida_html)
    else:
        # Sidan lovas i navigationen och i appens svar. Saknas underlaget ska
        # bygget säga det, inte tyst leverera en trasig länk.
        log("  VARNING: data/regler.json saknas — /regler/ byggdes inte. "
            "Kör scripts/09_regler.py.")
    listsidor = omradeslista(omraden, manifest)
    for nyckel, html_text in listsidor.items():
        if nyckel == "index":
            skriv(os.path.join(DIST, "omraden", "index.html"), html_text)
        else:
            skriv(os.path.join(DIST, "omraden", nyckel, "index.html"), html_text)

    urler = [BAS + "/", BAS + "/kallor/", BAS + "/om/", BAS + "/omraden/"]
    if regelsida_html:
        urler.append(BAS + "/regler/")
    urler += [f"{BAS}/omraden/{n}/" for n in listsidor if n != "index"]
    for o in omraden:
        katalog = os.path.join(DIST, "omrade", f"{o['nvrid']}-{o['slug']}")
        skriv(os.path.join(katalog, "index.html"), omradessida(o, manifest))
        urler.append(f"{BAS}/omrade/{o['nvrid']}-{o['slug']}/")

    skriv(os.path.join(DIST, "sitemap.xml"),
          '<?xml version="1.0" encoding="UTF-8"?>\n'
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
          + "".join(f"<url><loc>{html.escape(u)}</loc>"
                    f"<lastmod>{manifest['hamtningsdatum']}</lastmod></url>\n"
                    for u in urler)
          + "</urlset>\n")
    skriv(os.path.join(DIST, "robots.txt"),
          f"User-agent: *\nAllow: /\n\nSitemap: {BAS}/sitemap.xml\n")
    skriv(os.path.join(DIST, "_headers"),
          "/*\n  X-Content-Type-Options: nosniff\n"
          "  Referrer-Policy: no-referrer\n"
          "  Permissions-Policy: geolocation=(self), interest-cohort=()\n")

    log(f"Steg 6 klart: {len(omraden)} områdessidor + 4 sidor i dist/")


if __name__ == "__main__":
    main()
