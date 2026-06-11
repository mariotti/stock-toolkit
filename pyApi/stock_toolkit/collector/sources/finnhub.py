"""Finnhub fetcher (live + historical)."""

import time
from datetime import datetime, date, timedelta, timezone

from .. import config as cfg
from ..config import log
from ..db import (
    make_row, _hist_has_data, _quote_is_fresh,
)
from ..failures import is_suppressed, record_failure
from ..http import safe_get, sleep_for_rate

# ── 3. Finnhub ────────────────────────────────
def fetch_finnhub(symbols: list[str], state: dict) -> list[dict]:
    """
    /quote — real-time last price snapshot (free tier).
    /stock/candle — OHLCV bars (paid tier, enabled via cfg.FINNHUB_PAID = True).
    Free: 60 calls/min, no daily cap.
    """
    key = cfg.API_KEYS["finnhub"]
    if not key:
        return []

    rows = []
    now_ts  = int(time.time())
    from_ts = int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp())

    for sym in symbols:
        if is_suppressed(sym, "finnhub"):
            log.info(f"[finnhub] {sym}: suppressed after {cfg.FAILURE_THRESHOLD} failures — skipping")
            continue
        if _quote_is_fresh(sym, "finnhub"):
            log.info(f"[finnhub] {sym}: quote is fresh, skipping")
            continue
        # real-time quote — free for US symbols; international needs paid plan
        q = safe_get("https://finnhub.io/api/v1/quote",
                     params={"symbol": sym, "token": key})
        sleep_for_rate("finnhub")
        if isinstance(q, dict) and q.get("_error") == 403:
            log.info(f"[finnhub] {sym}: not available on free tier "
                     f"(international symbols need a paid plan) — skipping")
            record_failure(sym, "finnhub", "paid plan required (403)")
            continue
        if q and q.get("c"):
            rows.append(make_row(
                sym, "finnhub", datetime.now(timezone.utc).date(), "1d",
                q.get("o"), q.get("h"), q.get("l"), q.get("c"), q.get("v"),
                change_pct=q.get("dp"),
                extra={"prev_close": q.get("pc"), "timestamp": q.get("t")}
            ))
            log.info(f"[finnhub] {sym}: quote c={q.get('c')}")
        else:
            log.warning(f"[finnhub] {sym}: empty or unexpected response")
            record_failure(sym, "finnhub", "empty or unexpected response")

        # daily candles — paid tier only
        if cfg.FINNHUB_PAID:
            c = safe_get("https://finnhub.io/api/v1/stock/candle",
                         params={"symbol": sym, "resolution": "D",
                                 "from": from_ts, "to": now_ts, "token": key})
            sleep_for_rate("finnhub")
            if c and c.get("s") == "ok":
                for i, ts in enumerate(c["t"]):
                    rows.append(make_row(
                        sym, "finnhub", date.fromtimestamp(ts), "1d",
                        c["o"][i], c["h"][i], c["l"][i], c["c"][i], c["v"][i],
                        vwap=c.get("vwap", [None]*len(c["t"]))[i] if "vwap" in c else None,
                    ))
                log.info(f"[finnhub] {sym}: {len(c['t'])} daily candles")
            else:
                log.warning(f"[finnhub] {sym}: candle error — {c.get('s') if c else 'no response'}")
    return rows


def _hist_finnhub(symbols, db_path, date_from, date_to, state) -> list:
    """
    Finnhub candle: arbitrary Unix timestamp range (paid tier only).
    Skipped automatically when cfg.FINNHUB_PAID = False.
    """
    key = cfg.API_KEYS["finnhub"]
    if not key or not cfg.FINNHUB_PAID:
        log.info("[hist/finnhub] skipped — set FINNHUB_PAID=true in config.env to enable")
        return []
    rows = []
    from_ts = int(datetime(date_from.year, date_from.month, date_from.day,
                           tzinfo=timezone.utc).timestamp())
    to_ts   = int(datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59,
                           tzinfo=timezone.utc).timestamp())
    for sym in symbols:
        if _hist_has_data(db_path, sym, "finnhub", date_from, date_to):
            log.info(f"[hist/finnhub] {sym}: already in DB, skipping")
            continue
        c = safe_get("https://finnhub.io/api/v1/stock/candle",
                     params={"symbol": sym, "resolution": "D",
                             "from": from_ts, "to": to_ts, "token": key})
        sleep_for_rate("finnhub")
        if not c or c.get("s") != "ok":
            reason = c.get("s") if c else "no response"
            log.warning(f"[hist/finnhub] {sym}: {reason}")
            record_failure(sym, "finnhub", reason)
            continue
        for i, ts in enumerate(c["t"]):
            rows.append(make_row(
                sym, "finnhub", date.fromtimestamp(ts), "1d",
                c["o"][i], c["h"][i], c["l"][i], c["c"][i], c["v"][i],
            ))
        log.info(f"[hist/finnhub] {sym}: {len(c['t'])} bars")
    return rows


