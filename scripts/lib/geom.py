"""Geometrihjälpmedel — förenkling, ytberäkning och punkt-i-polygon.

Regel R3: geometri uppfinns aldrig. Ingen funktion här skapar nya ytor,
buffertar eller cirklar. `forenkla_ring` tar bara bort befintliga punkter,
och den lämnar ringen orörd om förenklingen skulle göra ytan större —
förenklingen får krympa eller behålla ytan, aldrig utvidga den.
"""
from __future__ import annotations

import math

JORDRADIE = 6371008.8


def _skala(lat_deg):
    """Meter per grad i longitud/latitud vid given latitud."""
    m_per_lat = math.pi * JORDRADIE / 180.0
    m_per_lon = m_per_lat * math.cos(math.radians(lat_deg))
    return m_per_lon, m_per_lat


def ring_area_m2(ring):
    """Ungefärlig yta i m² via shoelace på lokalt planprojicerade koordinater."""
    if len(ring) < 4:
        return 0.0
    lat0 = sum(p[1] for p in ring) / len(ring)
    mx, my = _skala(lat0)
    s = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i][0] * mx, ring[i][1] * my
        x2, y2 = ring[i + 1][0] * mx, ring[i + 1][1] * my
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _perp_avstand_m(p, a, b, mx, my):
    px, py = p[0] * mx, p[1] * my
    ax, ay = a[0] * mx, a[1] * my
    bx, by = b[0] * mx, b[1] * my
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _dp(punkter, tol_m, mx, my):
    if len(punkter) < 3:
        return punkter
    a, b = punkter[0], punkter[-1]
    maxd, idx = -1.0, 0
    for i in range(1, len(punkter) - 1):
        d = _perp_avstand_m(punkter[i], a, b, mx, my)
        if d > maxd:
            maxd, idx = d, i
    if maxd <= tol_m:
        return [a, b]
    vanster = _dp(punkter[:idx + 1], tol_m, mx, my)
    hoger = _dp(punkter[idx:], tol_m, mx, my)
    return vanster[:-1] + hoger


def forenkla_ring(ring, tol_m):
    """Douglas–Peucker med krympgaranti.

    Returnerar (ny_ring, forenklad: bool). Om förenklingen skulle ge en
    större yta än originalet returneras originalringen orörd — då kan den
    ritade ytan aldrig påstå mer utbredning än myndighetens geometri.
    """
    if len(ring) <= 5:
        return ring, False
    lat0 = sum(p[1] for p in ring) / len(ring)
    mx, my = _skala(lat0)
    stangd = ring[0] == ring[-1]
    kropp = ring[:-1] if stangd else ring[:]
    # Douglas–Peucker på en sluten ring: dela i två halvor kring den
    # längst bort liggande punkten så att ringen inte kollapsar.
    if len(kropp) > 3:
        halva = len(kropp) // 2
        del1 = _dp(kropp[:halva + 1], tol_m, mx, my)
        del2 = _dp(kropp[halva:] + [kropp[0]], tol_m, mx, my)
        ny = del1[:-1] + del2
    else:
        ny = kropp + [kropp[0]]
    if ny[0] != ny[-1]:
        ny = ny + [ny[0]]
    if len(ny) < 4:
        return ring, False
    if ring_area_m2(ny) > ring_area_m2(ring):
        return ring, False       # förenklingen växte — behåll originalet
    return ny, len(ny) < len(ring)


def forenkla_geometri(geom, tol_m):
    """Förenklar Polygon/MultiPolygon. Returnerar (geometri, statistik)."""
    stat = {"punkter_fore": 0, "punkter_efter": 0, "ringar_behallna": 0,
            "yta_fore_m2": 0.0, "yta_efter_m2": 0.0}

    def gor_polygon(rings):
        ut = []
        for j, ring in enumerate(rings):
            stat["punkter_fore"] += len(ring)
            stat["yta_fore_m2"] += ring_area_m2(ring) * (1 if j == 0 else -1)
            ny, andrad = forenkla_ring(ring, tol_m)
            if not andrad:
                stat["ringar_behallna"] += 1
            stat["punkter_efter"] += len(ny)
            stat["yta_efter_m2"] += ring_area_m2(ny) * (1 if j == 0 else -1)
            ut.append(ny)
        return ut

    t = geom["type"]
    if t == "Polygon":
        return {"type": "Polygon", "coordinates": gor_polygon(geom["coordinates"])}, stat
    if t == "MultiPolygon":
        return {"type": "MultiPolygon",
                "coordinates": [gor_polygon(p) for p in geom["coordinates"]]}, stat
    return geom, stat


def bbox(geom):
    minx = miny = float("inf")
    maxx = maxy = float("-inf")

    def gar(c):
        nonlocal minx, miny, maxx, maxy
        if isinstance(c[0], (int, float)):
            minx, maxx = min(minx, c[0]), max(maxx, c[0])
            miny, maxy = min(miny, c[1]), max(maxy, c[1])
        else:
            for d in c:
                gar(d)

    gar(geom["coordinates"])
    return [round(minx, 6), round(miny, 6), round(maxx, 6), round(maxy, 6)]


def _punkt_i_ring(x, y, ring):
    """Ray casting. Punkt exakt på kanten räknas som inuti."""
    inne = False
    n = len(ring)
    for i in range(n - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        if (y1 > y) != (y2 > y):
            xs = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x == xs:
                return True
            if x < xs:
                inne = not inne
    return inne


def punkt_i_geometri(lon, lat, geom):
    """Punkt-i-polygon mot ORIGINALGEOMETRIN. Hål respekteras."""
    t = geom["type"]
    polys = geom["coordinates"] if t == "MultiPolygon" else [geom["coordinates"]]
    for rings in polys:
        if not rings:
            continue
        if _punkt_i_ring(lon, lat, rings[0]):
            if not any(_punkt_i_ring(lon, lat, h) for h in rings[1:]):
                return True
    return False


def avstand_m(lon1, lat1, lon2, lat2):
    """Haversine."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * JORDRADIE * math.asin(math.sqrt(a))


def har_sjalvskarning(ring, max_punkter=2000):
    """Enkel självskärningskontroll (O(n²), hoppas över för mycket stora ringar)."""
    n = len(ring) - 1
    if n > max_punkter or n < 4:
        return False

    def skar(a, b, c, d):
        def ori(p, q, r):
            v = (q[1] - p[1]) * (r[0] - q[0]) - (q[0] - p[0]) * (r[1] - q[1])
            return 0 if abs(v) < 1e-14 else (1 if v > 0 else 2)
        o1, o2, o3, o4 = ori(a, b, c), ori(a, b, d), ori(c, d, a), ori(c, d, b)
        return o1 != o2 and o3 != o4

    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            if skar(ring[i], ring[i + 1], ring[j], ring[j + 1]):
                return True
    return False
