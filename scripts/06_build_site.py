#!/usr/bin/env python3
"""Steg 6 — Sajtbygge.

Bygger dist/ enbart ur data/ — aldrig ur cache eller ad hoc-hämtningar.
Allt innehåll renderas som statisk HTML: sidorna är fullt läsbara och
indexerbara utan JavaScript. JavaScript används bara till kartan och
positionssvaret.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.common import CONFIG, DATA, DIST, ROOT, ensure_dir, log, read_json, write_json

SITE = os.path.join(ROOT, "site")
NAMN = CONFIG["site_name"]
BAS = CONFIG["base_url"].rstrip("/")

# ---------------------------------------------------------------------------
# Fast ansvarstext (§7 i uppdraget). Får inte kortas, mjukas upp eller
# "förbättras" — den beskriver tjänstens faktiska beteende.
# ---------------------------------------------------------------------------
ANSVARSTEXT = (
    "Den här tjänsten sammanställer och citerar offentliga myndighetsbeslut — "
    "den tolkar dem inte och ger inga klartecken. Citaten är maskinellt "
    "verifierade mot källdokumenten, men fel kan förekomma och regler kan ha "
    "ändrats efter hämtningsdatumet. Luftrumsregler (flygplatser, "
    "restriktionsområden, NOTAM, geografiska UAS-zoner) omfattas inte av "
    "tjänstens databas — kontrollera alltid LFV:s drönarkarta före flygning. "
    "Som fjärrpilot är du ensam ansvarig för att din flygning följer gällande "
    "regler. Läs alltid det länkade originalbeslutet."
)

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
    "motorfordon-möjligen-relevant": "Nämner motordrivet fordon — möjligen relevant",
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


def sidmall(*, titel, beskrivning, kanonisk, innehall, bred=False,
            extra_head="", data_datum="", schema=None):
    schema_json = ("\n<script type=\"application/ld+json\">" +
                   json.dumps(schema, ensure_ascii=False) + "</script>") if schema else ""
    wrap = "wrap-bred" if bred else "wrap"
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
<link rel="stylesheet" href="/assets/style.css">{extra_head}{schema_json}
</head>
<body>
<header class="topp">
  <div class="{wrap} rad">
    <a class="logotyp" href="/">{E(NAMN)}</a>
    <nav>
      <a href="/">Karta</a>
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
    Luftrumslagret: {E(CONFIG['lfv_wms']['attribution'])} — visas som raster direkt
    från LFV:s server. Tjänstens egen databas publiceras under CC0.</p>
    <p><a href="{E(CONFIG['repo_url'])}">Källkod och databas</a> ·
    <a href="/kallor/">Källor och täckning</a> ·
    <a href="{E(CONFIG['issue_url'])}">Rapportera fel</a></p>
  </div>
</footer>
</body>
</html>
"""


def rendera_citat(c):
    etikett = KLASS_ETIKETT.get(c["klassificering"], c["klassificering"])
    if c.get("konfidens") == "låg":
        etikett += LAG_KONFIDENS_TILLAGG
    punkt = f"Punkt {E(c['punkt'])}, " if c.get("punkt") else ""
    # Föreskriftsinledningen är en egen, separat verifierad delsträng ur samma
    # sida. Den visas som eget stycke ovanför punkten — aldrig hopskarvad med
    # citatet till en ny textmassa.
    inledning = (f"<blockquote>{E(c['inledning'])}</blockquote>"
                 if c.get("inledning") else "")
    return f"""<div class="citat">
{inledning}
<blockquote>{E(c['citat'])}</blockquote>
<div class="kalla">
<span class="chip">{E(etikett)}</span>
<span class="chip">Konfidens: {E(c.get('konfidens') or '—')}</span>
{punkt}sidan {c['sidnummer']} i
<a href="{E(c['dokument_url'])}" rel="noopener">{E(c['dokument_namn'] or 'beslutsdokumentet')}</a>
{' · OCR-tolkad text' if c.get('ocr') else ''}
· maskinellt verifierad ordagrant mot källdokumentet
</div>
</div>"""


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


def omradessida(o, manifest):
    datum = o["hamtningsdatum"]
    titel = f"Drönare i {o['namn']} — vad säger föreskrifterna?"
    citat = o.get("citat") or []
    if citat:
        svarslage = "Reglerat område — läs beslutet"
        svarklass = "svar-reglerat"
        svartext = (f"<p>{o['skyddstyp']} med myndighetsbeslut. Nedan citeras "
                    f"{len(citat)} punkter ur beslutsdokumenten ordagrant. Tjänsten "
                    "avgör inte vad de innebär för din flygning.</p>")
    else:
        svarslage = "Reglerat område — läs beslutet"
        svarklass = "svar-reglerat"
        svartext = ("<p>Ingen föreskrift som uttryckligen nämner luftfartyg hittades "
                    "i beslutet. Andra föreskrifter kan ändå vara relevanta — läs "
                    "beslutet.</p>")

    beskrivning = (f"{o['namn']} ({o['skyddstyp']}, {o['kommun']}). "
                   + (f"{len(citat)} ordagrant verifierade citat ur beslutsdokumenten."
                      if citat else
                      "Beslutsdokument länkade — inga citat om luftfartyg hittades.")
                   + f" Hämtat {datum}.")

    d = []
    d.append(f"<h1>{E(o['namn'])}</h1>")
    d.append(f'<p class="meta">{E(o["skyddstyp"])} · {E(o["kommun"] or o["lan"])} · '
             f'NVRID {E(o["nvrid"])} · Data hämtad {E(datum)}</p>')

    d.append(f'<div class="svar {svarklass}"><span class="svar-rubrik">{E(svarslage)}'
             f"</span>{svartext}</div>")

    if o.get("ocr"):
        d.append('<div class="ocr-varning">Texten är OCR-tolkad ur inskannat '
                 "original — kontrollera mot källdokumentet.</div>")

    d.append('<div id="minikarta" data-nvrid="' + E(o["nvrid"]) + '"></div>'
             if o.get("geometri") is not None else
             '<p class="meta">Området saknar kartgeometri i källdatan och visas '
             "därför inte som yta.</p>")

    if o.get("sasongsdata"):
        d.append("<h2>Tidsbegränsade föreskriftsområden</h2>")
        d.append("<p>Uppgifterna kommer ur Naturvårdsverkets register för det här "
                 "området. Vad de innebär framgår av beslutet.</p>")
        d.append('<div class="tabell-scroll"><table><thead><tr><th>Föreskriftstyp</th>'
                 "<th>Undertyp</th><th>Från</th><th>Till</th></tr></thead><tbody>")
        for f in o["sasongsdata"]:
            d.append("<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                E(f.get("foreskriftstyp") or "—"), E(f.get("foreskriftssubtyp") or "—"),
                E(f.get("franDatum") or "—"), E(f.get("tillDatum") or "—")))
        d.append("</tbody></table></div>")

    if citat:
        d.append("<h2>Ordagranna citat ur beslutsdokumenten</h2>")
        d.append(f"<p>{FLERA_BESLUT}</p>")
        per_dok = {}
        for c in citat:
            per_dok.setdefault((c["dokument_id"], c["dokument_namn"],
                                c["dokument_url"]), []).append(c)
        for (did, namn, url), lista in per_dok.items():
            d.append(f'<h3><a href="{E(url)}" rel="noopener">{E(namn or did)}</a></h3>')
            for c in lista:
                d.append(rendera_citat(c))
    else:
        d.append('<div class="svar svar-ingen"><span class="svar-rubrik">'
                 "Ingen föreskrift om luftfartyg hittad i de dokument tjänsten läst"
                 "</span><p>Ingen föreskrift som uttryckligen nämner luftfartyg "
                 "hittades i beslutet. Andra föreskrifter kan ändå vara relevanta — "
                 "läs beslutet.</p></div>")

    d.append("<h2>Beslutsdokument</h2>")
    dok = [x for x in (o.get("dokument") or []) if x.get("url")]
    if dok:
        d.append(f"<p>{FLERA_BESLUT}</p><ul>")
        for x in dok:
            extra = []
            if x.get("ocr"):
                extra.append("OCR-tolkad text")
            if x.get("sidor"):
                extra.append(f"{x['sidor']} sidor")
            if x.get("fel"):
                extra.append(f"kunde inte läsas: {x['fel']}")
            d.append('<li><a href="{}" rel="noopener">{}</a>{}</li>'.format(
                E(x["url"]), E(x.get("namn") or "Beslutsdokument"),
                f' <span class="avstand">({E(", ".join(extra))})</span>' if extra else ""))
        d.append("</ul>")
    else:
        d.append('<div class="svar svar-tacks-ej"><span class="svar-rubrik">'
                 "Beslutsdokument ej tillgängligt digitalt</span>"
                 "<p>Se områdets sida hos Naturvårdsverket.</p></div>")
    d.append(f'<p><a href="{E(o["sknat_url"])}" rel="noopener">Områdets sida i '
             "Naturvårdsverkets kartverktyg Skyddad natur</a></p>")

    if o.get("dokument_hoppade"):
        d.append("<details><summary>Dokument som inte lästs av tjänsten "
                 f"({len(o['dokument_hoppade'])} st)</summary><ul>")
        for x in o["dokument_hoppade"]:
            d.append('<li><a href="{}" rel="noopener">{}</a> — {}</li>'.format(
                E(x.get("url") or ""), E(x.get("namn") or "dokument"),
                E(x.get("orsak") or "")))
        d.append("</ul></details>")

    d.append("<h2>Uppgifter om området</h2>")
    d.append('<div class="tabell-scroll"><table><tbody>')
    rader = [
        ("Skyddstyp", o["skyddstyp"]),
        ("Kommun", o.get("kommun")),
        ("Län", o.get("lan")),
        ("Beslutsmyndighet", o.get("beslutsmyndighet")),
        ("Förvaltare", o.get("forvaltare")),
        ("Tillsynsmyndighet", o.get("tillsynsmyndighet")),
        ("Ursprungligt beslutsdatum", o.get("urspr_beslutsdatum")),
        ("Senaste gällandedatum", o.get("senaste_gallandedatum")),
        ("Areal (ha)", o.get("area_ha")),
        ("NVRID", o["nvrid"]),
        ("Geometrins källa", (o["geometri_kalla"]["tjanst"] + ", hämtad "
                              + o["geometri_kalla"]["hamtningsdatum"]
                              + ", licens " + o["geometri_kalla"]["licens"])),
        ("Geometrins svarshash (SHA-256)", o["geometri_kalla"]["svarshash_sha256"]),
    ]
    for k, v in rader:
        if v in (None, "", " "):
            continue
        d.append(f"<tr><th>{E(k)}</th><td>{E(str(v))}</td></tr>")
    d.append("</tbody></table></div>")

    d.append('<div class="svar svar-tacks-ej"><span class="svar-rubrik">'
             f"Denna källa täcks inte här</span><p>{LFV_RAD}</p></div>")
    d.append(f'<div class="ansvar">{ANSVARSTEXT}</div>')
    d.append('<p><a class="knapp sekundar" style="display:inline-block;'
             'text-decoration:none" href="{}" rel="noopener">Rapportera fel på '
             "den här sidan</a></p>".format(
                 E(issue_lank(o, svarslage, manifest["hamtningsdatum"]))))

    kanonisk = f"{BAS}/omrade/{o['nvrid']}-{o['slug']}/"
    schema = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [{
            "@type": "Question",
            "name": f"Vad säger föreskrifterna om drönare i {o['namn']}?",
            "acceptedAnswer": {
                "@type": "Answer",
                "text": (("Tjänsten citerar %d punkter ordagrant ur beslutsdokumenten "
                          "för %s. Den tolkar dem inte och ger inga klartecken. "
                          % (len(citat), o["namn"])) if citat else
                         ("Ingen föreskrift som uttryckligen nämner luftfartyg hittades "
                          "i beslutet för %s. Andra föreskrifter kan ändå vara "
                          "relevanta — läs beslutet. " % o["namn"]))
                        + "Luftrumsregler omfattas inte av tjänstens databas — "
                          "kontrollera alltid LFV:s drönarkarta före flygning.",
            },
        }],
    }
    return sidmall(titel=titel, beskrivning=beskrivning, kanonisk=kanonisk,
                   innehall="\n".join(d), data_datum=datum, schema=schema,
                   extra_head='\n<script defer src="/vendor/leaflet/leaflet.js"></script>'
                              '\n<script defer src="/assets/app.js"></script>'
                              f'\n<script>window.DK_LFV={json.dumps(CONFIG["lfv_wms"])};'
                              f'window.DK_ANSVARSTEXT={json.dumps(ANSVARSTEXT)};</script>')


def startsida(manifest, bboxindex):
    datum = manifest["hamtningsdatum"]
    karn = sum(1 for r in bboxindex["rader"] if r[4] not in
               ("vattenskyddsomrade", "landskapsbildsskydd", "biotopskydd"))
    d = []
    d.append(f"<h1>{E(NAMN)}</h1>")
    d.append("<p>Kartan visar skyddade naturområden i Skåne län vars myndighetsbeslut "
             "kan innehålla föreskrifter som berör drönarflygning. Tjänsten citerar "
             "föreskrifterna ordagrant och länkar till originalbeslutet. Den tolkar "
             "dem inte och ger inga klartecken.</p>")
    d.append('<div class="kartverktyg">'
             '<button class="knapp" id="positionsknapp" disabled>Kontrollera min position</button>'
             '<label class="toggle"><input type="checkbox" id="lfvtoggle" checked> '
             "Luftrumslager från LFV</label>"
             '<label class="toggle"><input type="checkbox" id="extralager"> '
             "Vattenskydd, landskapsbildsskydd och biotopskydd</label>"
             '<span class="laddstatus" id="laddstatus">Laddar områdesdata …</span>'
             "</div>")
    d.append('<div id="karta"></div>')
    d.append('<div id="positionssvar"></div>')
    d.append('<div class="svar svar-tacks-ej"><span class="svar-rubrik">'
             f"Denna källa täcks inte här</span><p>{LFV_RAD}</p></div>")
    d.append(f'<div class="ansvar">{ANSVARSTEXT}</div>')
    d.append("<h2>Vad tjänsten gör</h2><ul>"
             "<li>Visar myndigheternas egna områdesgränser — inga egenritade "
             "buffertzoner, cirklar eller uppskattade zoner.</li>"
             "<li>Citerar föreskrifter ordagrant med paragraf, sidnummer och länk "
             "till originaldokumentet.</li>"
             "<li>Varje citat är maskinellt kontrollerat mot källdokumentets text. "
             "Citat som inte matchar kastas — då visas bara länken.</li>"
             "<li>Svarar alltid i ett av tre lägen: <em>reglerat område — läs "
             "beslutet</em>, <em>ingen restriktion hittad i de källor tjänsten "
             "täcker</em>, eller <em>denna källa täcks inte här</em>.</li>"
             "</ul>")
    d.append(f"<p>Databasen omfattar {len(bboxindex['rader'])} områden i Skåne län, "
             f"varav {karn} i kärnlagret. Se <a href=\"/kallor/\">källor och "
             "täckning</a> för vad som ingår och vilka luckor som är kända.</p>")
    return sidmall(
        titel=f"{NAMN} — {CONFIG['site_tagline']}",
        beskrivning=("Karta över skyddade naturområden i Skåne län med ordagranna "
                     "citat ur myndigheternas beslutsföreskrifter om drönare och "
                     "luftfartyg. Gratis, reklamfri, utan spårning."),
        kanonisk=BAS + "/", innehall="\n".join(d), bred=True, data_datum=datum,
        extra_head='\n<script defer src="/vendor/leaflet/leaflet.js"></script>'
                   '\n<script defer src="/assets/app.js"></script>'
                   f'\n<script>window.DK_LFV={json.dumps(CONFIG["lfv_wms"])};'
                   f'window.DK_ANSVARSTEXT={json.dumps(ANSVARSTEXT)};</script>')


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
             "<th>Lager</th><th>Antal i Skåne</th><th>Varav med verifierade citat</th>"
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
                 "register returnerar inga objekt av den typen i Skåne län vid "
                 f"hämtningen {E(datum)}: <strong>{E(', '.join(tomma))}</strong>. "
                 "En tom kategori är ett besked om registret, inte ett besked om att "
                 "inget är reglerat.</p></div>")

    d.append("<h2>Kända luckor</h2><ul>")
    d.append("<li>Naturvårdsverkets punktlager <em>SkyddadePunkter</em> returnerar noll "
             f"objekt för Skåne län ({E(datum)}). Naturminnen i länet finns i "
             "ytlagret i stället.</li>")
    d.append("<li>Tillträdesförbud har enligt Naturvårdsverket kända ajourföringsluckor "
             "i Södermanlands, Örebro, Gotlands och Stockholms län. Skåne berörs inte "
             "av den uppgiften, och lagret returnerar objekt för Skåne — se tabellen "
             "ovan för antalet.</li>")
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
             '<a href="/data/verification-report.json">verification-report.json</a>, '
             '<a href="/data/manifest.json">manifest.json</a>.</p>')

    d.append("<h2>Ladda ned databasen</h2>")
    d.append('<p>Tjänstens databas publiceras under CC0. '
             '<a href="/data/areas.geojson">areas.geojson</a> (förenklad '
             'visningsgeometri), <a href="/data/bbox-index.json">bbox-index.json</a>, '
             "per-områdesfiler under <code>/data/omraden/{nvrid}.json</code> med "
             'oförenklad originalgeometri, samt <a href="/data/LICENSE">LICENSE</a> och '
             '<a href="/data/README.md">schemabeskrivning</a>.</p>')
    d.append(f'<div class="ansvar">{ANSVARSTEXT}</div>')
    return sidmall(titel=f"Källor och täckning — {NAMN}",
                   beskrivning=("Vilka rättskällor tjänsten täcker, varifrån data "
                                "kommer, hämtningsdatum, licenser och kända luckor."),
                   kanonisk=BAS + "/kallor/", innehall="\n".join(d), data_datum=datum)


def omsida(manifest, rapport):
    datum = manifest["hamtningsdatum"]
    d = ["<h1>Om tjänsten</h1>"]
    d.append("<p>Tjänsten är en vägvisare till källor. Den pekar och citerar — "
             "den bedömer inte, sammanfattar inte och ger inga klartecken.</p>")
    d.append("<h2>Tre svarslägen, aldrig ett fjärde</h2><ol>"
             "<li><strong>Reglerat område — läs beslutet.</strong> Med ordagranna "
             "citat och länk till originalbeslutet.</li>"
             "<li><strong>Ingen restriktion hittad i de källor tjänsten täcker.</strong> "
             "Ett besked om vad databasen innehåller, inte om din flygning.</li>"
             "<li><strong>Denna källa täcks inte här.</strong> Per rättskälla, "
             "till exempel luftrummet.</li></ol>")
    d.append("<h2>Så tas citaten fram</h2><ol>"
             "<li>Områdena hämtas ur Naturvårdsverkets naturvårdsregister via öppet "
             "WFS-API. Geometrin används exakt som myndigheten levererat den.</li>"
             "<li>Beslutsdokumenten laddas ned från Naturvårdsverkets dokumentarkiv. "
             "Text extraheras med pdftotext, och inskannade original OCR-tolkas med "
             "tesseract på svenska.</li>"
             "<li>Föreskriftspunkter som kan beröra flygning skärs ut som "
             "sammanhängande delsträngar ur dokumentets text. Ingen text skrivs om "
             "eller sätts ihop från flera ställen.</li>"
             "<li>Ett fristående kontrollskript strängmatchar varje citat mot "
             "källdokumentets text. Håller citatet inte måttet kastas det, och "
             "området visas i länk-läge.</li></ol>")
    d.append("<h2>Vad tjänsten aldrig gör</h2><ul>"
             "<li>Ritar aldrig egna zoner, buffertar eller cirklar kring något.</li>"
             "<li>Skriver aldrig om en föreskrift med egna ord.</li>"
             "<li>Ger aldrig ett samlat omdöme om huruvida en flygning är i sin "
             "ordning.</li>"
             "<li>Spårar aldrig besökare och visar aldrig reklam.</li></ul>")
    d.append("<h2>Hitta fel?</h2>")
    d.append(f'<p>Rapportera gärna: <a href="{E(CONFIG["issue_url"])}" rel="noopener">'
             "öppna ett ärende i tjänstens öppna kodförråd</a>. Varje bekräftat fel "
             "blir ett regressionstest innan det rättas.</p>")
    d.append(f'<div class="ansvar">{ANSVARSTEXT}</div>')
    return sidmall(titel=f"Om tjänsten — {NAMN}",
                   beskrivning=("Hur tjänsten hämtar, citerar och verifierar "
                                "föreskrifter ur myndighetsbeslut — och vad den "
                                "medvetet aldrig gör."),
                   kanonisk=BAS + "/om/", innehall="\n".join(d), data_datum=datum)


def omradeslista(omraden, manifest):
    datum = manifest["hamtningsdatum"]
    per_kommun = {}
    for o in omraden:
        for k in (o.get("kommun") or "Okänd kommun").split(","):
            per_kommun.setdefault(k.strip() or "Okänd kommun", []).append(o)
    d = ["<h1>Alla områden i Skåne län</h1>",
         f"<p>{len(omraden)} områden, sorterade per kommun. Varje sida citerar "
         "beslutets föreskrifter ordagrant och länkar till originaldokumentet.</p>"]
    for kommun in sorted(per_kommun):
        lista = sorted(per_kommun[kommun], key=lambda x: x["namn"] or "")
        d.append(f"<h2>{E(kommun)} ({len(lista)})</h2><ul class=\"lista-omraden\">")
        for o in lista:
            d.append('<li><a href="/omrade/{}-{}/">{}</a> <span class="avstand">{}{}</span></li>'
                     .format(E(o["nvrid"]), E(o["slug"]), E(o["namn"]), E(o["skyddstyp"]),
                             f" · {len(o['citat'])} verifierade citat" if o.get("citat")
                             else " · länk-läge"))
        d.append("</ul>")
    return sidmall(titel=f"Alla områden i Skåne län — {NAMN}",
                   beskrivning=(f"Register över {len(omraden)} skyddade områden i "
                                "Skåne län med föreskriftscitat och länk till "
                                "myndighetsbesluten."),
                   kanonisk=BAS + "/omraden/", innehall="\n".join(d), data_datum=datum)


def skriv(path, text):
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


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
    shutil.copytree(DATA, os.path.join(DIST, "data"))

    omraden = []
    for nvrid in sorted(manifest["objekt"]):
        o = read_json(os.path.join(DATA, "omraden", f"{nvrid}.json"))
        if o:
            omraden.append(o)

    skriv(os.path.join(DIST, "index.html"), startsida(manifest, bboxindex))
    skriv(os.path.join(DIST, "kallor", "index.html"),
          kallsida(manifest, rapport, omraden))
    skriv(os.path.join(DIST, "om", "index.html"), omsida(manifest, rapport))
    skriv(os.path.join(DIST, "omraden", "index.html"), omradeslista(omraden, manifest))

    urler = [BAS + "/", BAS + "/kallor/", BAS + "/om/", BAS + "/omraden/"]
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
