"""Delade hjälpfunktioner för pipelinen.

Ingen tredjepartsberoende: allt bygger på Python-stdlib så att pipelinen kan
köras om på vilken maskin som helst utan installationssteg.
"""
from __future__ import annotations

import hashlib
import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE = os.path.join(ROOT, "cache")
DATA = os.path.join(ROOT, "data")
DIST = os.path.join(ROOT, "dist")

_last_request = {}


def load_config():
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


CONFIG = load_config()


def today():
    """Hämtningsdatum som ISO-sträng (UTC-datum)."""
    return datetime.now(timezone.utc).date().isoformat()


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _throttle(host: str, min_interval: float):
    """Minst `min_interval` sekunder mellan anrop mot samma värd."""
    prev = _last_request.get(host)
    if prev is not None:
        wait = min_interval - (time.time() - prev)
        if wait > 0:
            time.sleep(wait)
    _last_request[host] = time.time()


def fetch(url: str, *, min_interval: float = None, timeout: int = 120,
          retries: int = 3, accept: str = None) -> bytes:
    """Hämta en URL med throttling, beskrivande User-Agent och retry."""
    if min_interval is None:
        min_interval = CONFIG["throttle_api_s"]
    host = urllib.parse.urlsplit(url).netloc
    last_err = None
    for attempt in range(retries):
        _throttle(host, min_interval)
        req = urllib.request.Request(url, headers={
            "User-Agent": CONFIG["user_agent"],
            "Accept": accept or "*/*",
        })
        try:
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001 - vi vill fånga allt och backa av
            last_err = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"fetch misslyckades efter {retries} försök: {url}: {last_err}")


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path, obj, *, compact=False):
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as fh:
        if compact:
            json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, fh, ensure_ascii=False, indent=1, sort_keys=False)
            fh.write("\n")


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def slugify(name: str) -> str:
    """URL-slug för svenska områdesnamn."""
    trans = str.maketrans("åäöÅÄÖéèüÜáàóòíì", "aaoAAOeeuUaaooii")
    s = name.translate(trans).lower()
    out = []
    for ch in s:
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-") or "omrade"
