#!/usr/bin/env python3
"""Steg 2 — Dokumenthämtning och textextraktion.

Två faser med olika flaskhals:

  2a  Nedladdning — nätbunden, körs seriellt med ≥1 s mellan anrop mot samma
      server, beskrivande User-Agent och lokal cache. Ombyggen hämtar inte om
      oförändrat material.
  2b  Textextraktion — CPU-bunden, körs parallellt över alla kärnor.
      pdftotext först; är sidan tom eller nästan tom antas inskannat original
      och tesseract (svenska) kör OCR per sida. OCR-flaggan följer med hela
      vägen ut i UI:t.

Urval: bara dokument som rimligen bär föreskrifter laddas ned (beslut,
föreskrifter, förordnanden, kungörelser, ändringsbeslut). Skötselplaner,
kartbilagor och visningsbilder hoppas över — de innehåller inte de
föreskrifter tjänsten citerar. Saknar ett objekt sådana dokument laddas
samtliga ned som fallback. Vad som hoppades över redovisas i manifestet.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.common import (CACHE, CONFIG, DATA, ensure_dir, fetch, log, read_json,
                        sha256_bytes, today, write_json)

DOCS = os.path.join(CACHE, "docs")
TEXT = os.path.join(CACHE, "text")

RULE_DOC = re.compile(
    r"beslut|f[oö]reskrift|f[oö]rordnand|kung[oö]rels|stadga|bildand|"
    r"utvidgn|[aä]ndr|reservatsbeslut|tilltr[aä]desf[oö]rbud|interimist",
    re.I)
SKIP_DOC = re.compile(r"visningsbild|sk[oö]tselplan|^karta|_karta|bilaga\s*karta|"
                      r"\.(jpe?g|png|tiff?|gif)$", re.I)

MAX_BYTES = 40 * 1024 * 1024
MIN_CHARS_PER_PAGE = 120
OCR_DPI = 200            # räcker för maskinskriven text, ~2× snabbare än 300
OCR_MAX_SIDOR = 60


def doc_id(url: str) -> str:
    m = re.search(r"/dokument/(\d+)", url)
    return "nvdok-" + m.group(1) if m else "url-" + sha256_bytes(url.encode())[:16]


def pick_docs(rec):
    docs = rec.get("dokument") or []
    kept, skipped = [], []
    for d in docs:
        namn = d.get("namn") or ""
        if SKIP_DOC.search(namn):
            skipped.append({**d, "orsak": "ej föreskriftsbärande dokumenttyp"})
        elif RULE_DOC.search(namn):
            kept.append(d)
        else:
            skipped.append({**d, "orsak": "namnet matchar inget föreskriftsmönster"})
    if not kept:
        kept = [d for d in docs if not SKIP_DOC.search(d.get("namn") or "")]
        skipped = [s for s in skipped if SKIP_DOC.search(s.get("namn") or "")]
    return kept, skipped


def run(cmd, timeout=300):
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


def pdf_pages(path):
    try:
        out = run(["pdfinfo", path], timeout=60)
        m = re.search(rb"Pages:\s+(\d+)", out.stdout)
        return int(m.group(1)) if m else 0
    except Exception:  # noqa: BLE001
        return 0


def extract_one(args):
    """Kör i en arbetsprocess. Returnerar metadata-dict för ett dokument."""
    did, pdf_path, hd = args
    txt_path = os.path.join(TEXT, did + ".txt")
    warn = None
    try:
        res = run(["pdftotext", "-layout", "-enc", "UTF-8", pdf_path, "-"], timeout=180)
        raw = res.stdout.decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        raw, warn = "", f"pdftotext: {exc}"
    pages = raw.split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    total = sum(len(p.strip()) for p in pages)
    npages = max(len(pages), 1)
    ocr = False

    if not (total >= MIN_CHARS_PER_PAGE * npages * 0.5 and total > 300):
        n = pdf_pages(pdf_path) or npages
        if n > OCR_MAX_SIDOR:
            warn = f"OCR hoppades över: {n} sidor"
        else:
            ocr_pages = []
            try:
                with tempfile.TemporaryDirectory() as td:
                    for p in range(1, n + 1):
                        base = os.path.join(td, "sida")
                        run(["pdftoppm", "-r", str(OCR_DPI), "-gray", "-f", str(p),
                             "-l", str(p), "-png", pdf_path, base], timeout=180)
                        pngs = sorted(f for f in os.listdir(td) if f.endswith(".png"))
                        if not pngs:
                            ocr_pages.append("")
                            continue
                        png = os.path.join(td, pngs[0])
                        r = run(["tesseract", png, "stdout", "-l", "swe",
                                 "--oem", "1", "--psm", "3"], timeout=240)
                        ocr_pages.append(r.stdout.decode("utf-8", errors="replace"))
                        os.remove(png)
                if sum(len(p.strip()) for p in ocr_pages) > total:
                    pages, ocr = ocr_pages, True
                else:
                    warn = "OCR gav inte mer text än pdftotext"
            except Exception as exc:  # noqa: BLE001
                warn = f"OCR: {exc}"

    with open(txt_path, "w", encoding="utf-8") as fh:
        fh.write("\f".join(pages))
    return {
        "dokument_id": did,
        "sha256": None,          # fylls i av huvudprocessen
        "sidor": len(pages),
        "tecken": sum(len(p) for p in pages),
        "ocr": ocr,
        "varning": warn,
        "hamtningsdatum": hd,
        "textfil": f"cache/text/{did}.txt",
    }


def main():
    manifest = read_json(os.path.join(DATA, "manifest.json"))
    if manifest is None:
        sys.exit("Kör scripts/01_ingest.py först.")
    ensure_dir(DOCS)
    ensure_dir(TEXT)
    hd = today()
    objekt = manifest["objekt"]

    # ---------------------------------------------------------- 2a nedladdning
    plan, seen = [], {}
    for nvrid, rec in sorted(objekt.items()):
        kept, skipped = pick_docs(rec)
        rec["dokument_hoppade"] = skipped
        rec["_valda_dok"] = kept
        for d in kept:
            did = doc_id(d["url"])
            seen.setdefault(did, d["url"])
    log(f"2a — {len(seen)} unika dokument att säkerställa lokalt")

    stats = {"nedladdade": 0, "cachade": 0, "fel": 0, "for_stora": 0}
    hashar, fel = {}, {}
    for i, (did, url) in enumerate(sorted(seen.items()), 1):
        pdf_path = os.path.join(DOCS, did + ".pdf")
        if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
            stats["cachade"] += 1
            with open(pdf_path, "rb") as fh:
                hashar[did] = sha256_bytes(fh.read())
            continue
        try:
            blob = fetch(url, min_interval=CONFIG["throttle_docs_s"], timeout=300)
        except Exception as exc:  # noqa: BLE001
            stats["fel"] += 1
            fel[did] = f"nedladdning: {exc}"
            continue
        if len(blob) > MAX_BYTES:
            stats["for_stora"] += 1
            fel[did] = f"för stor ({len(blob)} byte)"
            continue
        with open(pdf_path, "wb") as fh:
            fh.write(blob)
        hashar[did] = sha256_bytes(blob)
        stats["nedladdade"] += 1
        if i % 50 == 0:
            log(f"  2a {i}/{len(seen)} — {stats}")
    log(f"2a klart: {stats}")

    # -------------------------------------------------------- 2b textextraktion
    jobb = []
    for did in sorted(hashar):
        meta_path = os.path.join(TEXT, did + ".meta.json")
        txt_path = os.path.join(TEXT, did + ".txt")
        gammal = read_json(meta_path)
        if gammal and gammal.get("sha256") == hashar[did] and os.path.exists(txt_path):
            continue
        jobb.append((did, os.path.join(DOCS, did + ".pdf"), hd))
    log(f"2b — {len(jobb)} dokument behöver textextraktion "
        f"({len(hashar) - len(jobb)} oförändrade)")

    if jobb:
        workers = max(1, min(mp.cpu_count() - 2, 10))
        klara = 0
        with mp.Pool(workers) as pool:
            for meta in pool.imap_unordered(extract_one, jobb, chunksize=1):
                meta["sha256"] = hashar[meta["dokument_id"]]
                write_json(os.path.join(TEXT, meta["dokument_id"] + ".meta.json"), meta)
                klara += 1
                if klara % 25 == 0:
                    log(f"  2b {klara}/{len(jobb)}")
        log(f"2b klart ({workers} processer)")

    # ------------------------------------------------------- skriv in i manifest
    ocr_antal = 0
    for nvrid, rec in sorted(objekt.items()):
        valda = []
        for d in rec.pop("_valda_dok", []):
            did = doc_id(d["url"])
            meta = read_json(os.path.join(TEXT, did + ".meta.json"))
            if meta is None:
                valda.append({**d, "dokument_id": did,
                              "fel": fel.get(did, "text saknas")})
                continue
            valda.append({**d, **meta})
            if meta.get("ocr"):
                ocr_antal += 1
        rec["dokument"] = valda

    stats["ocr_dokument"] = ocr_antal
    stats["unika_dokument"] = len(seen)
    stats["dokument_med_fel"] = len(fel)
    manifest["dokumentstatistik"] = stats
    manifest["dokumentfel"] = fel
    manifest["dokument_hamtningsdatum"] = hd
    write_json(os.path.join(DATA, "manifest.json"), manifest)
    log(f"Steg 2 klart: {stats}")


if __name__ == "__main__":
    main()
