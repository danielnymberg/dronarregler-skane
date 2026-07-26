#!/usr/bin/env python3
"""Test E — visuell granskning.

Startar en lokal server mot dist/ och plockar fram de sidor och lägen som ska
granskas med ögon. Skriptet öppnar inte browsern själv (det görs av den som
kör granskningen, med Chrome-verktygen), men det gör tre saker:

  1. serverar dist/ på en känd port,
  2. skriver ut exakt vilka URL:er och åtgärder som ska granskas,
  3. plockar automatiskt fram lämpliga testobjekt ur data/ — ett OCR-flaggat
     område, ett område med säsongsdata, Kullaberg — så att listan inte
     hårdkodas och ruttnar.

Kör: python3 scripts/07_visuell_granskning.py [--port 8787] [--servera]
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import socketserver
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.common import DATA, DIST, ROOT, read_json


def las_omraden():
    katalog = os.path.join(DATA, "omraden")
    for fn in sorted(os.listdir(katalog)):
        o = read_json(os.path.join(katalog, fn))
        if o:
            yield o


def valj_testobjekt():
    ocr = sasong = kullaberg = None
    flest = None
    for o in las_omraden():
        if o["nvrid"] == "2000972":
            kullaberg = o
        if ocr is None and o.get("ocr") and o.get("citat"):
            ocr = o
        if sasong is None and o.get("sasongsdata"):
            sasong = o
        if o.get("citat") and (flest is None or len(o["citat"]) > len(flest["citat"])):
            flest = o
    return {"kullaberg": kullaberg, "ocr": ocr, "sasong": sasong, "flest_citat": flest}


def url(o, port):
    return f"http://localhost:{port}/omrade/{o['nvrid']}-{o['slug']}/" if o else None


def servera(port):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=DIST)
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--servera", action="store_true",
                    help="håll servern igång tills Ctrl+C")
    args = ap.parse_args()

    if not os.path.isdir(DIST):
        sys.exit("dist/ saknas — kör make all först.")

    valda = valj_testobjekt()
    golden = read_json(os.path.join(ROOT, "tests", "golden.json"))
    punkt = golden["punkter_utan_traff"][0]

    plan = {
        "port": args.port,
        "granskningspunkter": [
            {"id": "a-karta-lansutsnitt",
             "url": f"http://localhost:{args.port}/",
             "viewport": "mobil 390×844",
             "kontrollera": "Alla Skånepolygoner renderas vid länsutsnitt utan "
                            "länsväljare och utan att något kärnlager måste slås på "
                            "manuellt.",
             "skarmdump": "a-karta-lansutsnitt-mobil.png"},
            {"id": "b-positionssvar-hoganas",
             "url": f"http://localhost:{args.port}/",
             "atgard": f"window.DK_positionssvar({punkt['lon']}, {punkt['lat']})",
             "kontrollera": "Svarsläge 2 visas med texten 'Ingen restriktion hittad i "
                            "de källor tjänsten täcker' — aldrig som tillåtelse — plus "
                            "LFV-raden och ansvarstexten.",
             "skarmdump": "b-positionssvar-hoganas.png"},
            {"id": "c-kullabergssidan",
             "url": url(valda["kullaberg"], args.port),
             "kontrollera": "Citatet, dokumentlänken, hämtningsdatumet och "
                            "ansvarstexten syns.",
             "skarmdump": "c-kullaberg.png"},
            {"id": "d-lfv-raster",
             "url": f"http://localhost:{args.port}/",
             "atgard": "Slå av och på kryssrutan 'Luftrumslager från LFV'",
             "kontrollera": "Rastret försvinner/återkommer och attributionen "
                            "'© LFV (CC BY-NC-ND 4.0)' syns i kartans hörn.",
             "skarmdump": "d-lfv-raster.png"},
            {"id": "e-ocr-varning",
             "url": url(valda["ocr"], args.port),
             "kontrollera": "OCR-varningen 'Texten är OCR-tolkad ur inskannat "
                            "original' visas.",
             "skarmdump": "e-ocr-varning.png"},
            {"id": "f-sasongsdata",
             "url": url(valda["sasong"], args.port),
             "kontrollera": "Datumperioderna visas och kommer ur källdata.",
             "skarmdump": "f-sasongsdata.png"},
        ],
        "valda_objekt": {k: (v and {"nvrid": v["nvrid"], "namn": v["namn"],
                                    "citat": len(v.get("citat") or []),
                                    "ocr": v.get("ocr")})
                         for k, v in valda.items()},
    }
    ut = os.path.join(ROOT, "verification", "granskningsplan.json")
    os.makedirs(os.path.dirname(ut), exist_ok=True)
    with open(ut, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, ensure_ascii=False, indent=1)

    print(json.dumps(plan, ensure_ascii=False, indent=1))
    if args.servera:
        srv = servera(args.port)
        print(f"\nServern kör på http://localhost:{args.port} — Ctrl+C avslutar.")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            srv.shutdown()


if __name__ == "__main__":
    main()
