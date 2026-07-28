"""Appikoner, ritade i kod.

Varför inte en bildfil i förrådet: pipelinen ska gå att köra om på vilken
maskin som helst utan installationssteg, och det gäller även ikonerna. Här
skrivs PNG:er direkt med zlib och struct ur stdlib — ingen Pillow, ingen
SVG-konverterare som råkar saknas på byggmaskinen.

Märket är en fyrkopter sedd uppifrån inuti en ring: fyra rotorer, en kropp,
och en ring som antyder en zongräns. Det är läsbart ned till 48 px, vilket är
den storlek Android faktiskt visar på hemskärmen.
"""
from __future__ import annotations

import struct
import zlib

BAKGRUND = (11, 45, 74)      # djupblå
MARKE = (255, 255, 255)
ACCENT = (198, 40, 40)       # samma röd som nivå 1 i gränssnittet


def _png(bredd, hojd, pixlar):
    """Minimal men fullt giltig PNG (8-bitars RGBA, filtertyp 0)."""
    rader = bytearray()
    for y in range(hojd):
        rader.append(0)
        rad = pixlar[y]
        for x in range(bredd):
            rader.extend(rad[x])
    komprimerad = zlib.compress(bytes(rader), 9)

    def bit(typ, data):
        return (struct.pack(">I", len(data)) + typ + data +
                struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n" +
            bit(b"IHDR", struct.pack(">IIBBBBB", bredd, hojd, 8, 6, 0, 0, 0)) +
            bit(b"IDAT", komprimerad) +
            bit(b"IEND", b""))


def _blanda(under, over, alfa):
    return tuple(int(round(under[i] * (1 - alfa) + over[i] * alfa)) for i in range(3))


def rita(storlek, maskbar=False):
    """Returnerar PNG-byte för en kvadratisk ikon.

    `maskbar` ritar märket mindre, så att det överlever Androids beskärning
    till cirkel (den så kallade säkra zonen är de inre 80 procenten).
    """
    n = storlek
    c = (n - 1) / 2.0
    skala = 0.62 if maskbar else 0.80
    # Superenkel kantutjämning: fyra delprover per pixel räcker gott för
    # symboler av den här sorten, och håller koden läsbar.
    prov = [(-0.25, -0.25), (0.25, -0.25), (-0.25, 0.25), (0.25, 0.25)]

    ring_r = 0.455 * n * skala
    ring_tjocklek = 0.042 * n * skala
    rotor_r = 0.082 * n * skala
    rotor_avstand = 0.215 * n * skala
    arm = 0.026 * n * skala
    kropp = 0.088 * n * skala

    def tackning(px, py):
        """Hur mycket av pixeln som täcks av märket, och av vilken färg.

        Ordningen är medveten: kroppen ritas röd och armarna bryts mot den, så
        att mittmarkeringen inte överritas av det vita korset.
        """
        vit = 0
        rod = 0
        for dx, dy in prov:
            x, y = px + dx - c, py + dy - c
            r = (x * x + y * y) ** 0.5
            if abs(r - ring_r) <= ring_tjocklek:
                vit += 1
                continue
            if max(abs(x), abs(y)) <= kropp:
                rod += 1
                continue
            traffad = False
            for ox, oy in ((-rotor_avstand, -rotor_avstand),
                           (rotor_avstand, -rotor_avstand),
                           (-rotor_avstand, rotor_avstand),
                           (rotor_avstand, rotor_avstand)):
                if ((x - ox) ** 2 + (y - oy) ** 2) ** 0.5 <= rotor_r:
                    vit += 1
                    traffad = True
                    break
            if traffad:
                continue
            # Armarna: två diagonala band ut till rotorerna.
            if (abs(abs(x) - abs(y)) <= arm
                    and max(abs(x), abs(y)) <= rotor_avstand):
                vit += 1
        return vit / len(prov), rod / len(prov)

    pixlar = []
    hornradie = 0.22 * n
    for y in range(n):
        rad = []
        for x in range(n):
            # Rundade hörn så att ikonen ser rätt ut även där plattformen inte
            # maskar den själv.
            dx = max(hornradie - x, 0, x - (n - 1 - hornradie))
            dy = max(hornradie - y, 0, y - (n - 1 - hornradie))
            utanfor = (dx * dx + dy * dy) ** 0.5 > hornradie
            if utanfor and not maskbar:
                rad.append(bytes((0, 0, 0, 0)))
                continue
            v, r = tackning(x, y)
            farg = BAKGRUND
            if r > 0:
                farg = _blanda(farg, ACCENT, min(r, 1.0))
            if v > 0:
                farg = _blanda(farg, MARKE, min(v, 1.0))
            rad.append(bytes(farg + (255,)))
        pixlar.append(rad)
    return _png(n, n, pixlar)
