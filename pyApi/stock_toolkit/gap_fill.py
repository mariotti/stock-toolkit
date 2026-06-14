"""
stock-gap-fill
==============
Fills detected gaps in the daily-bar coverage by fetching only the
missing date ranges from yfinance. Complements `stock-collect`
(forward, daily) and `stock-bootstrap` (one-shot full history): this
is the targeted "I missed a week, please backfill just that week" tool.

Run:
    stock-gap-fill                       # fill every detected gap
    stock-gap-fill -s AAPL ENEL.MI       # restrict to specific symbols
    stock-gap-fill --dry-run             # show what would be fetched
    stock-gap-fill --gap-threshold 3     # be more aggressive about long-weekend gaps

Holiday-style "gaps" of 1–3 days flanking known closures get skipped
automatically — yfinance returns no rows for genuine market holidays,
and the UNIQUE(symbol, source, timestamp) constraint dedups overlaps
with existing data.
"""

import argparse
import datetime
import sys

from stock_toolkit.collector.db import db_insert_rows, make_row
from stock_toolkit.common import CONFIG_PATH
from stock_toolkit.inventory import detect_gaps, discover_dbs


def _fetch_range(yf, symbol: str, start: datetime.date,
                 end: datetime.date) -> list:
    """yfinance daily bars for [start, end] (inclusive). Empty list on error."""
    try:
        # yfinance treats end= as exclusive; bump by one day to include `end`
        hist = yf.Ticker(symbol).history(
            start=str(start),
            end=str(end + datetime.timedelta(days=1)),
            interval="1d",
        )
    except Exception as e:
        print(f"  [{symbol}] yfinance error: {e}")
        return []

    rows = []
    for ts, bar in hist.iterrows():
        rows.append(make_row(
            symbol, "yfinance", ts.date(), "1d",
            bar.get("Open"), bar.get("High"), bar.get("Low"),
            bar.get("Close"), bar.get("Volume"),
        ))
    return rows


def fill_gaps(symbol_filter: list = None, dry_run: bool = False,
              gap_threshold_days: int = 5, dbs: list = None) -> dict:
    """Find and fill gaps. Returns {(db_path, symbol): n_rows_inserted}.

    `dbs` defaults to discover_dbs() — pass explicitly for testing against
    a fixture DB without touching $STOCK_DIR / global module state.
    """
    try:
        import yfinance as yf
    except ImportError:
        print("[gap-fill] yfinance not installed — pip install yfinance")
        return {}

    if dbs is None:
        dbs = discover_dbs()

    gaps = detect_gaps(
        dbs,
        symbol_filter=symbol_filter,
        gap_threshold_days=gap_threshold_days,
    )
    if not gaps:
        print("[gap-fill] No gaps detected — nothing to do.")
        return {}

    summary: dict = {}
    n_pairs = len(gaps)
    print(f"[gap-fill] {n_pairs} (db, symbol) pair(s) with gaps "
          + ("— dry run, no writes" if dry_run else "— fetching from yfinance"))
    print()

    for (db_path, symbol), ranges in sorted(gaps.items(),
                                            key=lambda x: (x[0][0].name, x[0][1])):
        total_days = sum((e - s).days + 1 for s, e in ranges)
        print(f"  {symbol:<10} → {db_path.name}: "
              f"{len(ranges)} gap(s), {total_days} business day(s)")

        if dry_run:
            for s, e in ranges[:3]:
                print(f"      {s} → {e}")
            if len(ranges) > 3:
                print(f"      … {len(ranges) - 3} more")
            continue

        all_rows = []
        for start, end in ranges:
            rows = _fetch_range(yf, symbol, start, end)
            all_rows += rows

        if not all_rows:
            print("      yfinance returned 0 bars (probably real holidays)")
            continue

        inserted = db_insert_rows(all_rows, db_path=db_path)
        summary[(db_path, symbol)] = inserted
        print(f"      fetched {len(all_rows)} bar(s), inserted {inserted} new")

    if not dry_run and summary:
        total = sum(summary.values())
        print()
        print(f"[gap-fill] Done. {total} bar(s) inserted across "
              f"{len(summary)} (db, symbol) pair(s).")
    return summary


def main():
    parser = argparse.ArgumentParser(
        prog="stock-gap-fill",
        description=(
            "Fill detected gaps in daily-bar coverage by fetching only the "
            "missing date ranges from yfinance. Holiday-style short gaps "
            "are skipped automatically."
        ),
    )
    parser.add_argument(
        "-s", "--symbols", nargs="+", metavar="TICKER",
        help="Restrict to these symbols (default: every symbol with gaps)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be fetched without writing to the DB",
    )
    parser.add_argument(
        "--gap-threshold", type=int, default=5, metavar="DAYS",
        help="Calendar-day gap above which a stretch is flagged as missing "
             "(default: 5; lower = more aggressive about holiday-edge gaps)",
    )
    args = parser.parse_args()

    if not CONFIG_PATH.exists():
        print(f"[gap-fill] {CONFIG_PATH} not found.")
        print("  Run 'stock-setup' first.")
        sys.exit(1)

    fill_gaps(
        symbol_filter=args.symbols,
        dry_run=args.dry_run,
        gap_threshold_days=args.gap_threshold,
    )


if __name__ == "__main__":
    main()
