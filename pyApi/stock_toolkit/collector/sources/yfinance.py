"""yfinance fetcher (live + historical). No API key needed."""

import time
from datetime import date, timedelta

from .. import config as cfg
from ..config import log
from ..db import (
    make_row, _hist_has_data, _live_has_today, _hourly_bar_is_current,
)
from ..failures import is_suppressed, record_failure

# ── 1. yfinance (no key needed) ──────────────
def fetch_yfinance(symbols: list[str]) -> list[dict]:
    """
    Uses the yfinance Python library to pull EOD history + the latest quote.
    No API key required. Unofficial — can break without notice.
    """
    try:
        import yfinance as yf
    except ImportError:
        log.warning("[yfinance] not installed — run: pip install yfinance")
        return []

    rows = []
    today = date.today()
    start = (today - timedelta(days=7)).isoformat()  # last 7 days of daily bars

    for sym in symbols:
        if is_suppressed(sym, "yfinance"):
            log.info(f"[yfinance] {sym}: suppressed after {cfg.FAILURE_THRESHOLD} failures — skipping")
            continue
        daily_done  = _live_has_today(sym, "yfinance", "1d")
        hourly_done = _hourly_bar_is_current(sym, "yfinance")
        if daily_done and hourly_done:
            log.info(f"[yfinance] {sym}: daily done + hourly current, skipping")
            continue
        try:
            ticker = yf.Ticker(sym)

            # --- historical daily bars ---
            if not daily_done:
                hist = ticker.history(start=start, interval="1d")
                for ts, bar in hist.iterrows():
                    rows.append(make_row(
                        sym, "yfinance", ts.date(), "1d",
                        bar.get("Open"), bar.get("High"), bar.get("Low"), bar.get("Close"),
                        bar.get("Volume"),
                    ))
            else:
                hist = []

            # --- intraday 1-hour bars (last 5 days) ---
            if not hourly_done:
                intra = ticker.history(period="5d", interval="1h")
                for ts, bar in intra.iterrows():
                    rows.append(make_row(
                        sym, "yfinance", ts.isoformat(), "1h",
                        bar.get("Open"), bar.get("High"), bar.get("Low"), bar.get("Close"),
                        bar.get("Volume"),
                    ))
            else:
                intra = []

            if len(hist) == 0 and len(intra) == 0 and not (daily_done and hourly_done):
                record_failure(sym, "yfinance", "0 bars returned — possibly delisted")
            log.info(f"[yfinance] {sym}: {len(hist)} daily + {len(intra)} hourly bars")
            time.sleep(0.5)   # gentle pacing
        except Exception as e:
            log.error(f"[yfinance] {sym}: {e}")
            record_failure(sym, "yfinance", str(e)[:80])
    return rows


def _hist_yfinance(symbols, db_path, date_from, date_to, state) -> list:
    """yfinance: full date-range history, no API key needed."""
    try:
        import yfinance as yf
    except ImportError:
        log.warning("[hist/yfinance] not installed")
        return []
    rows = []
    for sym in symbols:
        if is_suppressed(sym, "yfinance"):
            log.info(f"[hist/yfinance] {sym}: suppressed after {cfg.FAILURE_THRESHOLD} failures — skipping")
            continue
        if _hist_has_data(db_path, sym, "yfinance", date_from, date_to):
            log.info(f"[hist/yfinance] {sym}: already in DB, skipping")
            continue
        try:
            hist = yf.Ticker(sym).history(
                start=str(date_from), end=str(date_to), interval="1d"
            )
            for ts, bar in hist.iterrows():
                rows.append(make_row(
                    sym, "yfinance", ts.date(), "1d",
                    bar.get("Open"), bar.get("High"),
                    bar.get("Low"),  bar.get("Close"), bar.get("Volume"),
                ))
            log.info(f"[hist/yfinance] {sym}: {len(hist)} bars")
            time.sleep(0.5)
        except Exception as e:
            log.error(f"[hist/yfinance] {sym}: {e}")
    return rows


