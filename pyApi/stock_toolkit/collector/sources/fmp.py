"""Financial Modeling Prep fetcher (live + historical)."""

import time
from datetime import datetime, date, timedelta, timezone

from .. import config as cfg
from ..config import log
from ..db import (
    make_row, _hist_has_data, _live_has_today, _quote_is_fresh,
)
from ..failures import is_suppressed, record_failure
from ..http import safe_get
from ..state import budget_ok, record_call

# ── 5. Financial Modeling Prep (FMP) ──────────
def fetch_fmp(symbols: list[str], state: dict) -> list[dict]:
    """
    /stable/quote                    — real-time snapshot (1 call for ALL symbols).
    /stable/historical-price-eod/full — EOD OHLCV per symbol.
    Stable API (replaces legacy /api/v3/ which is now subscription-only).
    Free: 250 calls/day.
    """
    key = cfg.API_KEYS["fmp"]
    if not key:
        return []

    BASE = "https://financialmodelingprep.com/stable"
    rows = []

    # --- bulk quote (1 call for all symbols) ---
    quote_pending = [s for s in symbols if not _quote_is_fresh(s, "fmp")]
    if not quote_pending:
        log.info("[fmp] all symbols have fresh quotes, skipping bulk quote")
        q_data = []
    else:
        sym_str = ",".join(quote_pending)
        q_data = safe_get(f"{BASE}/quote",
                          params={"symbol": sym_str, "apikey": key})
        record_call(state, "fmp")
        if q_data is None:
            log.warning("[fmp] quote: no response — check key or network")
            q_data = []
        elif isinstance(q_data, dict) and "message" in q_data:
            log.warning(f"[fmp] quote error: {q_data.get('message','?')}")
            q_data = []

    if isinstance(q_data, list):
        for q in q_data:
            rows.append(make_row(
                q["symbol"], "fmp", datetime.now(timezone.utc).date(), "1d",
                q.get("open"), q.get("dayHigh"), q.get("dayLow"),
                q.get("price"), q.get("volume"),
                change_pct=q.get("changesPercentage"),
                extra={"market_cap": q.get("marketCap"), "pe": q.get("pe"),
                       "eps": q.get("eps"), "52w_high": q.get("yearHigh"),
                       "52w_low": q.get("yearLow")}
            ))
        if q_data:
            log.info(f"[fmp] bulk quote: {len(q_data)} symbols")
    elif isinstance(q_data, dict) and q_data.get("_error") == 402:
        log.info("[fmp] batch quote requires paid plan — skipping quotes (historical bars unaffected)")

    # --- historical EOD per symbol ---
    for sym in symbols:
        if is_suppressed(sym, "fmp"):
            log.info(f"[fmp] {sym}: suppressed after {cfg.FAILURE_THRESHOLD} failures — skipping")
            continue
        if _live_has_today(sym, "fmp"):
            log.info(f"[fmp] {sym}: already collected today, skipping")
            continue
        if not budget_ok(state, "fmp"):
            break
        from_date = (date.today() - timedelta(days=90)).isoformat()
        h_data = safe_get(
            f"{BASE}/historical-price-eod/full",
            params={"symbol": sym, "from": from_date, "apikey": key}
        )
        record_call(state, "fmp")
        if h_data is None:
            log.warning(f"[fmp] {sym}: no response")
            time.sleep(0.5)
            continue
        if isinstance(h_data, dict) and h_data.get("_error") == 402:
            log.info(f"[fmp] {sym}: requires paid plan — skipping (free tier covers major US large-caps only)")
            record_failure(sym, "fmp", "paid plan required (402)")
            time.sleep(0.5)
            continue
        # stable endpoint returns a list directly (not wrapped in {"historical": [...]})
        if isinstance(h_data, list):
            bars = h_data
        elif isinstance(h_data, dict):
            if "message" in h_data:
                log.warning(f"[fmp] {sym}: {h_data.get('message','API error')}")
                time.sleep(0.5)
                continue
            # fallback: old format still returned for some accounts
            bars = h_data.get("historical", [])
        else:
            log.warning(f"[fmp] {sym}: unexpected response type {type(h_data).__name__}")
            time.sleep(0.5)
            continue

        if not bars:
            log.warning(f"[fmp] {sym}: empty response")
            time.sleep(0.5)
            continue

        for bar in bars:
            rows.append(make_row(
                sym, "fmp", bar["date"], "1d",
                bar.get("open"), bar.get("high"), bar.get("low"), bar.get("close"),
                bar.get("volume"), vwap=bar.get("vwap"),
                change_pct=bar.get("changePercent"),
                extra={"adj_close": bar.get("adjClose"),
                       "unadjusted_volume": bar.get("unadjustedVolume")}
            ))
        log.info(f"[fmp] {sym}: {len(bars)} daily bars")
        time.sleep(0.5)
    return rows


def _hist_fmp(symbols, db_path, date_from, date_to, state) -> list:
    """FMP stable/historical-price-eod/full with from/to. 250 calls/day, 1 call/symbol."""
    key = cfg.API_KEYS["fmp"]
    if not key:
        return []
    BASE = "https://financialmodelingprep.com/stable"
    rows = []
    for sym in symbols:
        if not budget_ok(state, "fmp"):
            break
        if _hist_has_data(db_path, sym, "fmp", date_from, date_to):
            log.info(f"[hist/fmp] {sym}: already in DB, skipping")
            continue
        data = safe_get(
            f"{BASE}/historical-price-eod/full",
            params={"symbol": sym, "from": str(date_from),
                    "to": str(date_to), "apikey": key}
        )
        record_call(state, "fmp")
        if data is None:
            log.warning(f"[hist/fmp] {sym}: no response")
            record_failure(sym, "fmp", "no response")
            continue
        if isinstance(data, dict) and data.get("_error") == 402:
            log.info(f"[hist/fmp] {sym}: requires paid plan — skipping")
            record_failure(sym, "fmp", "paid plan required (402)")
            continue
        if isinstance(data, dict):
            if "message" in data:
                msg = data.get("message","API error")
                log.warning(f"[hist/fmp] {sym}: {msg}")
                record_failure(sym, "fmp", msg[:80])
                continue
            # old format fallback
            bars = data.get("historical", [])
        elif isinstance(data, list):
            bars = data
        else:
            log.warning(f"[hist/fmp] {sym}: unexpected type {type(data).__name__}")
            continue
        if not bars:
            log.warning(f"[hist/fmp] {sym}: empty response")
            record_failure(sym, "fmp", "empty response")
            continue
        for bar in bars:
            rows.append(make_row(
                sym, "fmp", bar["date"], "1d",
                bar.get("open"), bar.get("high"), bar.get("low"), bar.get("close"),
                bar.get("volume"), vwap=bar.get("vwap"),
                change_pct=bar.get("changePercent"),
                extra={"adj_close": bar.get("adjClose")}
            ))
        log.info(f"[hist/fmp] {sym}: {len(bars)} bars")
        time.sleep(0.5)
    return rows


