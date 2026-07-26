"""Textnormalisering för ordagrann-matchning (Regel R7).

Normaliseringen är medvetet konservativ: den tar bort skillnader som uppstår
i PDF-textextraktion och OCR, men rör inte ordens innehåll. Efter
normalisering krävs EXAKT förekomst av citatet i källdokumentets text —
ingen fuzzy-matchning, ingen likhetströskel.

Varje transformation nedan är dokumenterad eftersom den är en del av
verifieringens beviskedja.
"""
from __future__ import annotations

import re
import unicodedata

# 1. Ligaturer som poppler och tesseract producerar ur äldre tryck.
LIGATURER = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl",
    "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
}
# 2. Typografiska citattecken och apostrofer → raka.
CITATTECKEN = {
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "«": '"', "»": '"', "″": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
}
# 3. Streck av alla slag → bindestreck.
STRECK = {
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-", "⁃": "-",
}
# 4. Osynliga tecken som ska bort helt.
BORT = ["­", "​", "‌", "‍", "﻿"]

_TRANS = {}
for _d in (LIGATURER, CITATTECKEN, STRECK):
    for _k, _v in _d.items():
        _TRANS[ord(_k)] = _v
for _c in BORT:
    _TRANS[ord(_c)] = None

# 5. Avstavning över radbryt: "natur-\nreservat" → "naturreservat".
_AVSTAVNING = re.compile(r"(\w)[-­]\s*\n\s*(\w)")
# 6. All whitespace (inkl. radbryt och sidbryt) kollapsas till ett mellanslag.
_WS = re.compile(r"\s+")


def normalisera(text: str) -> str:
    """Normalisera text inför ordagrann-matchning."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_TRANS)
    text = _AVSTAVNING.sub(r"\1\2", text)
    text = _WS.sub(" ", text)
    return text.strip()


def innehaller(hela_texten: str, citat: str) -> bool:
    """Sant om citatet förekommer ordagrant i texten efter normalisering."""
    c = normalisera(citat)
    if len(c) < 15:
        return False
    return c in normalisera(hela_texten)
