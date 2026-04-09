"""
stock_collector.py
==================
Collects stock market data from multiple free APIs and appends
to a SQLite database by default, deduplicating via a UNIQUE constraint.
Pass --csv to write to a legacy CSV file instead.

Designed to be run via cron every 10 or 30 minutes:
  */10 * * * * /usr/bin/python3 /path/to/stock_collector.py
  */30 * * * * /usr/bin/python3 /path/to/stock_collector.py

Install dependencies:
  pip install requests yfinance pandas

API keys: fill in the CONFIG section below.
Each API that is not configured (key left as "") will be skipped.
"""

import os
import csv
import json
import sqlite3
import time
import logging
import hashlib
import argparse
import requests
import pandas as pd
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

# ─────────────────────────────────────────────
#  CONFIG — loaded from config.env, with
#           hardcoded defaults as fallback
# ─────────────────────────────────────────────

def _load_config(config_path: Path) -> dict:
    """
    Parse a simple KEY=VALUE config file.
    - Lines starting with # are comments.
    - Inline comments (value # comment) are stripped.
    - Quoted values ("value" or 'value') have quotes stripped.
    - Missing file is silently ignored (defaults apply).
    """
    cfg: dict = {}
    if not config_path.exists():
        return cfg
    with open(config_path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            # strip inline comment (covers both "value # comment" and "  # comment")
            if val.startswith("#"):
                val = ""
            elif " #" in val:
                val = val[:val.index(" #")].strip()
            # strip matching quotes
            if (val.startswith('"') and val.endswith('"')) or \
               (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            cfg[key] = val
    return cfg


# Config file sits next to this script — keep it out of git (see .gitignore)
_CONFIG_PATH = Path(__file__).parent / "config.env"
_cfg = _load_config(_CONFIG_PATH)

if _cfg:
    _src = str(_CONFIG_PATH)
else:
    _src = "built-in defaults (config.env not found)"

# ── symbols ───────────────────────────────────────────────────────────────────

# SYMBOLS in config.env is a comma-separated list, e.g.:
#   SYMBOLS=AAPL,MSFT,GOOGL,AMZN,TSLA
_sym_raw = _cfg.get("SYMBOLS", "AAPL,MSFT,GOOGL,AMZN,TSLA")
SYMBOLS = [s.strip().upper() for s in _sym_raw.split(",") if s.strip()]

# SYMBOLS_IGNORE — symbols to never collect, even if in config or DB.
# Use this to block bare EU tickers (e.g. ENI, ENEL) that are duplicates
# of the exchange-suffixed versions (ENI.MI, ENEL.MI).
#   SYMBOLS_IGNORE=ENI,ENEL,CSMIB,SAP
_ignore_raw = _cfg.get("SYMBOLS_IGNORE", "")
SYMBOLS_IGNORE = {s.strip().upper() for s in _ignore_raw.split(",") if s.strip()}

# FAILURE_THRESHOLD — stop requesting a (symbol, source) pair after this many
# consecutive failures. Recorded in stock_failures.csv, editable by hand.
FAILURE_THRESHOLD = int(_cfg.get("FAILURE_THRESHOLD", "5"))


def _symbols_from_db() -> list[str]:
    """
    Return symbols from the live DB that have at least 2 daily bars.
    The threshold filters out symbols that were tried once and returned
    nothing useful (e.g. bare 'ENI' instead of 'ENI.MI') — a single
    stale row is not enough to keep a symbol in the collection loop.
    Returns an empty list if the DB does not exist yet.
    """
    if not DB_PATH.exists():
        return []
    try:
        import sqlite3 as _sq
        con = _sq.connect(DB_PATH)
        try:
            rows = con.execute(
                """SELECT symbol FROM prices WHERE interval='1d'
                   GROUP BY symbol HAVING COUNT(*) >= 2
                   ORDER BY symbol"""
            ).fetchall()
        finally:
            con.close()
        return [r[0] for r in rows]
    except Exception:
        return []

# ── API keys ──────────────────────────────────────────────────────────────────

API_KEYS = {
    "alphavantage": _cfg.get("ALPHAVANTAGE_KEY", ""),
    "finnhub":      _cfg.get("FINNHUB_KEY",      ""),
    "polygon":      _cfg.get("MASSIVE_KEY", "") or _cfg.get("POLYGON_KEY", ""),   # MASSIVE_KEY preferred; POLYGON_KEY accepted for backward compatibility
    "fmp":          _cfg.get("FMP_KEY",           ""),
    "twelvedata":   _cfg.get("TWELVEDATA_KEY",    ""),
    "marketstack":  _cfg.get("MARKETSTACK_KEY",   ""),
}

# ── paid tier flags ───────────────────────────────────────────────────────────

# FINNHUB_PAID=true    → unlocks /stock/candle (OHLCV bars)
# ALPHAVANTAGE_PAID=true → unlocks TIME_SERIES_DAILY_ADJUSTED + full history
FINNHUB_PAID      = _cfg.get("FINNHUB_PAID",      "false").lower() == "true"
ALPHAVANTAGE_PAID = _cfg.get("ALPHAVANTAGE_PAID", "false").lower() == "true"

# ── paths ─────────────────────────────────────────────────────────────────────

OUTPUT_DIR     = Path(_cfg.get("OUTPUT_DIR", str(Path(__file__).parent)))
DB_PATH        = OUTPUT_DIR / _cfg.get("DB_FILE",      "stock_data.db")
CSV_PATH       = OUTPUT_DIR / _cfg.get("CSV_FILE",     "stock_data.csv")
STATE_PATH     = OUTPUT_DIR / _cfg.get("STATE_FILE",   ".collector_state.json")
LOG_PATH       = OUTPUT_DIR / _cfg.get("LOG_FILE",     "collector.log")
GNUPLOT_DIR    = OUTPUT_DIR / _cfg.get("GNUPLOT_DIR",  "gnuplot-data")
MATPLOTLIB_DIR = OUTPUT_DIR / _cfg.get("MATPLOT_DIR",  "matplot")
HIST_DIR       = OUTPUT_DIR / _cfg.get("HIST_DIR",     "data")
FAILURES_DB_PATH     = OUTPUT_DIR / _cfg.get("FAILURES_DB",     "stock_failures.db")
FAILURES_REPORT_PATH = OUTPUT_DIR / _cfg.get("FAILURES_REPORT", "stock_failures_report.csv")

# ── rate limits (not user-configurable via config.env) ───────────────────────

DAILY_LIMITS = {
    "alphavantage": 25,    # 25 calls / day
    "finnhub":      None,  # 60 calls / minute — no daily cap, handled below
    "polygon":      None,  # 5 calls / minute  — no daily cap
    "fmp":          250,   # 250 calls / day
    "twelvedata":   800,   # 800 calls / day
    "marketstack":  3,     # 100 calls / month → ~3/day safety budget
}

MINUTE_LIMITS = {
    "finnhub":  60,
    "polygon":  5,
}

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)
log.debug(f"Config loaded from: {_src}")

# ─────────────────────────────────────────────
#  STATE — persists daily call counts across runs
# ─────────────────────────────────────────────

def load_state() -> dict:
    if STATE_PATH.exists():
        with open(STATE_PATH) as f:
            state = json.load(f)
        # reset counters if the stored date is not today
        if state.get("date") != str(date.today()):
            state = {"date": str(date.today()), "calls": {}}
    else:
        state = {"date": str(date.today()), "calls": {}}
    return state

def save_state(state: dict):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


# ─────────────────────────────────────────────
#  FAILURE TRACKER — stock_failures.csv
# ─────────────────────────────────────────────

def _failures_db_connect() -> sqlite3.Connection:
    """
    Open (and if needed initialise) the failures SQLite database.
    Separate from the main prices DB so it can be written to in real-time
    from parallel fetcher threads without interfering with price data.
    WAL mode allows concurrent readers alongside the writer.
    """
    con = sqlite3.connect(FAILURES_DB_PATH, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""
        CREATE TABLE IF NOT EXISTS failures (
            symbol      TEXT NOT NULL,
            source      TEXT NOT NULL,
            reason      TEXT,
            hits        INTEGER NOT NULL DEFAULT 0,
            first_seen  TEXT NOT NULL,
            last_seen   TEXT NOT NULL,
            PRIMARY KEY (symbol, source)
        )
    """)
    con.commit()
    return con

def record_failure(symbol: str, source: str, reason: str) -> None:
    """
    Record a failed fetch for (symbol, source) directly in the failures DB.
    Safe to call from parallel threads — SQLite serialises concurrent writes.
    When hits reach FAILURE_THRESHOLD, logs a warning once.
    """
    today = str(date.today())
    sym   = symbol.upper()
    con = _failures_db_connect()
    try:
        con.execute("""
            INSERT INTO failures (symbol, source, reason, hits, first_seen, last_seen)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(symbol, source) DO UPDATE SET
                hits      = hits + 1,
                reason    = excluded.reason,
                last_seen = excluded.last_seen
        """, (sym, source, reason, today, today))
        con.commit()
        # check if we just crossed the threshold
        hits = con.execute(
            "SELECT hits FROM failures WHERE symbol=? AND source=?",
            (sym, source)
        ).fetchone()[0]
        if hits == FAILURE_THRESHOLD:
            log.warning(
                f"[failures] {sym}/{source}: {FAILURE_THRESHOLD} failures "
                f"({reason}) — will be skipped automatically. "
                f"Reset: delete or UPDATE the row in {FAILURES_DB_PATH.name}"
            )
    finally:
        con.close()


def is_suppressed(symbol: str, source: str) -> bool:
    """
    Return True if (symbol, source) has reached the failure threshold.
    Queries the failures DB directly — no in-memory cache needed.
    """
    if not FAILURES_DB_PATH.exists():
        return False
    try:
        con = _failures_db_connect()
        try:
            row = con.execute(
                "SELECT hits FROM failures WHERE symbol=? AND source=?",
                (symbol.upper(), source)
            ).fetchone()
        finally:
            con.close()
        return row is not None and row[0] >= FAILURE_THRESHOLD
    except Exception:
        return False


def flush_failures() -> None:
    """
    Export the failures DB to a human-readable CSV report.
    Called once at end of run. The CSV is for inspection only —
    the DB is the authoritative source.
    """
    if not FAILURES_DB_PATH.exists():
        return
    try:
        con = _failures_db_connect()
        try:
            rows = con.execute("""
                SELECT symbol, source, reason, hits, first_seen, last_seen
                FROM failures
                ORDER BY hits DESC, symbol, source
            """).fetchall()
        finally:
            con.close()
        if not rows:
            return
        with open(FAILURES_REPORT_PATH, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["symbol", "source", "reason", "hits",
                             "first_seen", "last_seen"])
            writer.writerows(rows)
        suppressed = sum(1 for r in rows if r[3] >= FAILURE_THRESHOLD)
        log.info(f"[failures] report written to {FAILURES_REPORT_PATH.name} "
                 f"({len(rows)} entries, {suppressed} suppressed)")
    except Exception as e:
        log.warning(f"[failures] could not write report: {e}")
        with open(FAILURES_REPORT_PATH, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["symbol", "source", "reason", "hits",
                             "first_seen", "last_seen"])
            writer.writerows(rows)
        suppressed = sum(1 for r in rows if r[3] >= FAILURE_THRESHOLD)
        log.info(f"[failures] report written to {FAILURES_REPORT_PATH.name} "
                 f"({len(rows)} entries, {suppressed} suppressed)")
    except Exception as e:
        log.warning(f"[failures] could not write report: {e}")

def budget_ok(state: dict, source: str) -> bool:
    limit = DAILY_LIMITS.get(source)
    if limit is None:
        return True   # no daily cap
    used = state["calls"].get(source, 0)
    if used >= limit:
        log.warning(f"[{source}] daily budget exhausted ({used}/{limit}), skipping.")
        return False
    return True

def record_call(state: dict, source: str, n: int = 1):
    state["calls"][source] = state["calls"].get(source, 0) + n

# ─────────────────────────────────────────────
#  SHARED — column definitions
# ─────────────────────────────────────────────

FIELDNAMES = [
    "fetched_at",   # ISO timestamp of when we collected it
    "symbol",
    "source",       # which API
    "timestamp",    # ISO datetime the OHLCV belongs to (always full datetime)
    "interval",     # computed hint: "1d", "1h", "1m", etc.
    "open",
    "high",
    "low",
    "close",
    "volume",
    "vwap",         # volume-weighted avg price (when available)
    "change_pct",   # % change from previous close (when available)
    "extra",        # JSON blob for any bonus fields (sentiment score, etc.)
]

# ─────────────────────────────────────────────
#  TIMESTAMP NORMALISATION
# ─────────────────────────────────────────────

def _to_timestamp(value) -> str:
    """
    Normalise any date/datetime value to a full ISO-8601 string with timezone.

    - Full datetime with tz  → kept as-is (re-formatted for consistency)
    - Full datetime without tz → assumed UTC
    - date / date-string      → midnight UTC (T00:00:00+00:00)
    - Unix integer timestamp  → converted to UTC datetime

    This means daily bars from APIs that only give a date (yfinance EOD,
    FMP, Marketstack, Alpha Vantage) are stored as midnight UTC.  The date
    part is still correct and consistent across sources; the time component
    is a neutral placeholder that makes the column uniformly a datetime.
    """
    from datetime import date as _date, datetime as _dt, timezone as _tz
    import pandas as _pd

    if isinstance(value, int):
        # Unix timestamp
        return _dt.fromtimestamp(value, tz=_tz.utc).isoformat(timespec="seconds")

    if isinstance(value, _pd.Timestamp):
        if value.tzinfo is None:
            value = value.tz_localize("UTC")
        return value.isoformat(timespec="seconds")

    if isinstance(value, _dt):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_tz.utc)
        return value.isoformat(timespec="seconds")

    if isinstance(value, _date):
        return _dt(value.year, value.month, value.day,
                   tzinfo=_tz.utc).isoformat(timespec="seconds")

    # string
    s = str(value).strip()
    if "T" in s or " " in s:
        # looks like a datetime string
        try:
            if "+" in s or s.endswith("Z") or s.count("-") > 2:
                # has timezone info
                dt = _dt.fromisoformat(s.replace("Z", "+00:00"))
            else:
                dt = _dt.fromisoformat(s).replace(tzinfo=_tz.utc)
            return dt.isoformat(timespec="seconds")
        except ValueError:
            pass
    # date-only string "YYYY-MM-DD"
    try:
        d = _date.fromisoformat(s[:10])
        return _dt(d.year, d.month, d.day,
                   tzinfo=_tz.utc).isoformat(timespec="seconds")
    except ValueError:
        pass

    # fallback — store as-is and let the caller deal with it
    return s


def _infer_interval(timestamp: str) -> str:
    """
    Infer the bar interval from the time component of a timestamp.
    Used as a fallback when the caller doesn't specify an interval.

    Midnight UTC (T00:00:00) → daily bar from a date-only source → "1d"
    Any other time           → assume hourly until proven otherwise → "1h"
    """
    return "1d" if "T00:00:00" in timestamp else "1h"

# ─────────────────────────────────────────────
#  SQLITE — default storage
# ─────────────────────────────────────────────

def db_connect(db_path: "Path | None" = None) -> sqlite3.Connection:
    """Open (and if needed initialise) the SQLite database."""
    con = sqlite3.connect(db_path or DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")   # safe for concurrent readers

    # ── schema: new timestamp-based layout ───────────────────────────────────
    # timestamp replaces data_date — always a full ISO-8601 datetime with tz.
    # interval is kept as a computed hint for fast filtering but is derived
    # from the timestamp gap, not trusted as the source of truth.
    # UNIQUE key: (symbol, source, timestamp) — interval dropped because the
    # timestamp is now precise enough to identify a bar uniquely per source.
    con.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            fetched_at  TEXT,
            symbol      TEXT    NOT NULL,
            source      TEXT    NOT NULL,
            timestamp   TEXT    NOT NULL,
            interval    TEXT    NOT NULL,
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL,
            volume      INTEGER,
            vwap        REAL,
            change_pct  REAL,
            extra       TEXT,
            UNIQUE (symbol, source, timestamp)
        )
    """)

    # ── migration 1: rename data_date → timestamp, drop interval from key ─────
    # Runs once: detects old schema by presence of data_date column.
    # NOTE: CREATE INDEX on (symbol, timestamp) is deferred until after
    # migration — on an old DB the timestamp column doesn't exist yet.
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(prices)").fetchall()}
        if "data_date" in cols:
            log.info("[db_connect] migrating schema: data_date → timestamp")
            con.execute("""
                CREATE TABLE IF NOT EXISTS prices_new (
                    fetched_at  TEXT,
                    symbol      TEXT    NOT NULL,
                    source      TEXT    NOT NULL,
                    timestamp   TEXT    NOT NULL,
                    interval    TEXT    NOT NULL,
                    open        REAL,
                    high        REAL,
                    low         REAL,
                    close       REAL,
                    volume      INTEGER,
                    vwap        REAL,
                    change_pct  REAL,
                    extra       TEXT,
                    UNIQUE (symbol, source, timestamp)
                )
            """)
            # Copy rows: normalise data_date to full ISO timestamp
            # date-only → midnight UTC; already-full datetimes → kept as-is
            con.execute("""
                INSERT OR IGNORE INTO prices_new
                SELECT
                    fetched_at, symbol, source,
                    CASE
                        WHEN length(data_date) = 10
                        THEN data_date || 'T00:00:00+00:00'
                        ELSE data_date
                    END AS timestamp,
                    interval,
                    open, high, low, close, volume, vwap, change_pct, extra
                FROM prices
            """)
            con.execute("DROP TABLE prices")
            con.execute("ALTER TABLE prices_new RENAME TO prices")
            con.commit()
            n = con.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
            log.info(f"[db_connect] schema migration complete — {n} rows")
    except Exception as e:
        log.warning(f"[db_connect] migration error: {e}")

    # Create index after migration — timestamp column is guaranteed to exist now
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_symbol_ts
        ON prices (symbol, timestamp)
    """)

    # ── migration 2: interval='quote' → '1d' (legacy cleanup) ────────────────
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM prices WHERE interval='quote'"
        ).fetchone()[0]
        if n > 0:
            con.execute("""
                DELETE FROM prices
                WHERE interval='quote'
                AND EXISTS (
                    SELECT 1 FROM prices p2
                    WHERE p2.symbol    = prices.symbol
                    AND   p2.source    = prices.source
                    AND   p2.timestamp = prices.timestamp
                    AND   p2.interval  = '1d'
                )
            """)
            con.execute("UPDATE prices SET interval='1d' WHERE interval='quote'")
            con.commit()
            log.info(f"[db_connect] migrated {n} legacy 'quote' rows → '1d'")
    except Exception:
        pass

    con.commit()
    return con

def db_insert_rows(rows: list[dict], db_path: "Path | None" = None) -> int:
    """Insert rows, silently skipping duplicates via UNIQUE constraint."""
    if not rows:
        return 0
    con = db_connect(db_path)
    try:
        cur = con.executemany(
            """INSERT OR IGNORE INTO prices
               (fetched_at, symbol, source, timestamp, interval,
                open, high, low, close, volume, vwap, change_pct, extra)
               VALUES
               (:fetched_at, :symbol, :source, :timestamp, :interval,
                :open, :high, :low, :close, :volume, :vwap, :change_pct, :extra)
            """,
            rows,
        )
        con.commit()
        return cur.rowcount
    finally:
        con.close()

def dedup_key(row: dict) -> str:
    """Fingerprint for a row — used by the CSV path to detect duplicates."""
    raw = f"{row['symbol']}|{row['source']}|{row['timestamp']}|{row['interval']}"
    return hashlib.md5(raw.encode()).hexdigest()

def load_existing_keys() -> set:
    """Read the CSV and build a set of already-seen dedup keys."""
    if not CSV_PATH.exists():
        return set()
    seen = set()
    with open(CSV_PATH, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            seen.add(dedup_key(row))
    return seen

def csv_append_rows(rows: list[dict], seen: set) -> int:
    """Append new rows to the CSV, skipping duplicates."""
    new_rows = []
    for row in rows:
        k = dedup_key(row)
        if k not in seen:
            seen.add(k)
            new_rows.append(row)

    if not new_rows:
        return 0

    write_header = not CSV_PATH.exists()
    with open(CSV_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(new_rows)
    return len(new_rows)

def make_row(symbol, source, ts, interval, o, h, l, c, v,
             vwap=None, change_pct=None, extra=None) -> dict:
    """
    Build a price row dict ready for db_insert_rows.

    ts       — any date/datetime/string/unix-int; normalised to full ISO via _to_timestamp()
    interval — "1d", "1h", "1m", etc.  Pass None to auto-infer from the timestamp.
    """
    timestamp = _to_timestamp(ts)
    return {
        "fetched_at":  datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "symbol":      symbol.upper(),
        "source":      source,
        "timestamp":   timestamp,
        "interval":    interval if interval is not None else _infer_interval(timestamp),
        "open":        round(float(o), 4) if o not in (None, "") else "",
        "high":        round(float(h), 4) if h not in (None, "") else "",
        "low":         round(float(l), 4) if l not in (None, "") else "",
        "close":       round(float(c), 4) if c not in (None, "") else "",
        "volume":      int(float(v)) if v not in (None, "") else "",
        "vwap":        round(float(vwap), 4) if vwap not in (None, "") else "",
        "change_pct":  round(float(change_pct), 4) if change_pct not in (None, "") else "",
        "extra":       json.dumps(extra) if extra else "",
    }

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
    limit = MINUTE_LIMITS.get(source)
    if limit:
        time.sleep(60 / limit + 0.1)   # e.g. 1.1 s between Polygon calls

# ─────────────────────────────────────────────
#  FETCHERS — one per API
# ─────────────────────────────────────────────

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
            log.debug(f"[yfinance] {sym}: suppressed (too many failures)")
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
    Toggle via ALPHAVANTAGE_PAID = True in the config section.
    Free: 25 calls/day, 1 call/symbol.
    """
    key = API_KEYS["alphavantage"]
    if not key:
        return []

    if ALPHAVANTAGE_PAID:
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
            log.debug(f"[alphavantage] {sym}: suppressed (too many failures)")
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
        tier = "adjusted" if ALPHAVANTAGE_PAID else "unadjusted"
        log.info(f"[alphavantage] {sym}: {len(data['Time Series (Daily)'])} days ({tier})")
    return rows


# ── 3. Finnhub ────────────────────────────────
def fetch_finnhub(symbols: list[str], state: dict) -> list[dict]:
    """
    /quote — real-time last price snapshot (free tier).
    /stock/candle — OHLCV bars (paid tier, enabled via FINNHUB_PAID = True).
    Free: 60 calls/min, no daily cap.
    """
    key = API_KEYS["finnhub"]
    if not key:
        return []

    rows = []
    now_ts  = int(time.time())
    from_ts = int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp())

    for sym in symbols:
        if is_suppressed(sym, "finnhub"):
            log.debug(f"[finnhub] {sym}: suppressed (too many failures)")
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
        if FINNHUB_PAID:
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


# ── 4. Massive (formerly Polygon.io) ───────────
def fetch_polygon(symbols: list[str], state: dict) -> list[dict]:
    """
    /v2/aggs/ticker/{sym}/range — OHLCV bars (daily, last 30 days).
    Free: 5 calls/min, delayed data.
    """
    key = API_KEYS["polygon"]
    if not key:
        return []

    rows = []
    to_date   = date.today().isoformat()
    from_date = (date.today() - timedelta(days=30)).isoformat()

    for sym in symbols:
        if is_suppressed(sym, "polygon"):
            log.debug(f"[polygon] {sym}: suppressed (too many failures)")
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
        log.info(f"[polygon] {sym}: {len(data.get('results', []))} daily bars")
    return rows


# ── 5. Financial Modeling Prep (FMP) ──────────
def fetch_fmp(symbols: list[str], state: dict) -> list[dict]:
    """
    /stable/quote                    — real-time snapshot (1 call for ALL symbols).
    /stable/historical-price-eod/full — EOD OHLCV per symbol.
    Stable API (replaces legacy /api/v3/ which is now subscription-only).
    Free: 250 calls/day.
    """
    key = API_KEYS["fmp"]
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
            log.debug(f"[fmp] {sym}: suppressed (too many failures)")
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


# ── 6. Twelve Data ────────────────────────────
def fetch_twelvedata(symbols: list[str], state: dict) -> list[dict]:
    """
    /time_series — OHLCV bars (daily + 1h) per symbol.
    Free: 800 credits/day, 8 credits/minute (1 credit per symbol per request).
    Symbols are batched in groups of 8 with a 62-second sleep between batches
    to stay within the per-minute limit.
    """
    key = API_KEYS["twelvedata"]
    if not key:
        return []

    rows = []
    # filter to symbols not yet collected for each interval
    symbols_1d = [s for s in symbols if not _live_has_today(s, "twelvedata", "1d")]
    symbols_1h = [s for s in symbols if not _hourly_bar_is_current(s, "twelvedata")]
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
                log.info(f"[twelvedata] rate-limit pause 62s before next batch…")
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


# ── 7. Marketstack ────────────────────────────
def fetch_marketstack(symbols: list[str], state: dict) -> list[dict]:
    """
    /eod — end-of-day OHLCV for multiple symbols in one call.
    V2 API (v1 deprecated June 2025). HTTPS available on all plans.
    Free: 100 calls/month. Budget conservatively — only fetch when budget allows.
    """
    key = API_KEYS["marketstack"]
    if not key:
        return []
    if not budget_ok(state, "marketstack"):
        return []

    # skip suppressed symbols and those already collected today
    pending = [s for s in symbols
               if not is_suppressed(s, "marketstack")
               and not _live_has_today(s, "marketstack")]
    if not pending:
        log.info("[marketstack] all symbols already collected today, skipping")
        return []
    rows = []
    sym_str = ",".join(pending)
    data = safe_get(
        "https://api.marketstack.com/v2/eod",
        params={"access_key": key, "symbols": sym_str, "limit": 100}
    )
    if not data:
        log.warning("[marketstack] no response — check network or API key")
        return []
    if "error" in data:
        err = data["error"]
        log.warning(f"[marketstack] API error {err.get('code','?')}: "
                    f"{err.get('message','unknown')}")
        return []
    if "data" not in data:
        log.warning(f"[marketstack] unexpected response keys: {list(data.keys())}")
        return []
    record_call(state, "marketstack")   # only count successful calls
    returned_syms = set()
    for bar in data["data"]:
        sym = bar["symbol"].split(".")[0]
        returned_syms.add(sym)
        rows.append(make_row(
            sym, "marketstack",
            bar["date"][:10], "1d",
            bar.get("open"), bar.get("high"), bar.get("low"), bar.get("close"),
            bar.get("volume"), vwap=bar.get("adj_close"),
            extra={"exchange": bar.get("exchange")}
        ))
    # record failures for symbols that returned no data
    for sym in pending:
        if sym not in returned_syms:
            record_failure(sym, "marketstack", "no data returned")
    log.info(f"[marketstack] {len(data['data'])} EOD bars across {len(pending)} symbols")
    return rows




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


def _hist_has_data(db_path: "Path", symbol: str, source: str,
                   date_from: "date", date_to: "date") -> bool:
    """
    Return True if (symbol, source) already has at least one daily row
    in [date_from, date_to].  Used to skip re-fetching.
    """
    if not db_path.exists():
        return False
    try:
        con = sqlite3.connect(db_path)
        try:
            n = con.execute(
                "SELECT COUNT(*) FROM prices "
                "WHERE symbol=? AND source=? AND interval='1d' "
                "AND timestamp>=? AND timestamp<=?",
                (symbol, source, str(date_from), str(date_to))
            ).fetchone()[0]
        finally:
            con.close()
        return n > 0
    except Exception:
        return False



def _live_has_today(symbol: str, source: str, interval: str = "1d") -> bool:
    """
    Return True if (symbol, source, interval) already has a row for today
    in the live DB. Checks timestamp LIKE 'today%' — works for both
    date-only-origin rows (stored as 2026-03-31T00:00:00+00:00) and
    full intraday timestamps.
    """
    if not DB_PATH.exists():
        return False
    today_str = str(date.today())
    try:
        con = sqlite3.connect(DB_PATH)
        try:
            n = con.execute(
                "SELECT COUNT(*) FROM prices "
                "WHERE symbol=? AND source=? AND interval=? AND timestamp LIKE ?",
                (symbol, source, interval, today_str + "%")
            ).fetchone()[0]
        finally:
            con.close()
        return n > 0
    except Exception:
        return False



def _quote_is_fresh(symbol: str, source: str, minutes: int = 25) -> bool:
    """
    Return True if (symbol, source, interval='1d') has a row inserted
    within the last `minutes` minutes.  Used by real-time quote fetchers
    (Finnhub, FMP) so a 30-min cron gets a fresh snapshot every run instead
    of being blocked by the first collection of the day.
    Quote data is now stored as interval='1d' so it merges cleanly with
    EOD bars and is visible to all analysis tools.
    """
    if not DB_PATH.exists():
        return False
    try:
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        con = sqlite3.connect(DB_PATH)
        try:
            n = con.execute(
                "SELECT COUNT(*) FROM prices "
                "WHERE symbol=? AND source=? AND interval='1d' "
                "AND fetched_at >= ?",
                (symbol, source, cutoff)
            ).fetchone()[0]
        finally:
            con.close()
        return n > 0
    except Exception:
        return False


def _hourly_bar_is_current(symbol: str, source: str) -> bool:
    """
    Return True if (symbol, source, interval='1h') already has a bar whose
    timestamp falls within the current UTC hour.  Allows hourly-bar fetchers
    to collect fresh bars on each cron run within the same day.
    """
    if not DB_PATH.exists():
        return False
    try:
        from datetime import datetime, timezone
        now        = datetime.now(timezone.utc)
        hour_start = now.strftime("%Y-%m-%dT%H:")   # e.g. "2026-03-31T14:"
        con = sqlite3.connect(DB_PATH)
        try:
            n = con.execute(
                "SELECT COUNT(*) FROM prices "
                "WHERE symbol=? AND source=? AND interval='1h' "
                "AND timestamp LIKE ?",
                (symbol, source, hour_start + "%")
            ).fetchone()[0]
        finally:
            con.close()
        return n > 0
    except Exception:
        return False


# ── historical fetchers (one per API) ───────────────────────────────

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
            log.debug(f"[hist/yfinance] {sym}: suppressed")
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


def _hist_alphavantage(symbols, db_path, date_from, date_to, state) -> list:
    """
    outputsize=full returns 20+ years in one call per symbol.
    Free:  TIME_SERIES_DAILY (unadjusted).
    Paid:  TIME_SERIES_DAILY_ADJUSTED — set ALPHAVANTAGE_PAID = True.
    Costs 1 call/symbol from the 25/day budget.
    """
    key = API_KEYS["alphavantage"]
    if not key:
        return []

    if ALPHAVANTAGE_PAID:
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
        outputsize = "full" if ALPHAVANTAGE_PAID else "compact"
        if not ALPHAVANTAGE_PAID:
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
        tier = "adjusted" if ALPHAVANTAGE_PAID else "unadjusted"
        log.info(f"[hist/alphavantage] {sym}: {kept} bars in range ({tier})")
    return rows


def _hist_finnhub(symbols, db_path, date_from, date_to, state) -> list:
    """
    Finnhub candle: arbitrary Unix timestamp range (paid tier only).
    Skipped automatically when FINNHUB_PAID = False.
    """
    key = API_KEYS["finnhub"]
    if not key or not FINNHUB_PAID:
        log.info("[hist/finnhub] skipped — set FINNHUB_PAID = True to enable")
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


def _hist_polygon(symbols, db_path, date_from, date_to, state) -> list:
    """
    Polygon /v2/aggs range endpoint with automatic pagination.
    5 calls/min free.  Note: free tier history may be limited to ~2 years.
    """
    key = API_KEYS["polygon"]
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


def _hist_fmp(symbols, db_path, date_from, date_to, state) -> list:
    """FMP stable/historical-price-eod/full with from/to. 250 calls/day, 1 call/symbol."""
    key = API_KEYS["fmp"]
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


def _hist_twelvedata(symbols, db_path, date_from, date_to, state) -> list:
    """
    Twelve Data time_series: outputsize=5000 covers ~19 years per call.
    For longer ranges the range is split into 19-year chunks.
    800 calls/day free.
    """
    key = API_KEYS["twelvedata"]
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
    HIST_DIR.mkdir(parents=True, exist_ok=True)
    db_path = HIST_DIR / f"stock_data_{suffix}.db"

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

# ─────────────────────────────────────────────
#  PLOTTING
# ─────────────────────────────────────────────

PLOT_FIELDS = ["close", "open", "high", "low", "volume", "vwap", "change_pct"]

# gnuplot tab-file column index for each field (col 1 = date)
_GNUPLOT_COL = {
    "open": 2, "high": 3, "low": 4, "close": 5,
    "volume": 6, "vwap": 7, "change_pct": 8,
}

def _load_plot_data(symbols: list[str], use_csv: bool, field: str,
                     db_path: "Path | None" = None) -> "pd.DataFrame":
    """
    Load daily rows from SQLite or CSV.
    Returns a tidy DataFrame: symbol, source, timestamp, <field>
    db_path overrides DB_PATH (used by --historical to point at the right DB).
    """
    if use_csv:
        if not CSV_PATH.exists():
            log.warning("[plot] No CSV file found — run a collection first.")
            return pd.DataFrame()
        df = pd.read_csv(CSV_PATH)
    else:
        active_db = db_path or DB_PATH
        if not active_db.exists():
            log.warning(f"[plot] {active_db.name} not found — run a collection first.")
            return pd.DataFrame()
        import sqlite3
        con = sqlite3.connect(active_db)
        try:
            df = pd.read_sql("SELECT * FROM prices", con)
        finally:
            con.close()

    df = df[df["interval"] == "1d"].copy()
    df = df[df["symbol"].isin([s.upper() for s in symbols])]
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp", field])
    df[field] = pd.to_numeric(df[field], errors="coerce")
    df = df.dropna(subset=[field])
    df = df.sort_values("timestamp")
    return df[["symbol", "source", "timestamp", field]].reset_index(drop=True)


# ── gnuplot ───────────────────────────────────────

def plot_gnuplot(symbols: list[str], use_csv: bool, field: str,
                 db_path: "Path | None" = None):
    """
    Writes one .dat file per symbol and a master stock_plot.gp script.

    Each .dat file uses gnuplot index blocks (separated by two blank lines),
    one block per data source, so every source appears as a separate line.

    Run the output with:
        gnuplot stock_plot.gp          # saves stock_plot_<field>.png
        gnuplot -p stock_plot.gp       # interactive window + PNG
    """
    df = _load_plot_data(symbols, use_csv, field, db_path=db_path)
    if df.empty:
        log.warning("[gnuplot] No data available to plot.")
        return

    dat_meta = []   # (symbol, dat_path, [sources])
    GNUPLOT_DIR.mkdir(parents=True, exist_ok=True)

    # ── write one .dat per symbol ────────────────────────────────────────────────────
    for sym in sorted(df["symbol"].unique()):
        sym_df  = df[df["symbol"] == sym]
        sources = sorted(sym_df["source"].unique())
        dat_path = GNUPLOT_DIR / f"stock_gnuplot_{sym}.dat"

        with open(dat_path, "w") as f:
            f.write(f"# stock_collector.py — {sym}  field: {field}\n")
            f.write(f"# columns: date  {field}\n\n")
            for src in sources:
                grp = sym_df[sym_df["source"] == src].sort_values("timestamp")
                f.write(f"# --- index block: source={src} ---\n")
                for _, row in grp.iterrows():
                    date_str = str(row["timestamp"])[:10]
                    f.write(f"{date_str}\t{row[field]}\n")
                f.write("\n\n")    # gnuplot index block separator (two blank lines)

        dat_meta.append((sym, dat_path, sources))
        log.info(f"[gnuplot] wrote {dat_path.name}  ({len(sources)} source(s))")

    # ── write master .gp script ────────────────────────────────────────────────────
    gp_path  = GNUPLOT_DIR / "stock_plot.gp"
    png_name = f"stock_plot_{field}.png"
    ylabel   = field.replace("_", " ").title()
    sym_list = ", ".join(sorted(df["symbol"].unique()))

    with open(gp_path, "w") as f:
        f.write("# Generated by stock_collector.py\n")
        f.write("# Render to PNG:        gnuplot gnuplot-data/stock_plot.gp\n")
        f.write("# Interactive + PNG:    gnuplot -p gnuplot-data/stock_plot.gp\n\n")

        # terminal / output
        f.write("set terminal pngcairo size 1400,600 enhanced font 'Arial,11'\n")
        f.write(f"set output '{png_name}'\n\n")

        # axes
        f.write("set xdata time\n")
        f.write("set timefmt '%Y-%m-%d'\n")
        f.write("set format x '%b %d'\n")
        f.write("set xlabel 'Date'\n")
        f.write(f"set ylabel '{ylabel}'\n")
        f.write(f"set title 'Stock data  —  {ylabel}  ({sym_list})'\n")
        f.write("set grid\n")
        f.write("set key outside right top\n\n")

        # plot command — one entry per (symbol, source) pair
        plot_lines = []
        for sym, dat_path, sources in dat_meta:
            for idx, src in enumerate(sources):
                plot_lines.append(
                    f"  '{dat_path.name}' index {idx}"
                    f" using 1:2 with linespoints pt 7 ps 0.5"
                    f" title '{sym} / {src}'"
                )
        f.write("plot \\\n" + ", \\\n".join(plot_lines) + "\n")

    log.info(f"[gnuplot] wrote {gp_path.name}  →  gnuplot {gp_path.name}")


# ── matplotlib ───────────────────────────────────────────────

def plot_matplotlib(symbols: list[str], use_csv: bool, field: str,
                    db_path: "Path | None" = None):
    """
    Plots with matplotlib: one line per (symbol, source) pair.
    Saves stock_plot_<field>.png in OUTPUT_DIR and opens an interactive window.
    """
    try:
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        log.error("[matplotlib] not installed — run:  pip install matplotlib")
        return

    df = _load_plot_data(symbols, use_csv, field, db_path=db_path)
    if df.empty:
        log.warning("[matplotlib] No data available to plot.")
        return

    fig, ax = plt.subplots(figsize=(14, 6))

    colors  = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
               "#9467bd", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]
    markers = ["o", "s", "^", "D", "v", "P", "X", "p", "h", "*"]

    idx = 0
    for sym in sorted(df["symbol"].unique()):
        for src in sorted(df[df["symbol"] == sym]["source"].unique()):
            grp = df[(df["symbol"] == sym) & (df["source"] == src)]
            ax.plot(
                grp["timestamp"], grp[field],
                label=f"{sym} / {src}",
                color=colors[idx % len(colors)],
                marker=markers[idx % len(markers)],
                markersize=3,
                linewidth=1.3,
            )
            idx += 1

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    fig.autofmt_xdate(rotation=35)

    ylabel   = field.replace("_", " ").title()
    sym_list = ", ".join(sorted(df["symbol"].unique()))
    ax.set_xlabel("Date")
    ax.set_ylabel(ylabel)
    ax.set_title(f"Stock data  —  {ylabel}  ({sym_list})")
    ax.legend(loc="upper left", fontsize=8, framealpha=0.75)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()

    MATPLOTLIB_DIR.mkdir(parents=True, exist_ok=True)
    out_path = MATPLOTLIB_DIR / f"stock_plot_{field}.png"
    plt.savefig(out_path, dpi=150)
    log.info(f"[matplotlib] saved {out_path.name}")
    plt.show()


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Stock market data collector")
    parser.add_argument(
        "-s", "--symbol",
        metavar="TICKER",
        help="Run only for this symbol (overrides the SYMBOLS list in config)",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Write to CSV instead of SQLite (legacy mode)",
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        metavar="SOURCE",
        choices=["yfinance","alphavantage","finnhub","polygon","fmp","twelvedata","marketstack"],
        help=(
            "Run only these data sources (default: all configured).\n"
            "Useful for cron jobs targeting specific collection frequencies:\n"
            "  --sources finnhub fmp          (every 30 min — real-time quotes)\n"
            "  --sources yfinance twelvedata  (every hour  — hourly bars)\n"
            "  --sources alphavantage polygon marketstack  (once/day — daily bars)"
        )
    )
    parser.add_argument(
        "--historical",
        metavar="RANGE",
        help=(
            "Fetch historical data instead of live collection. "
            "RANGE: a year (2020), a range (2000-2015), or ALL. "
            "Saved to stock_data_<range>.db — never overwrites stock_data.db. "
            "Re-running is safe: already-loaded (symbol, source) pairs are skipped."
        ),
    )
    parser.add_argument(
        "--plot-gnuplot",
        action="store_true",
        help="Generate stock_gnuplot_<SYM>.dat + stock_plot.gp after collecting",
    )
    parser.add_argument(
        "--plot-matplotlib",
        action="store_true",
        help="Plot with matplotlib after collecting (saves PNG + opens window)",
    )
    parser.add_argument(
        "--plot-data",
        metavar="FIELD",
        default="close",
        choices=PLOT_FIELDS,
        help="Field to plot (default: close). Choices: " + ", ".join(PLOT_FIELDS),
    )
    args = parser.parse_args()

    # ── symbol resolution ─────────────────────────────────────────────────────
    # Priority:
    #   1. -s / --symbol flag  → explicit override, use exactly that symbol
    #      (SYMBOLS_IGNORE still applies even with -s)
    #   2. No flag             → config SYMBOLS ∪ symbols already in the DB,
    #      minus anything in SYMBOLS_IGNORE
    if args.symbol:
        sym = args.symbol.upper()
        if sym in SYMBOLS_IGNORE:
            log.warning(f"Symbol '{sym}' is in SYMBOLS_IGNORE — skipping.")
            return
        symbols = [sym]
    else:
        db_syms  = _symbols_from_db()
        cfg_syms = [s for s in SYMBOLS if s not in SYMBOLS_IGNORE]
        # merge, preserve config order first, then any DB-only extras
        seen     = set(cfg_syms)
        symbols  = list(cfg_syms) + [s for s in db_syms
                                      if s not in seen
                                      and s not in SYMBOLS_IGNORE]
        if SYMBOLS_IGNORE:
            blocked = [s for s in (list(SYMBOLS) + db_syms) if s in SYMBOLS_IGNORE]
            if blocked:
                log.info(f"Symbols blocked by SYMBOLS_IGNORE: {sorted(set(blocked))}")
        if db_syms:
            extras = [s for s in db_syms
                      if s not in set(SYMBOLS) and s not in SYMBOLS_IGNORE]
            if extras:
                log.info(f"Symbols from DB not in config (kept): {extras}")
    use_csv    = args.csv
    plot_field = args.plot_data
    # sources filter — None means run all
    run_sources = set(args.sources) if args.sources else None
    def _should_run(source: str) -> bool:
        return run_sources is None or source in run_sources

    log.info("=" * 60)
    log.info("Stock collector starting")
    log.info(f"Symbols: {symbols}")
    if args.sources:
        log.info(f"Sources filter: {args.sources}")

    state = load_state()
    log.info(f"Daily call counts so far: {state['calls']}")

    # ── historical mode ──────────────────────────────────────

    if args.historical:
        try:
            active_db = run_historical(symbols, args.historical, state)
        except ValueError as e:
            log.error(str(e))
            return
        save_state(state)
        log.info(f"Updated call counts: {state['calls']}")
        if args.plot_gnuplot:
            plot_gnuplot(symbols, use_csv=False, field=plot_field, db_path=active_db)
        if args.plot_matplotlib:
            plot_matplotlib(symbols, use_csv=False, field=plot_field, db_path=active_db)
        flush_failures()
        log.info("Done.\n")
        return

    # ── live collection mode ─────────────────────────────────

    log.info(f"Backend: {'CSV → ' + str(CSV_PATH) if use_csv else 'SQLite → ' + str(DB_PATH)}")

    # Each fetcher is a (name, label, callable) tuple.
    # Fetchers run in parallel via ThreadPoolExecutor — safe because:
    #   - HTTP calls are I/O-bound, GIL is released during socket waits
    #   - SQLite skip-function reads use WAL mode (readers never block)
    #   - state["calls"] keys are per-source, no two fetchers share one
    #   - rows are collected per-fetcher and merged after all complete
    fetchers = [
        ("yfinance",     "── yfinance ─────────────────────────────────────────",
         lambda: fetch_yfinance(symbols)),
        ("alphavantage", "── Alpha Vantage ────────────────────────────────────",
         lambda: fetch_alphavantage(symbols, state)),
        ("finnhub",      "── Finnhub ───────────────────────────────────────────────",
         lambda: fetch_finnhub(symbols, state)),
        ("polygon",      "── Massive (formerly Polygon.io) ────────────────────────",
         lambda: fetch_polygon(symbols, state)),
        ("fmp",          "── Financial Modeling Prep (FMP) ────────────────",
         lambda: fetch_fmp(symbols, state)),
        ("twelvedata",   "── Twelve Data ──────────────────────────────────────────",
         lambda: fetch_twelvedata(symbols, state)),
        ("marketstack",  "── Marketstack ──────────────────────────────────────────",
         lambda: fetch_marketstack(symbols, state)),
    ]

    active = [(name, label, fn)
              for name, label, fn in fetchers
              if _should_run(name)]

    all_rows: list[dict] = []

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _run_fetcher(name: str, label: str, fn) -> tuple[str, list[dict]]:
        log.info(label)
        return name, fn()

    with ThreadPoolExecutor(max_workers=len(active),
                            thread_name_prefix="fetcher") as pool:
        futures = {
            pool.submit(_run_fetcher, name, label, fn): name
            for name, label, fn in active
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                _, rows = future.result()
                all_rows += rows
            except Exception as e:
                log.error(f"[{name}] fetcher raised an exception: {e}")

    # ── persist ──────────────────────────────────────

    save_state(state)

    if use_csv:
        seen = load_existing_keys()
        added = csv_append_rows(all_rows, seen)
        log.info(f"Fetched {len(all_rows)} rows | {added} new rows appended to {CSV_PATH.name}")
    else:
        added = db_insert_rows(all_rows)
        log.info(f"Fetched {len(all_rows)} rows | {added} new rows inserted into {DB_PATH.name}")

    log.info(f"Updated call counts: {state['calls']}")

    # ── plot ───────────────────────────────────────────

    if args.plot_gnuplot:
        plot_gnuplot(symbols, use_csv, plot_field)

    if args.plot_matplotlib:
        plot_matplotlib(symbols, use_csv, plot_field)

    log.info("Done.\n")

    # ── cleanup ────────────────────────────────────────
    flush_failures()          # write accumulated failures to stock_failures.csv


if __name__ == "__main__":
    main()
