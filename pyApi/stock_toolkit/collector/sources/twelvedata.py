"""Twelve Data fetcher (live + historical)."""

import time
from datetime import date

from .. import config as cfg
from ..config import log
from ..db import (
    make_row, _hist_has_data, _live_has_today, _hourly_bar_is_current,
)
from ..failures import is_suppressed, record_failure
from ..http import safe_get
from ..state import budget_ok, record_call

# ── 6. Twelve Data ────────────────────────────
def fetch_twelvedata(symbols: list[str], state: dict) -> list[dict]:
    """
    /time_series — OHLCV bars (daily + 1h) per symbol.
    Free: 800 credits/day, 8 credits/minute (1 credit per symbol per request).
    Symbols are batched in groups of 8 with a 62-second sleep between batches
    to stay within the per-minute limit.
    """
    key = cfg.API_KEYS["twelvedata"]
    if not key:
        return []

    rows = []
    # filter to symbols not yet collected and not suppressed
    symbols_1d = [s for s in symbols
                  if not is_suppressed(s, "twelvedata")
                  and not _live_has_today(s, "twelvedata", "1d")]
    symbols_1h = [s for s in symbols
                  if not is_suppressed(s, "twelvedata")
                  and not _hourly_bar_is_current(s, "twelvedata")]
    if not symbols_1d:
        log.info("[twelvedata] all symbols already collected today (1d), skipping")
    if not symbols_1h:
        log.info("[twelvedata] all hourly bars current, skipping 1h fetch")

    TD_CREDITS_PER_MIN = 8   # free plan limit

    def _chunks(lst, n):
        for i in range(0, len(lst), n):
            yield lst[i:i + n]

    def fetch_series(interval: str, syms: list[str], outputsize: int = 30) -> None:
        if not syms:
            return
        for i, batch in enumerate(_chunks(syms, TD_CREDITS_PER_MIN)):
            if not budget_ok(state, "twelvedata"):
                log.warning("[twelvedata] daily budget exhausted, stopping")
                return
            if i > 0:
                log.info("[twelvedata] rate-limit pause 62s before next batch…")
                time.sleep(62)
            data = safe_get(
                "https://api.twelvedata.com/time_series",
                params={"symbol": ",".join(batch), "interval": interval,
                        "outputsize": outputsize, "apikey": key}
            )
            record_call(state, "twelvedata", len(batch))  # 1 credit per symbol
            if not data:
                log.warning(f"[twelvedata] no response for batch {batch}")
                continue
            # check for a top-level error response (entire batch rejected)
            # e.g. {"code": 429, "message": "...", "status": "error"}
            if data.get("status") == "error" or "code" in data and "message" in data:
                log.warning(f"[twelvedata] {interval} batch error "
                            f"{data.get('code','?')}: {data.get('message','?')}")
                continue
            # single-symbol response has "values" at top level; wrap it
            if "values" in data:
                data = {batch[0]: data}
            for sym, payload in data.items():
                if not isinstance(payload, dict):
                    log.warning(f"[twelvedata] {sym} {interval}: "
                                f"unexpected type {type(payload).__name__}: {payload}")
                    continue
                if payload.get("status") == "error":
                    msg = payload.get("message","API error")
                    log.warning(f"[twelvedata] {sym} {interval}: {msg}")
                    record_failure(sym, "twelvedata", msg[:80])
                    continue
                if "values" not in payload:
                    log.warning(f"[twelvedata] {sym} {interval}: "
                                f"no values — {payload.get('message','?')}")
                    continue
                stored_interval = "1d" if interval == "1day" else interval
                for bar in payload["values"]:
                    rows.append(make_row(
                        sym, "twelvedata", bar["datetime"], stored_interval,
                        bar.get("open"), bar.get("high"), bar.get("low"),
                        bar.get("close"), bar.get("volume"),
                    ))
                log.info(f"[twelvedata] {sym} {interval}: {len(payload['values'])} bars")

    fetch_series("1day", symbols_1d, outputsize=30)
    if symbols_1d and symbols_1h:
        time.sleep(62)   # gap between daily and hourly batches
    fetch_series("1h",   symbols_1h, outputsize=24)
    return rows


def _hist_twelvedata(symbols, db_path, date_from, date_to, state) -> list:
    """
    Twelve Data time_series: outputsize=5000 covers ~19 years per call.
    For longer ranges the range is split into 19-year chunks.
    800 calls/day free.
    """
    key = cfg.API_KEYS["twelvedata"]
    if not key:
        return []
    symbols_needed = [
        s for s in symbols
        if not _hist_has_data(db_path, s, "twelvedata", date_from, date_to)
    ]
    skipped = len(symbols) - len(symbols_needed)
    if skipped:
        log.info(f"[hist/twelvedata] {skipped} symbol(s) already in DB, skipping")
    if not symbols_needed:
        return []

    rows = []
    chunk_start = date_from
    while chunk_start <= date_to:
        if not budget_ok(state, "twelvedata"):
            break
        chunk_end = min(date_to,
                        date(chunk_start.year + 19, chunk_start.month, chunk_start.day))
        data = safe_get(
            "https://api.twelvedata.com/time_series",
            params={"symbol": ",".join(symbols_needed), "interval": "1day",
                    "outputsize": 5000,
                    "start_date": str(chunk_start), "end_date": str(chunk_end),
                    "apikey": key}
        )
        record_call(state, "twelvedata")
        if not data:
            break
        # check for a top-level error response (e.g. 429 rate limit)
        if data.get("status") == "error" or ("code" in data and "message" in data):
            log.warning(f"[hist/twelvedata] batch error "
                        f"{data.get('code','?')}: {data.get('message','?')}")
            break
        if "values" in data:
            data = {symbols_needed[0]: data}
        for sym, payload in data.items():
            if not isinstance(payload, dict):
                log.warning(f"[hist/twelvedata] {sym}: unexpected type "
                            f"{type(payload).__name__}: {payload}")
                continue
            if payload.get("status") == "error":
                log.warning(f"[hist/twelvedata] {sym}: {payload.get('message','?')}")
                continue
            if "values" not in payload:
                log.warning(f"[hist/twelvedata] {sym}: {payload.get('message','?')}")
                continue
            stored_interval = "1d"   # normalize "1day" → "1d"
            for bar in payload["values"]:
                rows.append(make_row(
                    sym, "twelvedata", bar["datetime"], stored_interval,
                    bar.get("open"), bar.get("high"), bar.get("low"),
                    bar.get("close"), bar.get("volume"),
                ))
            log.info(f"[hist/twelvedata] {sym}: {len(payload['values'])} bars "
                     f"({chunk_start} → {chunk_end})")
        chunk_start = date(chunk_end.year + 1, 1, 1)
        time.sleep(1)
    return rows


