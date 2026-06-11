"""Shared HTTP helper with timeout/error handling and per-source pacing."""

import time

import requests

from . import config as cfg
from .config import log

# ─────────────────────────────────────────────
#  HELPER — safe HTTP get
# ─────────────────────────────────────────────

def safe_get(url: str, params: dict = None, timeout: int = 10) -> dict | None:
    """
    HTTP GET with error handling.  Forces Connection: close so each request
    releases its socket immediately — prevents FD exhaustion on long runs
    with many API sources and 60-second rate-limit sleeps between them.
    """
    try:
        r = requests.get(url, params=params, timeout=timeout,
                         headers={"Connection": "close"})
        try:
            if r.status_code == 402:
                return {"_error": 402, "_message": "Payment Required — endpoint requires a paid plan"}
            if r.status_code == 403:
                return {"_error": 403}
            if r.status_code == 429:
                return {"_error": 429, "_message": "Too Many Requests — rate or monthly limit hit"}
            r.raise_for_status()
            return r.json()
        finally:
            r.close()
    except requests.exceptions.HTTPError as e:
        log.warning(f"HTTP error: {e}  url={url}")
        return None
    except Exception as e:
        log.error(f"Request failed: {e}  url={url}")
        return None

def sleep_for_rate(source: str):
    """Sleep just enough to respect per-minute limits."""
    limit = cfg.MINUTE_LIMITS.get(source)
    if limit:
        time.sleep(60 / limit + 0.1)   # e.g. 1.1 s between Polygon calls

