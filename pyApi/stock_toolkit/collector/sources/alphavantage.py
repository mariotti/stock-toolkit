"""Alpha Vantage fetcher (live + historical)."""

import time
from datetime import date

from .. import config as cfg
from ..config import log
from ..db import (
    make_row, _hist_has_data, _live_has_today,
)
from ..failures import is_suppressed, record_failure
from ..http import safe_get
from ..state import budget_ok, record_call

# ── 2. Alpha Vantage ─────────────────────────
def _av_error(data: dict) -> str:
    """Extract human-readable error from an Alpha Vantage response."""
    for key in ("Note", "Information", "Error Message"):
        if key in data:
            return data[key].split(".")[0]   # first sentence is enough
    keys = list(data.keys())[:3]
    return f"unexpected keys: {keys}"

def fetch_alphavantage(symbols: list[str], state: dict) -> list[dict]:
    """
    Free:  TIME_SERIES_DAILY          — unadjusted OHLCV.
    Paid:  TIME_SERIES_DAILY_ADJUSTED — split/dividend-adjusted close.
    Toggle via cfg.ALPHAVANTAGE_PAID = True in the config section.
    Free: 25 calls/day, 1 call/symbol.
    """
    key = cfg.API_KEYS["alphavantage"]
    if not key:
        return []

    if cfg.ALPHAVANTAGE_PAID:
        av_function = "TIME_SERIES_DAILY_ADJUSTED"
        close_field  = "5. adjusted close"
        vol_field    = "6. volume"
        extra_fields = lambda bar: {"split_coefficient": bar.get("8. split coefficient"),
                                    "dividend":          bar.get("7. dividend amount")}
    else:
        av_function  = "TIME_SERIES_DAILY"
        close_field  = "4. close"
        vol_field    = "5. volume"
        extra_fields = lambda bar: {}

    rows = []
    for sym in symbols:
        if is_suppressed(sym, "alphavantage"):
            log.info(f"[alphavantage] {sym}: suppressed after {cfg.FAILURE_THRESHOLD} failures — skipping")
            continue
        if _live_has_today(sym, "alphavantage"):
            log.info(f"[alphavantage] {sym}: already collected today, skipping")
            continue
        if not budget_ok(state, "alphavantage"):
            break
        data = safe_get(
            "https://www.alphavantage.co/query",
            params={"function": av_function, "symbol": sym,
                    "outputsize": "compact", "apikey": key}
        )
        record_call(state, "alphavantage")
        time.sleep(13)   # free tier: max 5 calls/min — always sleep, even on error
        if not data or "Time Series (Daily)" not in data:
            reason = _av_error(data) if data else "no response"
            log.warning(f"[alphavantage] {sym}: {reason}")
            record_failure(sym, "alphavantage", reason)
            continue

        for date_str, bar in data["Time Series (Daily)"].items():
            rows.append(make_row(
                sym, "alphavantage", date_str, "1d",
                bar.get("1. open"), bar.get("2. high"),
                bar.get("3. low"),  bar.get(close_field),
                bar.get(vol_field),
                extra=extra_fields(bar) or None
            ))
        tier = "adjusted" if cfg.ALPHAVANTAGE_PAID else "unadjusted"
        log.info(f"[alphavantage] {sym}: {len(data['Time Series (Daily)'])} days ({tier})")
    return rows


def _hist_alphavantage(symbols, db_path, date_from, date_to, state) -> list:
    """
    outputsize=full returns 20+ years in one call per symbol.
    Free:  TIME_SERIES_DAILY (unadjusted).
    Paid:  TIME_SERIES_DAILY_ADJUSTED — set cfg.ALPHAVANTAGE_PAID = True.
    Costs 1 call/symbol from the 25/day budget.
    """
    key = cfg.API_KEYS["alphavantage"]
    if not key:
        return []

    if cfg.ALPHAVANTAGE_PAID:
        av_function  = "TIME_SERIES_DAILY_ADJUSTED"
        close_field  = "5. adjusted close"
        vol_field    = "6. volume"
        extra_fields = lambda bar: {"split_coefficient": bar.get("8. split coefficient"),
                                    "dividend":          bar.get("7. dividend amount")}
    else:
        av_function  = "TIME_SERIES_DAILY"
        close_field  = "4. close"
        vol_field    = "5. volume"
        extra_fields = lambda bar: {}

    rows = []
    for sym in symbols:
        if not budget_ok(state, "alphavantage"):
            break
        if _hist_has_data(db_path, sym, "alphavantage", date_from, date_to):
            log.info(f"[hist/alphavantage] {sym}: already in DB, skipping")
            continue
        outputsize = "full" if cfg.ALPHAVANTAGE_PAID else "compact"
        if not cfg.ALPHAVANTAGE_PAID:
            log.info(f"[hist/alphavantage] {sym}: free tier — compact only "
                     f"(~100 days). Upgrade to paid or use yfinance/FMP for full history.")
        data = safe_get(
            "https://www.alphavantage.co/query",
            params={"function": av_function, "symbol": sym,
                    "outputsize": outputsize, "apikey": key}
        )
        record_call(state, "alphavantage")
        time.sleep(13)   # free tier: max 5 calls/min — sleep even on error
        if not data or "Time Series (Daily)" not in data:
            reason = _av_error(data) if data else "no response"
            log.warning(f"[hist/alphavantage] {sym}: {reason}")
            record_failure(sym, "alphavantage", reason)
            continue
        kept = 0
        for date_str, bar in data["Time Series (Daily)"].items():
            d = date.fromisoformat(date_str)
            if not (date_from <= d <= date_to):
                continue
            rows.append(make_row(
                sym, "alphavantage", date_str, "1d",
                bar.get("1. open"), bar.get("2. high"),
                bar.get("3. low"),  bar.get(close_field),
                bar.get(vol_field),
                extra=extra_fields(bar) or None
            ))
            kept += 1
        tier = "adjusted" if cfg.ALPHAVANTAGE_PAID else "unadjusted"
        log.info(f"[hist/alphavantage] {sym}: {kept} bars in range ({tier})")
    return rows


