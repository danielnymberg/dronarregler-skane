#!/usr/bin/env python3
"""Månadsuppdatering.

Kör steg 1 på nytt, jämför manifestet mot föregående bygge och rapporterar
vad som ändrats. Steg 2 hämtar bara dokument som saknas lokalt och extraherar
bara text för dokument vars hash ändrats — cachen sköter inkrementaliteten.
Steg 4 (verifieringen) körs alltid från noll, även för oförändrade objekt.

Ett misslyckat jobb får aldrig göra sajten yngre än den är: skriptet rör inte
dist/ förrän hela kedjan och testsviten gått igenom.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.common import DATA, ROOT, log, now_iso, read_json, write_json

STEG = [
    ("Steg 1 — ingest", "01_ingest.py"),
    ("Steg 2 — dokument", "02_fetch_docs.py"),
    ("Steg 3 — extraktion", "03_extract.py"),
    ("Steg 4 — verifiering", "04_verify.py"),
    ("Steg 5 — databygge", "05_build_data.py"),
]


def kor(script):
    p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", script)],
                       cwd=ROOT)
    if p.returncode != 0:
        sys.exit(f"{script} misslyckades (kod {p.returncode}) — dist/ lämnas orörd.")


def sammanfatta(m):
    if not m:
        return {}
    return {n: {
        "namn": r.get("namn"),
        "skyddstyp": r.get("skyddstyp"),
        "geometrihash": (r.get("geometri_kalla") or {}).get("svarshash_sha256"),
        "dokument": sorted(d.get("sha256") or d.get("url")
                           for d in (r.get("dokument") or [])),
    } for n, r in (m.get("objekt") or {}).items()}


def main():
    fore = sammanfatta(read_json(os.path.join(DATA, "manifest.json")))

    for rubrik, script in STEG:
        log(rubrik)
        kor(script)

    efter = sammanfatta(read_json(os.path.join(DATA, "manifest.json")))
    nya = sorted(set(efter) - set(fore))
    borta = sorted(set(fore) - set(efter))
    andrade = sorted(n for n in set(fore) & set(efter) if fore[n] != efter[n])

    diff = {
        "kord": now_iso(),
        "antal_fore": len(fore),
        "antal_efter": len(efter),
        "nya": [{"nvrid": n, "namn": efter[n]["namn"]} for n in nya],
        "borttagna": [{"nvrid": n, "namn": fore[n]["namn"]} for n in borta],
        "andrade": [{"nvrid": n, "namn": efter[n]["namn"]} for n in andrade],
    }
    write_json(os.path.join(DATA, "uppdateringsdiff.json"), diff)
    log(f"Diff: {len(nya)} nya, {len(borta)} borttagna, {len(andrade)} ändrade")

    log("Bygger sajt och kör testsvit (dist/ rullas tillbaka om testerna faller)")
    p = subprocess.run(["make", "--no-print-directory", "_site-med-grind"], cwd=ROOT)
    if p.returncode != 0:
        sys.exit("Testsviten föll — dist/ rullades tillbaka till föregående bygge.")
    log("Uppdatering klar.")


if __name__ == "__main__":
    main()
