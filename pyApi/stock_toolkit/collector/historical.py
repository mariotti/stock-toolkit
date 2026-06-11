"""Historical collection: date-range parsing and per-source orchestration."""

from datetime import date
from pathlib import Path

from . import config as cfg
from .config import log
from .db import db_connect, db_insert_rows
from .sources import (
    _hist_alphavantage, _hist_finnhub, _hist_fmp, _hist_polygon,
    _hist_twelvedata, _hist_yfinance,
)

# ─────────────────────────────────────────────
#  HISTORICAL
# ─────────────────────────────────────────────

def parse_historical_arg(val: str) -> "tuple[date, date, str]":
    """
    Parse the --historical argument.

    Examples:
        "ALL"       -> (1970-01-01, today,      "all")
        "2026"      -> (2026-01-01, 2026-12-31, "2026")
        "2000-2015" -> (2000-01-01, 2015-12-31, "2000-2015")
    """
    val = val.strip()
    if val.upper() == "ALL":
        return date(1970, 1, 1), date.today(), "all"
    parts = val.split("-")
    if len(parts) == 2 and all(len(p) == 4 and p.isdigit() for p in parts):
        y1, y2 = int(parts[0]), int(parts[1])
        if y1 > y2:
            y1, y2 = y2, y1
        return date(y1, 1, 1), date(y2, 12, 31), f"{y1}-{y2}"
    if len(parts) == 1 and len(parts[0]) == 4 and parts[0].isdigit():
        y = int(parts[0])
        return date(y, 1, 1), date(y, 12, 31), str(y)
    raise ValueError(
        f"Invalid --historical value: '{val}'.  Use YYYY, YYYY-YYYY, or ALL."
    )


# ── orchestrator ───────────────────────────────────────────────

def run_historical(symbols: list, hist_arg: str, state: dict) -> "Path":
    """
    Fetch historical data for the given range and persist to a dedicated DB.
    Returns the DB path (used by plotting).

    The DB is named stock_data_<suffix>.db, never touching stock_data.db.
    Re-running is safe: _hist_has_data() skips (symbol, source) pairs that
    already have rows in the target range.
    """
    date_from, date_to, suffix = parse_historical_arg(hist_arg)
    cfg.HIST_DIR.mkdir(parents=True, exist_ok=True)
    db_path = cfg.HIST_DIR / f"stock_data_{suffix}.db"

    log.info(f"Historical mode: {date_from} → {date_to}  →  {db_path.name}")
    db_connect(db_path).close()     # ensure schema exists

    all_rows: list = []

    all_rows += _hist_yfinance(symbols,      db_path, date_from, date_to, state)
    all_rows += _hist_alphavantage(symbols,  db_path, date_from, date_to, state)
    all_rows += _hist_finnhub(symbols,       db_path, date_from, date_to, state)
    all_rows += _hist_polygon(symbols,       db_path, date_from, date_to, state)
    all_rows += _hist_fmp(symbols,           db_path, date_from, date_to, state)
    all_rows += _hist_twelvedata(symbols,    db_path, date_from, date_to, state)
    # Marketstack skipped: monthly budget too tight for bulk historical loads

    added = db_insert_rows(all_rows, db_path=db_path)
    log.info(f"Historical: {len(all_rows)} rows fetched | {added} new rows "
             f"inserted into {db_path.name}")
    return db_path

