"""Geometrihjälpmedel — förenkling, ytberäkning och punkt-i-polygon.

Regel R3: geometri uppfinns aldrig. Ingen funktion här skapar nya ytor,
buffertar eller cirklar. `forenkla_ring` tar bara bort befintliga punkter,
och den lämnar ringen orörd om förenklingen skulle göra ytan större —
förenklingen får krympa eller behålla ytan, aldrig utvidga den.
"""
from __future__ import annotations

import math

JORDRADIE = 6371008.8
DECIMALER = 5      # ~1,1 m i longitud vid Skånes breddgrad


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


def forenkla_ring(ring, tol_m, ar_hal=False, riktning="krymp"):
    """Douglas–Peucker med garanterad felriktning på nettoytan.

    Returnerar (ny_ring, forenklad: bool).

    Nettoytan är ytterring minus hål. Vilken väg felet får gå beror på vad
    geometrin ska användas till:

      riktning="krymp"  — VISNINGSgeometri. Ytan får aldrig växa, så kartan
                          aldrig ritar mer utbredning än myndigheten beslutat.
                          Ytterring får inte bli större, hål inte mindre.

      riktning="vaxa"   — geometri för PUNKT-I-POLYGON. Här är det motsatta
                          felet det farliga: en krympt yta gör att någon som
                          står strax innanför gränsen får svaret "ingen
                          restriktion hittad". Ytan får därför aldrig krympa.
                          Ytterring får inte bli mindre, hål inte större.

    Bryts kravet returneras originalringen orörd.
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
    # Avrunda till 5 decimaler (~1 m) INNAN garantin kontrolleras, så att
    # kravet gäller exakt de koordinater som hamnar i areas.geojson.
    ny = [[round(x, DECIMALER), round(y, DECIMALER)] for x, y in ny]
    if ny[0] != ny[-1]:
        ny[-1] = list(ny[0])
    ny_yta, org_yta = ring_area_m2(ny), ring_area_m2(ring)
    if riktning == "vaxa":
        brutet = (ny_yta > org_yta) if ar_hal else (ny_yta < org_yta)
    else:
        brutet = (ny_yta < org_yta) if ar_hal else (ny_yta > org_yta)
    if brutet:
        return ring, False       # ytkravet brutet — behåll originalet
    # Douglas–Peucker på en sluten ring kan i sällsynta fall låta två
    # förenklade segment korsa varandra. En självskärande ring är ogiltig
    # geometri och renderas oförutsägbart — behåll originalet i stället.
    #
    # Men bara om originalet inte redan var självskärande. Källdatan innehåller
    # enstaka trasiga ringar (Nedre Mjällådalens naturreservat har en femhörning
    # som korsar sig själv redan i registret). Att behålla originalet löser inte
    # det, och att kasta förenklingen ger bara en större trasig ring.
    if har_sjalvskarning(ny) and not har_sjalvskarning(ring):
        return ring, False
    return ny, len(ny) < len(ring)


def forenkla_geometri(geom, tol_m, riktning="krymp"):
    """Förenklar Polygon/MultiPolygon. Returnerar (geometri, statistik)."""
    stat = {"punkter_fore": 0, "punkter_efter": 0, "ringar_behallna": 0,
            "yta_fore_m2": 0.0, "yta_efter_m2": 0.0}

    def gor_polygon(rings):
        ut, fore, efter, punkter_efter, behallna = [], 0.0, 0.0, 0, 0
        for j, ring in enumerate(rings):
            tecken = 1 if j == 0 else -1
            fore += ring_area_m2(ring) * tecken
            ny, andrad = forenkla_ring(ring, tol_m, ar_hal=(j != 0),
                                       riktning=riktning)
            if not andrad:
                behallna += 1
            punkter_efter += len(ny)
            efter += ring_area_m2(ny) * tecken
            ut.append(ny)
        # Sista spärren: skulle polygonens NETTOyta ändå ha gått åt fel håll —
        # t.ex. för att en ytterring degenererat — behålls hela polygonen
        # oförenklad.
        if (efter < fore) if riktning == "vaxa" else (efter > fore):
            ut = [list(r) for r in rings]
            punkter_efter = sum(len(r) for r in rings)
            efter = fore
            behallna = len(rings)
        stat["punkter_fore"] += sum(len(r) for r in rings)
        stat["punkter_efter"] += punkter_efter
        stat["ringar_behallna"] += behallna
        stat["yta_fore_m2"] += fore
        stat["yta_efter_m2"] += efter
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




# ---------------------------------------------------------------------------
# ESRI JSON → GeoJSON
#
# Naturvårdsverkets WFS levererar trasig JSON i outputFormat=GEOJSON för celler
# med stora geometrier: svaret kapas mitt i, deterministiskt, och en eller flera
# features saknas. Samma cell i outputFormat=ESRIGEOJSON kommer komplett. Därför
# hämtas allt som ESRI JSON och konverteras här.
#
# Skillnaden som spelar roll: i ESRI JSON ligger alla ringar i en platt lista och
# ytterring skiljs från hål på ORIENTERINGEN (medurs = ytterring, moturs = hål),
# inte på ordningen. I GeoJSON är första ringen i varje polygon ytterringen och
# resten hål. Konverteringen måste därför klassa ringarna och para ihop varje hål
# med den ytterring som omsluter det.
# ---------------------------------------------------------------------------

def _signerad_yta(ring):
    """Shoelace med tecken. Används bara för att mäta yta, inte för att avgöra
    om en ring är ytterring eller hål — se esri_till_geojson."""
    s = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[i + 1][0], ring[i + 1][1]
        s += (x2 - x1) * (y2 + y1)
    return s


def _ring_bbox(ring):
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def _bbox_inuti(a, b):
    return a[0] >= b[0] and a[1] >= b[1] and a[2] <= b[2] and a[3] <= b[3]


def _ring_i_ring(inre, yttre):
    """Sant om `inre` ligger innanför `yttre`. Prövar flera punkter, eftersom en
    enskild punkt kan ligga exakt på den yttre ringens kant."""
    provade = 0
    for p in inre[:-1]:
        if p in yttre:
            continue
        if _punkt_i_ring(p[0], p[1], yttre):
            return True
        provade += 1
        if provade >= 3:
            return False
    return False


def esri_till_geojson(feature):
    """Konvertera en ESRI-JSON-feature till en GeoJSON-feature.

    Ytterring kontra hål avgörs på INNESLUTNING, inte på ringens orientering.
    Orienteringen går inte att lita på i den här datakällan: i naturreservatet
    Verkeån (NVRID 2001534) har ett 5,2 km² stort hål samma rotationsriktning
    som den 9,2 km² stora ytterringen. En orienteringsbaserad regel gjorde det
    hålet till en egen yta och nästan fyrdubblade reservatets area.

    Regeln som används i stället: en ring som ligger innanför ett jämnt antal
    andra ringar är ytterring, innanför ett udda antal är den ett hål. Varje hål
    hamnar i den minsta ring som omsluter det.
    """
    geom = feature.get("geometry") or {}
    props = feature.get("attributes") or feature.get("properties") or {}
    ringar = geom.get("rings")
    if not ringar:
        return {"type": "Feature", "geometry": None, "properties": dict(props)}

    slutna = []
    for r in ringar:
        r = [[p[0], p[1]] for p in r]
        if r[0] != r[-1]:
            r.append(list(r[0]))
        if len(r) >= 4:
            slutna.append(r)
    if not slutna:
        return {"type": "Feature", "geometry": None, "properties": dict(props)}

    boxar = [_ring_bbox(r) for r in slutna]
    ytor = [abs(_signerad_yta(r)) for r in slutna]
    # Störst först: bara en större ring kan omsluta en mindre.
    ordning = sorted(range(len(slutna)), key=lambda i: -ytor[i])

    foraldrar = {}
    for pos, i in enumerate(ordning):
        for j in ordning[:pos]:
            if _bbox_inuti(boxar[i], boxar[j]) and _ring_i_ring(slutna[i], slutna[j]):
                foraldrar[i] = j          # sista = minsta omslutande
    djup = {}

    def rakna_djup(i):
        if i in djup:
            return djup[i]
        j = foraldrar.get(i)
        djup[i] = 0 if j is None else rakna_djup(j) + 1
        return djup[i]

    polygoner, index_for = [], {}
    for i in ordning:
        if rakna_djup(i) % 2 == 0:
            index_for[i] = len(polygoner)
            polygoner.append([slutna[i]])
    for i in ordning:
        if rakna_djup(i) % 2 == 1:
            j = foraldrar.get(i)
            if j is not None and j in index_for:
                polygoner[index_for[j]].append(slutna[i])

    if not polygoner:
        gj = None
    elif len(polygoner) == 1:
        gj = {"type": "Polygon", "coordinates": polygoner[0]}
    else:
        gj = {"type": "MultiPolygon", "coordinates": polygoner}
    return {"type": "Feature", "geometry": gj, "properties": dict(props)}


def esri_samling_till_geojson(doc):
    """Konvertera ett helt ESRI-JSON-svar till en GeoJSON FeatureCollection."""
    return {"type": "FeatureCollection",
            "features": [esri_till_geojson(f) for f in doc.get("features", [])]}
