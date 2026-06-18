"""Failure tracking: suppress broken (symbol, source) pairs after N failures."""

import csv
import sqlite3
from datetime import date

from . import config as cfg
from .config import log

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
    con = sqlite3.connect(cfg.FAILURES_DB_PATH, timeout=10)
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
    When hits reach cfg.FAILURE_THRESHOLD, logs a warning once.
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
        if hits == cfg.FAILURE_THRESHOLD:
            log.warning(
                f"[failures] {sym}/{source}: {cfg.FAILURE_THRESHOLD} failures "
                f"({reason}) — will be skipped automatically. "
                f"Reset: delete or UPDATE the row in {cfg.FAILURES_DB_PATH.name}"
            )
    finally:
        con.close()


def is_suppressed(symbol: str, source: str) -> bool:
    """
    Return True if (symbol, source) has reached the failure threshold.
    Queries the failures DB directly — no in-memory cache needed.
    """
    if not cfg.FAILURES_DB_PATH.exists():
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
        return row is not None and row[0] >= cfg.FAILURE_THRESHOLD
    except Exception:
        return False


def flush_failures() -> None:
    """
    Export the failures DB to a human-readable CSV report.
    Called once at end of run. The CSV is for inspection only —
    the DB is the authoritative source.
    """
    if not cfg.FAILURES_DB_PATH.exists():
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
        with open(cfg.FAILURES_REPORT_PATH, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["symbol", "source", "reason", "hits",
                             "first_seen", "last_seen"])
            writer.writerows(rows)
        suppressed = sum(1 for r in rows if r[3] >= cfg.FAILURE_THRESHOLD)
        log.info(f"[failures] report written to {cfg.FAILURES_REPORT_PATH.name} "
                 f"({len(rows)} entries, {suppressed} suppressed)")
    except (sqlite3.OperationalError, OSError) as e:
        # OSError covers CSV write failures (disk full / permission);
        # sqlite3.OperationalError covers a corrupt failures DB. Anything
        # else (e.g. import-time KeyError) should propagate so the cron
        # job logs it properly instead of silently swallowing.
        log.warning(f"[failures] could not write report: {e}")

