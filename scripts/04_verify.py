#!/usr/bin/env python3
"""Steg 4 — Verifiering. Den hårda grinden (Regel R7).

Ett deterministiskt skript — ingen språkmodell inblandad — strängmatchar
varje extraherat citat mot källdokumentets extraherade text. Efter
normaliseringen i lib/textnorm.py krävs EXAKT förekomst. Miss ⇒ citatet
kasseras och objektet degraderas till länk-läge.

Verifieringen körs från noll vid varje bygge, även för oförändrade objekt.
Det är billigt och skyddar mot regressioner i normaliseringen.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.common import CACHE, DATA, log, now_iso, read_json, write_json
from lib.textnorm import normalisera

TEXT = os.path.join(CACHE, "text")
_cache = {}


def dokumenttext_normaliserad(dokument_id):
    if dokument_id in _cache:
        return _cache[dokument_id]
    path = os.path.join(TEXT, dokument_id + ".txt")
    if not os.path.exists(path):
        _cache[dokument_id] = None
        return None
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    sidor = raw.split("\f")
    varde = {
        "hel": normalisera(raw),
        "sidor": [normalisera(s) for s in sidor],
    }
    _cache[dokument_id] = varde
    return varde


def verifiera_citat(traff):
    """Returnerar (ok: bool, orsak: str|None, inledning_ok: bool).

    Citatet och föreskriftsinledningen verifieras var för sig — de är två
    fristående delsträngar ur samma sida och limmas aldrig ihop. Inledningen
    måste dessutom ligga FÖRE citatet på sidan; gör den inte det är den inte
    citatets rubrik och kasseras.
    """
    doc = dokumenttext_normaliserad(traff["dokument_id"])
    if doc is None:
        return False, "källtext saknas", False
    citat = normalisera(traff["citat"])
    if len(citat) < 25:
        return False, "citatet för kort för meningsfull verifiering", False

    sidnr = traff.get("sidnummer")
    if not (sidnr and 1 <= sidnr <= len(doc["sidor"])):
        if citat in doc["hel"]:
            return False, "sidnummer utanför dokumentets sidintervall", False
        return False, "ingen ordagrann förekomst i dokumentet", False

    sida = doc["sidor"][sidnr - 1]
    pos = sida.find(citat)
    if pos < 0:
        if citat in doc["hel"]:
            return False, "citatet finns i dokumentet men inte på angiven sida", False
        return False, "ingen ordagrann förekomst i dokumentet", False

    inledning = traff.get("inledning")
    if not inledning:
        return True, None, False
    inl_sidnr = traff.get("inledning_sidnummer") or sidnr
    if not (1 <= inl_sidnr <= len(doc["sidor"])):
        return True, None, False
    inl = normalisera(inledning)
    inl_pos = doc["sidor"][inl_sidnr - 1].find(inl)
    if inl_pos < 0:
        # Citatet står kvar; bara inledningen kasseras.
        return True, None, False
    # På samma sida måste rubriken stå före punkten. På en tidigare sida är
    # den per definition före.
    if inl_sidnr == sidnr and inl_pos >= pos:
        return True, None, False
    return True, None, True


def main():
    extraktion = read_json(os.path.join(DATA, "extraktion.json"))
    manifest = read_json(os.path.join(DATA, "manifest.json"))
    if extraktion is None or manifest is None:
        sys.exit("Kör scripts/01–03 först.")

    verifierade, fel = {}, []
    stats = {
        "antal_objekt": len(manifest["objekt"]),
        "objekt_med_verifierade_citat": 0,
        "objekt_i_lanklage": 0,
        "objekt_utan_traffar": 0,
        "objekt_med_ocr": 0,
        "citat_prövade": 0,
        "citat_godkanda": 0,
        "citat_kasserade": 0,
        "felorsaker": {},
        "per_klass_godkanda": {},
    }

    for nvrid in sorted(manifest["objekt"]):
        traffar = extraktion["traffar"].get(nvrid) or []
        ok_lista = []
        for t in traffar:
            stats["citat_prövade"] += 1
            ok, orsak, inl_ok = verifiera_citat(t)
            if ok:
                stats["citat_godkanda"] += 1
                if inl_ok:
                    stats["inledningar_godkanda"] = stats.get("inledningar_godkanda", 0) + 1
                elif t.get("inledning"):
                    stats["inledningar_kasserade"] = stats.get("inledningar_kasserade", 0) + 1
                k = t["klassificering"]
                stats["per_klass_godkanda"][k] = stats["per_klass_godkanda"].get(k, 0) + 1
                post = {**t, "verifierad": True,
                        "verifieringsmetod": "ordagrann strängmatchning efter "
                                             "dokumenterad normalisering"}
                if not inl_ok:
                    post["inledning"] = None
                ok_lista.append(post)
            else:
                stats["citat_kasserade"] += 1
                stats["felorsaker"][orsak] = stats["felorsaker"].get(orsak, 0) + 1
                fel.append({
                    "nvrid": nvrid,
                    "dokument_id": t.get("dokument_id"),
                    "sidnummer": t.get("sidnummer"),
                    "orsak": orsak,
                    "citat_borjan": t["citat"][:120],
                })
        verifierade[nvrid] = ok_lista
        rec = manifest["objekt"][nvrid]
        har_ocr = any(d.get("ocr") for d in rec.get("dokument") or [])
        if har_ocr:
            stats["objekt_med_ocr"] += 1
        if ok_lista:
            stats["objekt_med_verifierade_citat"] += 1
        elif traffar:
            stats["objekt_i_lanklage"] += 1   # hade träffar men inget höll
        else:
            stats["objekt_utan_traffar"] += 1

    andel = (stats["citat_godkanda"] / stats["citat_prövade"] * 100
             if stats["citat_prövade"] else 0.0)
    stats["andel_citat_godkanda_procent"] = round(andel, 1)
    stats["andel_objekt_med_verifierat_innehall_procent"] = round(
        stats["objekt_med_verifierade_citat"] / max(stats["antal_objekt"], 1) * 100, 1)

    write_json(os.path.join(DATA, "verification-report.json"), {
        "schema_version": 1,
        "kord": now_iso(),
        "normalisering": [
            "Unicode NFKC",
            "ligaturer (ﬁ ﬂ ﬀ ﬃ ﬄ ﬅ ﬆ) expanderas",
            "typografiska citattecken och apostrofer → raka",
            "alla streckvarianter → bindestreck",
            "mjukt bindestreck och nollbreddstecken tas bort",
            "avstavning över radbryt slås ihop",
            "all whitespace kollapsas till enkelt mellanslag",
        ],
        "krav": "exakt delsträngsförekomst efter normalisering; ingen fuzzy-matchning",
        "statistik": stats,
        "kasserade": fel,
    })
    write_json(os.path.join(DATA, "verifierade-citat.json"),
               {"schema_version": 1, "citat": verifierade})
    log(f"Steg 4 klart: {stats['citat_godkanda']}/{stats['citat_prövade']} citat "
        f"godkända ({andel:.1f}%), {stats['objekt_i_lanklage']} objekt i länk-läge")
    if fel:
        log(f"  felorsaker: {stats['felorsaker']}")


if __name__ == "__main__":
    main()
