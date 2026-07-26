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

# ---------------------------------------------------------------- nyckelord
# Termer i fallande relevans enligt uppdraget.
LUFTFART = re.compile(
    r"obemannad[et]?\s+luftfartyg|obemannade\s+luftfartyg|"
    r"dr[oö]nar\w*|\bUAS\b|\bUAV\b|modellflyg\w*|modellplan|"
    r"luftfarkost\w*|luftfartyg\w*|luftballong\w*|ballongfl\w*|"
    r"\bflygplan\w*|helikopt\w*|flygfarkost\w*|radiostyrd\w*|"
    r"fj[aä]rrstyrd\w*\s+(?:farkost|flyg)\w*|\bflygning\w*|\bflyga\b|\bflyger\b",
    re.I)
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


def kapa(text: str, start_offset: int):
    """Kapa ett segment till läsbar citatlängd utan att bryta ordagrannheten.

    Returnerar (citat, offset). Citatet är alltid en delsträng av segmentet
    med bibehållen början — bara slutet kapas, och då vid meningsgräns.
    """
    text = text.rstrip()
    # Trimma inledande blanksteg men behåll offset i synk.
    lead = len(text) - len(text.lstrip())
    text = text[lead:]
    start_offset += lead
    if len(text) <= MAX_CITAT:
        return text, start_offset
    bit = text[:MAX_CITAT]
    delar = list(MENING.finditer(bit))
    if delar:
        bit = bit[:delar[-1].start() + 1]
    else:
        bit = bit[:bit.rfind(" ")] if " " in bit else bit
    return bit.rstrip(), start_offset


def klassificera(citat: str):
    """Deterministisk klassificering + konfidens.

    Prioritet: uttrycklig luftfart > start/landning > motordrivet fordon >
    störning av djurliv > annat. Konfidens är 'hög' när segmentet också
    innehåller ett förbuds- eller tillståndsuttryck, annars 'medel' — den
    säger något om hur säkert segmentet är en föreskrift, aldrig något om
    vad föreskriften betyder.
    """
    luft = bool(LUFTFART.search(citat))
    startland = bool(START_LANDNING.search(citat))
    forbud = bool(FORBUD.search(citat))
    if luft and startland:
        klass = "start-landningsförbud"
    elif luft:
        klass = "uttryckligt-luftfartygsförbud"
    elif MOTOR.search(citat) and (startland or forbud):
        klass = "motorfordon-möjligen-relevant"
    elif STORNING.search(citat) and DJURLIV.search(citat):
        klass = "störningsförbud-djurliv"
    elif TILLTRADE.search(citat) and forbud:
        klass = "annat-läs-beslutet"
    else:
        return None, None
    return klass, ("hög" if forbud else "medel")


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
            citat, cit_offset = kapa(segtext, offset)
            if len(citat) < MIN_CITAT:
                continue
            klass, konfidens = klassificera(citat)
            if not klass:
                continue
            nyckel = normalisera(citat)[:200]
            if nyckel in sedda:
                continue
            sedda.add(nyckel)
            traffar.append({
                "citat": citat,
                "punkt": etikett,
                "sidnummer": sidnr,
                "teckenoffset_pa_sidan": cit_offset,
                "klassificering": klass,
                "konfidens": konfidens,
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
