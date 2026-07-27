"""Rutnät för kart- och positionsdata.

Hela landet i en fil fungerar inte på en telefon: rikstäckande geometri är
tiotals megabyte. Datan delas därför upp så att klienten bara hämtar det den
behöver just nu.

  oversikt.json        en fil för hela landet, hårt förenklad, bara områden
                       över en viss storlek. Laddas när kartan är utzoomad.
  rutor/{ID}.json      visningsgeometri i full upplösning, 1°-rutor. Bara de
                       rutor som syns i kartfönstret laddas.
  geom/{ID}.json       ORIGINALGEOMETRI, 0,25°-rutor, för punkt-i-polygon.
  bbox/{ID}.json       omslutande rektanglar, 0,25°-rutor, för att hitta
                       kandidater innan originalgeometrin hämtas.

Två rutstorlekar, av två skäl. Visningsrutorna får vara stora — man ritar ändå
hela kartfönstret. Geometrirutorna måste vara små, dels för att svaret ska gå
fort på mobil, dels för att Cloudflare Pages tar max 20 000 filer per
utrullning: en fil per område hade blivit 10 772 filer utöver lika många
HTML-sidor och spräckt taket. Per-områdesfilerna finns kvar i kodförrådet som
CC0-produkt, men serveras inte styckvis.

Ett område som spänner över flera rutor läggs i var och en av dem, och
klienten avdubblerar på NVRID.

Att ett område utelämnas ur översikten är ett synligt tillstånd, inte en tyst
lucka: kartan skriver ut hur många områden som inte visas vid aktuell zoom.
"""
from __future__ import annotations

import math

VISNING_GRADER = 1.0
GEOMETRI_GRADER = 0.25


def ruta_id(lon, lat, steg):
    return f"{int(math.floor(lon / steg))}_{int(math.floor(lat / steg))}"


def rutor_for_bbox(bb, steg):
    """Alla ruta-id:n som en omslutande rektangel berör."""
    minx, miny, maxx, maxy = bb
    ut = []
    x = math.floor(minx / steg)
    while x * steg <= maxx:
        y = math.floor(miny / steg)
        while y * steg <= maxy:
            ut.append(f"{int(x)}_{int(y)}")
            y += 1
        x += 1
    return ut
