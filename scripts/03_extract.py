#!/usr/bin/env python3
"""Steg 3 — Extraktion av föreskriftspunkter som kan beröra flygning.

Metod: deterministisk segmentering + deterministisk klassificering.

Varje citat skärs ut som en *sammanhängande delsträng ur källdokumentets
extraherade text*. Ingen text skrivs om, sätts ihop från flera ställen eller
formuleras om. Det gör att citatet är ordagrant redan per konstruktion —
men det är inte beviset. Beviset är steg 4, som verifierar oberoende.

Klassificeringen (vilken av de fem klasserna en träff får) sker med
dokumenterade nyckelordsregler i stället för fritt LLM-omdöme. Skälet står i
DECISIONS.md: reglerna är reproducerbara, granskningsbara och ger samma svar
vid varje ombygge, vilket ett språkmodellsanrop inte gör. Klassificeringen är
dessutom bara en etikett — substansen som visas för användaren är citatet.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.common import CACHE, DATA, log, read_json, write_json
from lib.textnorm import normalisera

TEXT = os.path.join(CACHE, "text")
MAX_CITAT = 700          # tecken; kapas alltid vid meningsslut
MIN_CITAT = 25
NARHET = 200             # tecken mellan triggerord och förbudsuttryck

# ---------------------------------------------------------------- nyckelord
# Termer i fallande relevans enligt uppdraget.
# Starka luftfartstermer: kan bara syfta på farkoster. En träff är alltid värd
# att visa, även utan förbudsord i närheten.
LUFTFART_STARK = re.compile(
    r"obemannad[et]?\s+luftfartyg|obemannade\s+luftfartyg|"
    r"dr[oö]nar\w*|\bUAS\b|\bUAV\b|modellflyg\w*|modellplan|"
    r"luftfarkost\w*|luftfartyg\w*|luftballong\w*|ballongfl\w*|"
    r"\bflygplan\w*|helikopt\w*|flygfarkost\w*|radiostyrd\w*|"
    r"fj[aä]rrstyrd\w*\s+(?:farkost|flyg)\w*",
    re.I)
# Svaga termer: förekommer lika gärna i beskrivande text om fågellivet.
# Räknas bara i föreskriftssammanhang.
LUFTFART_SVAG = re.compile(r"\bflygning\w*|\bflyga\b|\bflyger\b|\bflygs\b", re.I)
LUFTFART = re.compile(LUFTFART_STARK.pattern + "|" + LUFTFART_SVAG.pattern, re.I)
START_LANDNING = re.compile(r"\bstart\w*\b|\blanda\b|\blandning\w*|\blandar\b|"
                            r"\bstiga\s+upp\b|\bl[aä]tta\b", re.I)
MOTOR = re.compile(r"motordriv\w*|motorfordon\w*|\bmotor\b|\bfordon\b|"
                   r"terr[aä]ngfordon\w*|\bfarkost\w*", re.I)
STORNING = re.compile(
    r"st[oö]r\w*|oroa\w*|skr[aä]mma\w*|f[oö]rf[oö]lja\w*|ofreda\w*|"
    r"avsiktligt\s+n[aä]rma", re.I)
DJURLIV = re.compile(r"djurliv\w*|f[aå]gelliv\w*|f[aå]glar\w*|\bf[aå]gel\w*|"
                     r"h[aä]ckn\w*|boplats\w*|\bbon\b|\bs[aä]l\w*|vilda\s+djur|"
                     r"\bdjur\w*|yngel|kolonier", re.I)
TILLTRADE = re.compile(r"tilltr[aä]d\w*|bet[rä]äda\w*|\bbetr[aä]da\w*|"
                       r"\bvistas\b|\bfärdas\b|\bframf[oö]ra\b", re.I)
FORBUD = re.compile(r"f[oö]rbjud\w*|f[oö]rbud\w*|f[aå]r\s+inte|f[aå]r\s+ej|"
                    r"[aä]r\s+inte\s+till[aå]t\w*|inte\s+till[aå]tet|"
                    r"utan\s+l[aä]nsstyrelsens\s+till[aå]tels|kr[aä]vs\s+tillst[aå]nd|"
                    r"undantag\s+fr[aå]n", re.I)

# Uttryck som ger falska träffar och därför utesluter en kandidat helt.
BRUS = re.compile(
    r"^\s*(inneh[aå]ll|bilaga|sida|postadress|bes[oö]ksadress|telefon|"
    r"e-post|webb|org\.?\s*nr)\b", re.I)

# Avsnitt som per definition INTE är föreskrifter. Ett beslut återger ofta sina
# egna föreskrifter i skälen — "Enligt gällande föreskrifterna är det bland
# annat förbjudet att…" — och den återgivningen ska inte visas som föreskrift.
# Den riktiga punkten citeras ändå från föreskriftsavsnittet. Matchningen görs
# på segmentets FÖRSTA rad, alltså på avsnittsrubriken, inte på brödtexten.
SKAL_RUBRIK = re.compile(
    r"^\s*(sk[aä]l(en)?\s+(f[oö]r|till)\b|"
    r"[aä]rendets\s+handl[aä]ggning|"
    r"l[aä]nsstyrelsens\s+(bed[oö]mning|bem[oö]tande|[oö]verv[aä]gand)|"
    r"beskrivning\s+av\s+omr[aå]det|"
    r"yttranden?\b|inkomna\s+synpunkter|"
    r"hur\s+man\s+[oö]verklagar|upplysningar\b|"
    r"l[aä]nsstyrelsen\s*$|"
    r"kopia\s+till|s[aä]ndlista)", re.I)

KLASSER = {
    "uttryckligt-luftfartygsforbud": "uttryckligt-luftfartygsförbud",
    "start-landningsforbud": "start-landningsförbud",
    "motorfordon-mojligen-relevant": "motorfordon-möjligen-relevant",
    "storningsforbud-djurliv": "störningsförbud-djurliv",
    "annat-las-beslutet": "annat-läs-beslutet",
}

# ------------------------------------------------------------- segmentering
# Punktmarkörer i svenska myndighetsbeslut: "12.", "12)", "a)", "§ 5", "C 3".
PUNKT = re.compile(
    r"(?m)^[ \t]{0,12}("
    r"\d{1,2}\s*[.)]"          # 1.  1)
    r"|[a-zA-ZåäöÅÄÖ]\s*[.)]"  # a)  A.
    r"|§\s*\d+"                # § 5
    r"|[A-D]\s*\d{1,2}[.)]"    # C 3.
    r")\s+")
MENING = re.compile(r"(?<=[.!?:;])\s+")


def segmentera(sidtext: str):
    """Dela en sida i (etikett, delsträng, startoffset).

    Segmenten är alltid sammanhängande delsträngar av `sidtext` — det är det
    som gör citaten ordagranna per konstruktion.
    """
    markers = list(PUNKT.finditer(sidtext))
    segment = []
    if markers:
        for i, m in enumerate(markers):
            start = m.start()
            slut = markers[i + 1].start() if i + 1 < len(markers) else len(sidtext)
            segment.append((m.group(1).strip(), sidtext[start:slut], start))
        forsta = markers[0].start()
        if forsta > 0:
            segment.insert(0, (None, sidtext[:forsta], 0))
    else:
        pos = 0
        for bit in re.split(r"\n\s*\n", sidtext):
            segment.append((None, bit, pos))
            pos += len(bit) + 2
    return segment


NUMRERAD = re.compile(r"^\d{1,2}\s*[.)]$")


HANGANDE_AVSTAVNING = re.compile(r"\s\S*\w[-­]$")


def trim_avstavning(text: str) -> str:
    """Kapa bort ett avslutande avstavat ord.

    En sida kan sluta mitt i ett ord: "…eller styr-" med fortsättningen på
    nästa sida. Ett citat som slutar så är dels obegripligt för läsaren, dels
    en källa till falska verifieringsfel — normaliseringen slår ihop
    avstavningen över sidbrytningen, så samma sträng finns på sidan men inte i
    hela dokumentet. Ordet klipps därför bort. Resultatet är fortfarande en
    sammanhängande delsträng, eftersom bara slutet kapas.
    """
    m = HANGANDE_AVSTAVNING.search(text)
    while m:
        text = text[:m.start()].rstrip()
        m = HANGANDE_AVSTAVNING.search(text)
    return text


def kapa(text: str, start_offset: int, numrerad: bool = False):
    """Kapa ett segment till läsbar citatlängd utan att bryta ordagrannheten.

    Returnerar (citat, offset). Citatet är alltid en delsträng av segmentet
    med bibehållen början — bara slutet kapas, och då vid meningsgräns.
    """
    text = text.rstrip()
    # Trimma inledande blanksteg men behåll offset i synk.
    lead = len(text) - len(text.lstrip())
    text = text[lead:]
    start_offset += lead
    # En numrerad föreskriftspunkt är ett stycke. Löper segmentet vidare förbi
    # ett styckebrott har nästa punktmarkör inte hittats (vanligt i OCR-text) —
    # kapa då vid styckebrottet i stället för att dra med efterföljande brödtext.
    # Gäller bara numrerade punkter: avsnittsrubriker som "C." följs ofta av
    # föreskrifterna efter ett styckebrott, och där är brytningen inte ett slut.
    if numrerad:
        for styckebrott in re.finditer(r"\n[ \t]*\n", text):
            # Hoppa över brott direkt efter punktmarkören ("13.\n\n…") — där
            # har textextraktionen tappat radbrytningen, inte punkten slutat.
            if styckebrott.start() >= MIN_CITAT:
                text = text[:styckebrott.start()].rstrip()
                break
    if len(text) <= MAX_CITAT:
        return trim_avstavning(text), start_offset
    bit = text[:MAX_CITAT]
    delar = list(MENING.finditer(bit))
    if delar:
        bit = bit[:delar[-1].start() + 1]
    else:
        bit = bit[:bit.rfind(" ")] if " " in bit else bit
    return trim_avstavning(bit.rstrip()), start_offset


INLEDNING_MAX_AVSTAND = 3500   # tecken bakåt på samma sida
INLEDNING_MIN = 15
INLEDNING_MAX = 400


def hitta_inledning(sidtext: str, segment_start: int, foregaende_sida: str = None):
    """Hitta föreskriftsblockets inledande rad, t.ex. "Det är förbjudet att:".

    I svenska reservatsbeslut står förbudsordet i blockets rubrik och inte i de
    numrerade punkterna: rubriken "Det är förbjudet att inom reservatet:" följs
    av "6. framföra cykel eller motordrivet fordon…". Utan rubriken saknar
    punkten både sitt förbud och sitt sammanhang.

    Rubriken hämtas som en EGEN sammanhängande delsträng ur samma sidtext och
    verifieras separat i steg 4 — den limmas aldrig ihop med citatet till en ny
    textmassa. Returnerar (text, offset) eller (None, None).
    """
    # Rubriken måste både INNEHÅLLA ett förbuds-/tillståndsuttryck och SLUTA
    # som en inledning till en uppräkning — alltså på kolon, eller på "att"
    # (med eller utan kolon). Enbart innehållskravet räcker inte: en vanlig
    # mening som "…möjligheten att lägga ett helårs beträdnadsförbud på
    # revlarna" innehåller ordet förbud utan att vara en rubrik, och om den
    # accepteras som föreskriftsinledning börjar brödtext klassas som
    # föreskrift. Avslutskravet skiljer rubrik från brödtext.
    INLEDNINGSORD = re.compile(
        r"f[oö]rbjud|f[oö]rbud|f[aå]r\s+inte|f[aå]r\s+ej|g[aä]ller\s+f[oö]ljande|"
        r"till[aå]ts\s+inte|till[aå]tels|tillst[aå]nd|kr[aä]v(?:er|s)", re.I)
    INLEDNINGSAVSLUT = re.compile(r"(?::|\batt)\s*:?\s*$", re.I)

    def sok(fonster, fonster_start):
        """Sista raden i fönstret som ser ut som en föreskriftsinledning.

        Rubriken slutar ofta med kolon ("Det är förbjudet att:") men långt
        ifrån alltid — "Utöver föreskrifter och förbud i andra lagar och
        författningar är det förbjudet att" följt direkt av punkt 1 är minst
        lika vanligt. En tidigare version krävde kolon, och tappade då
        förbudet för hela listan under en sådan rubrik. Rader som själva är
        numrerade punkter utesluts, liksom rubrikrader av typen "Bilaga 2:".

        Rubriken kan sträcka sig över två rader; därför prövas både raden
        ensam och raden ihop med den föregående — som en sammanhängande
        delsträng, aldrig hopskarvad.
        """
        rader, pos, kandidater = [], 0, []
        for rad in fonster.split("\n"):
            rader.append((rad, pos))
            pos += len(rad) + 1
        for i, (rad, radpos) in enumerate(rader):
            text = rad.strip()
            if PUNKT.match(rad) or not text:
                continue
            for start_i in (i, i - 1):
                if start_i < 0:
                    continue
                bit = fonster[rader[start_i][1]:radpos + len(rad)]
                kandidat = bit.strip()
                if (INLEDNING_MIN <= len(kandidat) <= INLEDNING_MAX
                        and INLEDNINGSORD.search(kandidat)
                        and INLEDNINGSAVSLUT.search(kandidat)
                        and not PUNKT.match(bit)):
                    offset = (fonster_start + rader[start_i][1]
                              + (len(bit) - len(bit.lstrip())))
                    kandidater.append((kandidat, offset))
                    break
        return kandidater[-1] if kandidater else (None, None)

    start = max(0, segment_start - INLEDNING_MAX_AVSTAND)
    text, offset = sok(sidtext[start:segment_start], start)
    if text:
        return text, offset, 0

    # Långa föreskriftslistor löper över sidbrytningen: rubriken kan stå på
    # föregående sida. Inledningen hämtas då därifrån, som en egen delsträng
    # av den sidan, och verifieras mot den sidan.
    if foregaende_sida:
        start = max(0, len(foregaende_sida) - INLEDNING_MAX_AVSTAND)
        text, offset = sok(foregaende_sida[start:], start)
        if text:
            return text, offset, -1
    return None, None, 0


def klassificera(citat: str, inledning: str = None, numrerad: bool = False):
    """Deterministisk klassificering + konfidens.

    Prioritet: uttrycklig luftfart > start/landning > motordrivet fordon >
    störning av djurliv > annat. Konfidens är 'hög' när segmentet eller dess
    föreskriftsinledning innehåller ett förbuds- eller tillståndsuttryck,
    annars 'medel' — den säger något om hur säkert segmentet är en föreskrift,
    aldrig något om vad föreskriften betyder.
    """
    stark = bool(LUFTFART_STARK.search(citat))
    startland = bool(START_LANDNING.search(citat))
    forbud_i_citat = bool(FORBUD.search(citat))
    forbud_i_inledning = bool(inledning and FORBUD.search(inledning))
    forbud = forbud_i_citat or forbud_i_inledning

    def nara_forbud(trigger):
        """Sant om ett förbudsuttryck står nära triggerordet — eller kommer ur
        föreskriftsinledningen.

        Utan närhetskravet klassificeras uppräkningar av angränsande lagstiftning
        som föreskrifter: en punktlista där ett stycke nämner "förbud mot körning
        i terräng" och ett annat nämner "flygning med flygskärm" har båda orden i
        samma segment utan att ha något med varandra att göra.
        """
        if forbud_i_inledning:
            return True
        for m in trigger.finditer(citat):
            fonster = citat[max(0, m.start() - NARHET):m.end() + NARHET]
            if FORBUD.search(fonster):
                return True
        return False

    def traffade(*monster):
        """De ord i citatet som gjorde att det valdes ut.

        Poängen är läsbarhet utan tolkning. Ett citat är ofta en halv sida
        beslutstext, och den som står på en åker vill se på en sekund varför
        just den texten kom upp. Orden märks ut i gränssnittet — det är ett
        faktum om urvalet, inte ett påstående om vad föreskriften betyder.

        Två spärrar mot att märkningen slutar betyda något:

        * Högst tre ÄMNESORD (det som utlöste klassen) och högst ett
          FÖRBUDSORD. Förbudsordet finns i så gott som varje citat per
          konstruktion — märks allt blir hela stycket gult och signalen dör.
        * Längst först, så att överlappande träffar ("obemannat luftfartyg"
          och "luftfartyg") märks ut som den längre frasen.
        """
        amne = []
        for m in monster:
            for tr in m.finditer(citat):
                o = tr.group(0)
                if o not in amne:
                    amne.append(o)
        amne = sorted(amne, key=len, reverse=True)[:3]
        forbudsord = [m.group(0) for m in FORBUD.finditer(citat)][:1]
        return amne + [o for o in forbudsord if o not in amne]

    if stark:
        klass = "start-landningsförbud" if startland else "uttryckligt-luftfartygsförbud"
        ord_ = traffade(LUFTFART_STARK, START_LANDNING)
    elif not forbud:
        # Utan förbuds- eller tillståndsuttryck i vare sig punkten eller dess
        # föreskriftsinledning är detta med stor sannolikhet beskrivande text
        # ur skötselplan eller områdesbeskrivning, inte en föreskrift. Att
        # visa sådan text som föreskrift vore ett trubbigt falsklarm (R8).
        return None, None, []
    elif LUFTFART_SVAG.search(citat) and nara_forbud(LUFTFART_SVAG):
        klass = "start-landningsförbud" if startland else "uttryckligt-luftfartygsförbud"
        ord_ = traffade(LUFTFART_SVAG, START_LANDNING)
    elif MOTOR.search(citat) and nara_forbud(MOTOR):
        klass = "motorfordon-möjligen-relevant"
        ord_ = traffade(MOTOR)
    elif (STORNING.search(citat) and DJURLIV.search(citat)
          and nara_forbud(STORNING)):
        klass = "störningsförbud-djurliv"
        ord_ = traffade(STORNING, DJURLIV)
    elif TILLTRADE.search(citat) and nara_forbud(TILLTRADE):
        klass = "annat-läs-beslutet"
        ord_ = traffade(TILLTRADE)
    else:
        return None, None, []

    if forbud_i_citat:
        konfidens = "hög"
    elif forbud_i_inledning:
        konfidens = "medel"
    elif numrerad:
        # En numrerad punkt i ett myndighetsbeslut är strukturellt en
        # föreskriftspunkt, även när förbudsordet står i en rubrik som
        # textextraktionen inte lyckats fånga.
        konfidens = "medel"
    else:
        konfidens = "låg"
    return klass, konfidens, ord_


def extrahera_dokument(dok):
    """Returnerar lista med träffar för ett dokument."""
    did = dok.get("dokument_id")
    if not did:
        return []
    path = os.path.join(TEXT, did + ".txt")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        sidor = fh.read().split("\f")
    traffar, sedda = [], set()
    for sidnr, sidtext in enumerate(sidor, 1):
        if not sidtext.strip():
            continue
        for etikett, segtext, offset in segmentera(sidtext):
            if len(segtext.strip()) < MIN_CITAT or BRUS.match(segtext):
                continue
            if SKAL_RUBRIK.match(segtext.strip().split("\n")[0]):
                continue
            citat, cit_offset = kapa(
                segtext, offset, numrerad=bool(etikett and NUMRERAD.match(etikett)))
            if len(citat) < MIN_CITAT:
                continue
            numrerad = bool(etikett and NUMRERAD.match(etikett))
            inledning, inl_offset, sidforskjutning = hitta_inledning(
                sidtext, cit_offset, sidor[sidnr - 2] if sidnr > 1 else None)
            # Klassificeringen läser inledningen som sammanhang, men det som
            # visas som citat är fortfarande bara citatet.
            klass, konfidens, traffade_ord = klassificera(citat, inledning, numrerad)
            if not klass:
                continue
            nyckel = normalisera(citat)[:200]
            if nyckel in sedda:
                continue
            sedda.add(nyckel)
            traffar.append({
                "citat": citat,
                "inledning": inledning,
                "inledning_offset": inl_offset,
                "inledning_sidnummer": (sidnr + sidforskjutning) if inledning else None,
                "punkt": etikett,
                "sidnummer": sidnr,
                "teckenoffset_pa_sidan": cit_offset,
                "klassificering": klass,
                "konfidens": konfidens,
                "traffade_ord": traffade_ord,
                "dokument_id": did,
                "dokument_namn": dok.get("namn"),
                "dokument_url": dok.get("url"),
                "dokument_sha256": dok.get("sha256"),
                "ocr": bool(dok.get("ocr")),
            })
    return traffar


def main():
    manifest = read_json(os.path.join(DATA, "manifest.json"))
    if manifest is None:
        sys.exit("Kör scripts/01_ingest.py och 02_fetch_docs.py först.")

    resultat, stats = {}, {"objekt": 0, "med_traff": 0, "traffar": 0, "per_klass": {}}
    for nvrid, rec in sorted(manifest["objekt"].items()):
        stats["objekt"] += 1
        traffar = []
        for dok in rec.get("dokument") or []:
            traffar.extend(extrahera_dokument(dok))
        if traffar:
            stats["med_traff"] += 1
            stats["traffar"] += len(traffar)
            for t in traffar:
                k = t["klassificering"]
                stats["per_klass"][k] = stats["per_klass"].get(k, 0) + 1
        resultat[nvrid] = traffar

    write_json(os.path.join(DATA, "extraktion.json"),
               {"schema_version": 1, "statistik": stats, "traffar": resultat})
    log(f"Steg 3 klart: {stats}")


if __name__ == "__main__":
    main()
