"""SQLite/CSV storage, dedup, timestamp normalisation, freshness checks."""

import csv
import json
import hashlib
import sqlite3
from datetime import datetime, date, timezone
from pathlib import Path


from . import config as cfg
from .config import log

def _symbols_from_db() -> list[str]:
    """
    Return symbols with at least 2 daily bars across the live DB AND the
    historical/bootstrap DBs (``cfg.HIST_DIR/*.db``). Scanning the
    historicals too means a ticker you bootstrapped but never collected
    into the live DB (e.g. an EU name only in ``stock_data_all.db``) still
    stays in the collection loop instead of ageing out forever.

    The 2-bar threshold filters out symbols tried once that returned nothing
    useful (e.g. bare 'ENI' instead of 'ENI.MI'). Empty list if no DB yet.
    """
    import sqlite3 as _sq

    dbs = []
    if cfg.DB_PATH.exists():
        dbs.append(cfg.DB_PATH)
    if cfg.HIST_DIR.exists():
        dbs += sorted(cfg.HIST_DIR.glob("*.db"))

    found: set[str] = set()
    for db in dbs:
        try:
            con = _sq.connect(f"file:{db}?mode=ro", uri=True)
        except _sq.OperationalError:
            con = _sq.connect(db)
        try:
            rows = con.execute(
                """SELECT symbol FROM prices WHERE interval='1d'
                   GROUP BY symbol HAVING COUNT(*) >= 2"""
            ).fetchall()
            found.update(r[0] for r in rows)
        except _sq.OperationalError:
            pass
        finally:
            con.close()
    return sorted(found)


def _symbols_from_portfolios() -> list[str]:
    """
    Symbols traded in any Game portfolio, so an open position never ages out
    of the collection loop and its valuation stays current. Read-only; empty
    list if there's no portfolio DB yet.
    """
    import sqlite3 as _sq

    db = getattr(cfg, "PORTFOLIO_DB", None)
    if db is None or not db.exists():
        return []
    try:
        con = _sq.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = con.execute("SELECT DISTINCT symbol FROM trades").fetchall()
        finally:
            con.close()
        return sorted({r[0] for r in rows if r[0]})
    except Exception:
        return []

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

def _sort_by_staleness(symbols: list[str]) -> list[str]:
    """
    Sort symbols so the ones with the oldest data come first.
    Symbols not yet in the DB (no timestamp) are treated as most stale.
    This ensures budget-limited sources (Alpha Vantage, FMP) serve the
    least-recently-updated symbols first rather than always favouring
    whichever symbols happen to be first in config.env.
    """
    if not cfg.DB_PATH.exists() or not symbols:
        return symbols
    try:
        con = sqlite3.connect(cfg.DB_PATH)
        try:
            rows = con.execute(
                f"""SELECT symbol, MAX(timestamp) as last_ts
                    FROM prices
                    WHERE interval='1d'
                    AND symbol IN ({','.join('?' * len(symbols))})
                    GROUP BY symbol""",
                symbols
            ).fetchall()
        finally:
            con.close()
        last_seen = {sym: ts for sym, ts in rows}
        # symbols missing from DB get epoch (always first)
        return sorted(symbols, key=lambda s: last_seen.get(s, "1970-01-01"))
    except Exception:
        return symbols


def db_connect(db_path: "Path | None" = None) -> sqlite3.Connection:
    """Open (and if needed initialise) the SQLite database."""
    con = sqlite3.connect(db_path or cfg.DB_PATH)
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
    if not cfg.CSV_PATH.exists():
        return set()
    seen = set()
    with open(cfg.CSV_PATH, newline="") as f:
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

    write_header = not cfg.CSV_PATH.exists()
    with open(cfg.CSV_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(new_rows)
    return len(new_rows)

def make_row(symbol, source, ts, interval, o, h, lo, c, v,
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
        "low":         round(float(lo), 4) if lo not in (None, "") else "",
        "close":       round(float(c), 4) if c not in (None, "") else "",
        "volume":      int(float(v)) if v not in (None, "") else "",
        "vwap":        round(float(vwap), 4) if vwap not in (None, "") else "",
        "change_pct":  round(float(change_pct), 4) if change_pct not in (None, "") else "",
        "extra":       json.dumps(extra) if extra else "",
    }

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
    if not cfg.DB_PATH.exists():
        return False
    today_str = str(date.today())
    try:
        con = sqlite3.connect(cfg.DB_PATH)
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
    if not cfg.DB_PATH.exists():
        return False
    try:
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        con = sqlite3.connect(cfg.DB_PATH)
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
    if not cfg.DB_PATH.exists():
        return False
    try:
        from datetime import datetime, timezone
        now        = datetime.now(timezone.utc)
        hour_start = now.strftime("%Y-%m-%dT%H:")   # e.g. "2026-03-31T14:"
        con = sqlite3.connect(cfg.DB_PATH)
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


