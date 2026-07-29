#!/usr/bin/env python3
"""Steg 11 — kommunala föreskrifter, kommun för kommun.

Varför registret ser ut så här
------------------------------
Frågan var: reglerar kommuner drönarflygning? Två skånska kommuner utan träff
räckte inte som underlag för att stryka frågan — Daniel hade rätt i det. Men
290 kommuners PDF:er går inte att skrapa på ett vettigt sätt heller, och ett
generellt påstående ("kommuner reglerar inte drönare") vore precis den sortens
obelagda utsaga tjänsten finns till för att slippa.

Lösningen är att INTE generalisera. Registret säger vad som faktiskt
kontrollerats, när, i vilket dokument och med vilken sha256. Kommuner som inte
är kontrollerade redovisas som just det — inte som fria från regler. Listan kan
växa en kommun i taget utan att något påstås om resten.

Vad som är belagt strukturellt
------------------------------
En kommun får inte reglera LUFTRUMMET. Det är Transportstyrelsen som beslutar
om geografiska UAS-zoner enligt artikel 15 i EU 2019/947, och en lokal
ordningsföreskrift får inte strida mot nationell lag.

Vad kommunen däremot råder över är MARKEN. Ett parkreglemente eller en
badplatsföreskrift kan träffa lyft och landning utan att nämna ordet drönare.
Det är därför sökningen inte bara letar efter "drönare" utan också efter de
markrelaterade uttryck som skulle kunna fånga en start eller landning.
"""
from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.common import (CACHE, DATA, ensure_dir, fetch, log, sha256_bytes,
                        today, write_json)

# METODEN, och varför den ändrades
# ---------------------------------
# Första versionen sökte efter drönartermer INUTI ett dokument per kommun —
# de allmänna lokala ordningsföreskrifterna — och drog av noll träffar
# slutsatsen "Höganäs: ingenting om drönare". Det var sant om dokumentet och
# falskt om kommunen: Höganäs publicerar "Riktlinjer för drönarverksamhet",
# som en enkel sökning på kommunens webbplats hittar direkt. Daniel hittade
# den på en sökning; jag hade sökt på fel sak.
#
# Registret listar därför dokument, inte kommuner, och varje dokument har en
# `typ`:
#
#   foreskrift  — bindande för allmänheten (ordningsföreskrift m.m.)
#   intern      — kommunens egna rutiner för SIN verksamhet, binder inte dig
#
# Klassningen bygger på dokumentets egen beskrivning av sig självt, som
# lagras i `sjalvbeskrivning` så att den går att kontrollera.
KOMMUNER = [
    ("Höganäs", "Allmänna lokala ordningsföreskrifter KFS 2026:11",
     "https://www.hoganas.se/download/18.3d4a511a188bd9ee1364e17/1782882140176/"
     "Allm%C3%A4nna%20lokala%20ordningsf%C3%B6reskrifter%20f%C3%B6r%20"
     "H%C3%B6gan%C3%A4s%20kommun%20KFS%202026:11.pdf", "pdf", "foreskrift"),
    ("Höganäs", "Riktlinjer för drönarverksamhet i Höganäs kommun",
     "https://www.hoganas.se/download/18.7a54f188194d866594e4dce3/1740123018291/"
     "Riktlinjer%20f%C3%B6r%20dr%C3%B6narverksamhet.pdf", "pdf", "intern"),
    ("Helsingborg", "Allmänna lokala ordningsföreskrifter, dec 2024",
     "https://media.helsingborg.se/uploads/networks/4/sites/141/2024/12/"
     "allm-lok-ordn-foreskr-m-bilaga-1_4.pdf", "pdf", "foreskrift"),
    ("Stockholm", "Allmänna lokala ordningsföreskrifter",
     "https://start.stockholm/om-stockholms-stad/politik-och-demokrati/"
     "styrdokument/allmanna-lokala-ordningsforeskrifter-i-stockholm/", "html", "foreskrift"),
    ("Eskilstuna", "Lokala ordningsföreskrifter",
     "https://www.eskilstuna.se/kommun-och-politik/trygg-och-saker-stad/"
     "lokala-ordningsforeskrifter", "html", "foreskrift"),
    ("Sandviken", "Lokala ordningsföreskrifter",
     "https://sandviken.se/kommunpolitik/lokalaforeskrifterochstyrdokument/"
     "lokalaordningsforeskrifter.516.html", "html", "foreskrift"),
    ("Oxelösund", "Lokala ordningsföreskrifter",
     "https://www.oxelosund.se/trafik-och-infrastruktur/"
     "lokala-ordningsforeskrifter", "html", "foreskrift"),
    ("Avesta", "Lokala ordningsföreskrifter",
     "https://avesta.se/stod-och-omsorg/trygghet-och-sakerhet/brottsforebyggande/"
     "lokala-ordningsforeskrifter/", "html", "foreskrift"),
    ("Östhammar", "Lokala ordningsföreskrifter",
     "https://www.osthammar.se/sv/dokument/ordningsregler-och-foreskrifter/"
     "lokala-ordningsforeskrifter/", "html", "foreskrift"),
    ("Bollnäs", "Lokala ordningsföreskrifter",
     "https://bollnas.se/kommun-och-politik/kommunfakta/"
     "regler-och-styrande-dokument/lokala-ordningsforeskrifter", "html", "foreskrift"),
    ("Stockholm", "Så arbetar staden med drönare",
     "https://start.stockholm/om-stockholms-stad/sa-arbetar-staden/dronare/",
     "html", "intern"),
]

# Luftfartstermer: skulle en föreskrift nämna drönare uttryckligen.
LUFTFART = re.compile(
    r"dr[oö]nar\w*|obemannad\w*|luftfartyg\w*|luftfarkost\w*|modellflyg\w*|"
    r"radiostyrd\w*|\bUAS\b|flygning\w*", re.I)

# Markrelaterade uttryck: kommunen får inte reglera luftrummet men RÅDER ÖVER
# MARKEN, och ett förbud mot "motordriven farkost" på en badstrand kan träffa
# ett lyft utan att nämna drönare.
MARK = re.compile(
    r"motordriv\w*\s+farkost|motordrivet\s+fordon|\bfarkost\w*|"
    r"uppst[aä]llning\s+av\s+anordning|anordning\w*\s+p[aå]\s+offentlig", re.I)

MIN_TECKEN = 8000        # kortare än så är sidan en länksida, inte föreskriften


def text_ur_html(ra: bytes) -> str:
    t = ra.decode("utf-8", "replace")
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", t, flags=re.S | re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t)))


def text_ur_pdf(ra: bytes) -> str:
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "k.pdf")
        with open(p, "wb") as fh:
            fh.write(ra)
        ut = os.path.join(d, "k.txt")
        subprocess.run(["pdftotext", "-layout", p, ut], check=True,
                       capture_output=True, timeout=180)
        with open(ut, encoding="utf-8", errors="replace") as fh:
            return re.sub(r"\s+", " ", fh.read())


def utdrag(text, m, marg=110):
    """Ordagrant utdrag runt en träff, så att den går att bedöma direkt."""
    return text[max(0, m.start() - marg):m.end() + marg].strip()


def main():
    katalog = ensure_dir(os.path.join(CACHE, "kommunala"))
    kommuner = {}
    for namn, dokumentnamn, url, form, typ in KOMMUNER:
        post = kommuner.setdefault(namn, {"dokument": []})
        filnamn = re.sub(r"\W+", "_", namn + "-" + dokumentnamn)[:80]
        fil = os.path.join(katalog, filnamn + (".pdf" if form == "pdf" else ".html"))
        try:
            if os.path.exists(fil):
                with open(fil, "rb") as fh:
                    ra = fh.read()
            else:
                ra = fetch(url, min_interval=1.5, timeout=180)
                with open(fil, "wb") as fh:
                    fh.write(ra)
            text = (text_ur_pdf if form == "pdf" else text_ur_html)(ra)
        except Exception as exc:  # noqa: BLE001
            post["dokument"].append({"namn": dokumentnamn, "url": url, "typ": typ,
                                     "status": "kunde-inte-hamtas",
                                     "fel": str(exc)[:120]})
            log(f"  {namn}: FEL — {exc}")
            continue

        luft = [utdrag(text, m) for m in LUFTFART.finditer(text)][:6]
        mark = [utdrag(text, m) for m in MARK.finditer(text)][:4]
        fullstandig = len(text) >= MIN_TECKEN or typ == "intern"
        post["dokument"].append({
            "namn": dokumentnamn,
            "url": url,
            "typ": typ,
            "status": "kontrollerad" if fullstandig else "endast-lanksida",
            "sha256": sha256_bytes(ra),
            "tecken": len(text),
            "kontrollerad": today(),
            "luftfartstraffar": luft,
            "marktraffar": mark,
        })
        flagga = "✓" if fullstandig else "!"
        log(f"  {flagga} {namn} / {dokumentnamn[:44]}: {len(text)} tecken, "
            f"{len(luft)} luftfartsträffar, {len(mark)} markträffar [{typ}]")

    # En kommun räknas som kontrollerad när dess FÖRESKRIFT är läst i sin
    # helhet. Interna riktlinjer är värdefulla att visa men binder inte
    # allmänheten, och de får därför inte ensamma göra en kommun "kontrollerad".
    for namn, post in kommuner.items():
        foreskrifter = [d for d in post["dokument"]
                        if d.get("typ") == "foreskrift"
                        and d.get("status") == "kontrollerad"]
        traffar = [d for d in post["dokument"]
                   if d.get("luftfartstraffar") or d.get("marktraffar")]
        post["status"] = "kontrollerad" if foreskrifter else "ofullstandig"
        post["har_dronardokument"] = bool(
            [d for d in post["dokument"] if d.get("luftfartstraffar")])
        post["antal_traffar"] = len(traffar)

    kontrollerade = [k for k, v in kommuner.items() if v["status"] == "kontrollerad"]
    med_dok = [k for k, v in kommuner.items() if v["har_dronardokument"]]

    write_json(os.path.join(DATA, "kommunala-foreskrifter.json"), {
        "schema_version": 2,
        "hamtningsdatum": today(),
        "beskrivning": (
            "Kommunala dokument som rör drönare, kontrollerade en kommun i "
            "taget. Registret generaliserar INTE: en kommun som inte står här "
            "är inte kontrollerad, vilket är något annat än att den saknar "
            "regler."),
        "metodnot": (
            "Första versionen sökte drönartermer inuti ETT dokument per kommun "
            "och drog av noll träffar slutsatsen att kommunen saknade regler. "
            "Det var fel sak att söka på: Höganäs publicerar Riktlinjer för "
            "drönarverksamhet, som en sökning på kommunens webbplats hittar "
            "direkt. Registret listar därför dokument, inte kommuner."),
        "typnot": (
            "typ=foreskrift binder allmänheten. typ=intern är kommunens egna "
            "rutiner för sin egen verksamhet och binder inte en privat pilot — "
            "men den kan innehålla uppgifter som är värda att läsa ändå."),
        "strukturell_not": (
            "En kommun får inte reglera luftrummet — det är Transportstyrelsen "
            "som beslutar om geografiska UAS-zoner enligt artikel 15 i EU "
            "2019/947, och en lokal föreskrift får inte strida mot nationell "
            "lag. Kommunen råder däremot över marken, vilket kan träffa lyft "
            "och landning."),
        "antal_kontrollerade": len(kontrollerade),
        "antal_med_dronardokument": len(med_dok),
        "kommuner": kommuner,
    })
    log(f"Steg 11 klart: {len(kommuner)} kommuner, {len(kontrollerade)} med läst "
        f"föreskrift, {len(med_dok)} med drönardokument ({', '.join(med_dok)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
