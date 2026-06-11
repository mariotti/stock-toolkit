"""Massive (formerly Polygon.io) fetcher (live + historical)."""

from datetime import date, timedelta

from .. import config as cfg
from ..config import log
from ..db import (
    make_row, _hist_has_data, _live_has_today,
)
from ..failures import is_suppressed, record_failure
from ..http import safe_get, sleep_for_rate
from ..state import budget_ok

# ── 4. Massive (formerly Polygon.io) ───────────
def fetch_polygon(symbols: list[str], state: dict) -> list[dict]:
    """
    /v2/aggs/ticker/{sym}/range — OHLCV bars (daily, last 30 days).
    Free: 5 calls/min, delayed data.
    """
    key = cfg.API_KEYS["polygon"]
    if not key:
        return []

    rows = []
    to_date   = date.today().isoformat()
    from_date = (date.today() - timedelta(days=30)).isoformat()

    for sym in symbols:
        if is_suppressed(sym, "polygon"):
            log.info(f"[polygon] {sym}: suppressed after {cfg.FAILURE_THRESHOLD} failures — skipping")
            continue
        if _live_has_today(sym, "polygon"):
            log.info(f"[polygon] {sym}: already collected today, skipping")
            continue
        if not budget_ok(state, "polygon"):
            break
        url = f"https://api.massive.com/v2/aggs/ticker/{sym}/range/1/day/{from_date}/{to_date}"
        data = safe_get(url, params={"adjusted": "true", "sort": "asc", "apiKey": key})
        sleep_for_rate("polygon")
        if not data or data.get("status") not in ("OK", "DELAYED"):
            reason = data.get("status") if data else "no response"
            log.warning(f"[polygon] {sym}: {reason}")
            record_failure(sym, "polygon", reason)
            continue
        for bar in data.get("results", []):
            rows.append(make_row(
                sym, "polygon", date.fromtimestamp(bar["t"] / 1000), "1d",
                bar.get("o"), bar.get("h"), bar.get("l"), bar.get("c"), bar.get("v"),
                vwap=bar.get("vw"),
            ))
        n_bars = len(data.get("results", []))
        log.info(f"[polygon] {sym}: {n_bars} daily bars")
        if n_bars == 0:
            record_failure(sym, "polygon", "0 bars returned — US-only exchange")
    return rows


def _hist_polygon(symbols, db_path, date_from, date_to, state) -> list:
    """
    Polygon /v2/aggs range endpoint with automatic pagination.
    5 calls/min free.  Note: free tier history may be limited to ~2 years.
    """
    key = cfg.API_KEYS["polygon"]
    if not key:
        return []
    rows = []
    for sym in symbols:
        if _hist_has_data(db_path, sym, "polygon", date_from, date_to):
            log.info(f"[hist/polygon] {sym}: already in DB, skipping")
            continue
        url  = (f"https://api.massive.com/v2/aggs/ticker/{sym}/range/1/day"
                f"/{date_from}/{date_to}")
        sym_rows, page = [], 1
        while url:
            params = ({"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": key}
                      if page == 1 else {"apiKey": key})
            data = safe_get(url, params=params)
            sleep_for_rate("polygon")
            if not data or data.get("status") not in ("OK", "DELAYED"):
                break
            for bar in data.get("results", []):
                sym_rows.append(make_row(
                    sym, "polygon", date.fromtimestamp(bar["t"] / 1000), "1d",
                    bar.get("o"), bar.get("h"), bar.get("l"), bar.get("c"),
                    bar.get("v"), vwap=bar.get("vw"),
                ))
            url  = data.get("next_url")
            page += 1
        rows += sym_rows
        log.info(f"[hist/polygon] {sym}: {len(sym_rows)} bars ({page-1} page(s))")
    return rows


