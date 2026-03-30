"""
stock_inventory.py
==================
Lists all stock data available on disk: symbols, date ranges,
granularity/intervals, row counts, and which database each comes from.

Usage:
    python3 stock_inventory.py                  # full inventory
    python3 stock_inventory.py -s AAPL          # filter to one symbol
    python3 stock_inventory.py --db data/       # custom data folder
    python3 stock_inventory.py --json           # machine-readable output
    python3 stock_inventory.py --remove TSLA    # delete symbol from all DBs
    python3 stock_inventory.py --check          # data consistency report
"""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

# ── paths (mirror stock_collector.py) ────────────────────────────────────────

BASE_DIR = Path(__file__).parent
LIVE_DB  = BASE_DIR / "stock_data.db"
HIST_DIR = BASE_DIR / "data"

# ── helpers ───────────────────────────────────────────────────────────────────

def discover_dbs(extra_dir: Path | None = None) -> list[Path]:
    dbs = []
    if LIVE_DB.exists():
        dbs.append(LIVE_DB)
    hist = extra_dir or HIST_DIR
    if hist.exists():
        dbs += sorted(hist.glob("*.db"))
    return dbs


def query_db(db: Path, symbol_filter: list[str] | None) -> list[dict]:
    """
    Return one record per (symbol, interval, source) from this database.
    """
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        con = sqlite3.connect(db)   # fallback for older SQLite

    where = ""
    params: list = []
    if symbol_filter:
        placeholders = ",".join("?" * len(symbol_filter))
        where  = f" WHERE symbol IN ({placeholders})"
        params = [s.upper() for s in symbol_filter]

    try:
        rows = con.execute(
            f"""
            SELECT
                symbol,
                source,
                interval,
                COUNT(*)                      AS n_rows,
                MIN(data_date)                AS date_from,
                MAX(data_date)                AS date_to
            FROM prices{where}
            GROUP BY symbol, source, interval
            ORDER BY symbol, interval, source
            """,
            params,
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []   # table doesn't exist yet
    finally:
        con.close()

    return [
        {
            "db":        db.name,
            "symbol":    r[0],
            "source":    r[1],
            "interval":  r[2],
            "n_rows":    r[3],
            "date_from": r[4][:10] if r[4] else "—",
            "date_to":   r[5][:10] if r[5] else "—",
        }
        for r in rows
    ]


def col_widths(headers: list[str], rows: list[list]) -> list[int]:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, v in enumerate(row):
            widths[i] = max(widths[i], len(str(v)))
    return widths


def print_table(headers: list[str], rows: list[list]):
    widths = col_widths(headers, rows)
    fmt    = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print("  ".join("─" * w for w in widths))
    for row in rows:
        print(fmt.format(*[str(v) for v in row]))


def date_span(date_from: str, date_to: str) -> str:
    """Human-readable span label, e.g. '3 years 2 months'."""
    if date_from == "—" or date_to == "—":
        return "—"
    from datetime import date
    try:
        d0 = date.fromisoformat(date_from)
        d1 = date.fromisoformat(date_to)
        days = (d1 - d0).days
        if days < 7:
            return f"{days}d"
        if days < 60:
            return f"{days // 7}w"
        months = days // 30
        if months < 24:
            return f"{months}mo"
        return f"{months // 12}yr {months % 12}mo"
    except ValueError:
        return "—"


# ── remove ────────────────────────────────────────────────────────────────────

def cmd_remove(symbol: str, dbs: list[Path]) -> None:
    """
    Delete all rows for `symbol` from every database.

    Confirmation is controlled by the STOCK_INV_REMOVE environment variable:
      STOCK_INV_REMOVE=allow   — no prompt, delete immediately
      (not set)                — interactive prompt required
    """
    sym = symbol.upper()

    # ── count rows to be deleted across all DBs ───────────────────────────────
    preview: list[tuple[Path, int]] = []
    for db in dbs:
        try:
            con = sqlite3.connect(db)
            n = con.execute(
                "SELECT COUNT(*) FROM prices WHERE symbol=?", (sym,)
            ).fetchone()[0]
            con.close()
            if n > 0:
                preview.append((db, n))
        except sqlite3.OperationalError:
            pass

    if not preview:
        print(f"Symbol '{sym}' not found in any database.")
        return

    print(f"\nAbout to delete all data for '{sym}':")
    total = 0
    for db, n in preview:
        print(f"  {db.name:<40}  {n:,} rows")
        total += n
    print(f"  {'TOTAL':<40}  {total:,} rows\n")

    # ── confirmation ──────────────────────────────────────────────────────────
    allow_env = os.environ.get("STOCK_INV_REMOVE", "").strip().lower()
    if allow_env == "allow":
        print("  STOCK_INV_REMOVE=allow — skipping confirmation.")
    else:
        print("  Set STOCK_INV_REMOVE=allow to skip this prompt.")
        ans = input(f"  Type the symbol '{sym}' to confirm deletion: ").strip().upper()
        if ans != sym:
            print("  Aborted — symbol did not match.")
            return

    # ── delete ────────────────────────────────────────────────────────────────
    for db, _ in preview:
        con = sqlite3.connect(db)
        deleted = con.execute(
            "DELETE FROM prices WHERE symbol=?", (sym,)
        ).rowcount
        con.commit()
        # reclaim space immediately — these can be large deletions
        con.execute("VACUUM")
        con.close()
        print(f"  ✓  {db.name}  —  {deleted:,} rows deleted + VACUUM done")

    print(f"\nDone. '{sym}' removed from {len(preview)} database(s).")


# ── consistency check ─────────────────────────────────────────────────────────

def cmd_check(dbs: list[Path], symbol_filter: list[str] | None) -> None:
    """
    Data consistency report for daily (1d) bars.

    Checks:
      - Missing trading days between first and last date for each symbol
      - Symbols present in fewer sources than the majority
      - Suspiciously thin coverage (fewer bars than expected)

    Trading-day calendar is derived from the union of all dates present in the
    DB — no external market calendar needed. Days where every symbol has a gap
    are treated as holidays/non-trading days and excluded from gap counts.
    """
    try:
        import pandas as pd
    except ImportError:
        print("pandas required for --check  (pip install pandas)")
        sys.exit(1)

    issues: list[dict] = []
    all_clean = True

    for db in dbs:
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        except sqlite3.OperationalError:
            con = sqlite3.connect(db)

        # ── load all 1d rows (date + symbol) ──────────────────────────────────
        where = ""
        params: list = []
        if symbol_filter:
            placeholders = ",".join("?" * len(symbol_filter))
            where  = f" AND symbol IN ({placeholders})"
            params = [s.upper() for s in symbol_filter]

        try:
            rows = con.execute(
                f"SELECT symbol, data_date FROM prices "
                f"WHERE interval='1d'{where} ORDER BY symbol, data_date",
                params,
            ).fetchall()
        except sqlite3.OperationalError:
            con.close()
            continue
        con.close()

        if not rows:
            continue

        # ── build per-symbol date sets ─────────────────────────────────────────
        from collections import defaultdict
        sym_dates: dict[str, set] = defaultdict(set)
        for sym, d in rows:
            sym_dates[sym].add(d[:10])

        # Build a trading-day calendar: all dates present for ≥50% of symbols
        # (days where most symbols have data = likely a real trading day)
        all_dates: dict[str, int] = defaultdict(int)
        for dates in sym_dates.values():
            for d in dates:
                all_dates[d] += 1
        n_syms = len(sym_dates)
        trading_days = {
            d for d, count in all_dates.items()
            if count >= max(1, n_syms * 0.5)
        }

        # ── check each symbol ──────────────────────────────────────────────────
        sym_issues: list[str] = []
        for sym, dates in sorted(sym_dates.items()):
            d_min = min(dates)
            d_max = max(dates)

            # Expected trading days within this symbol's own range
            expected = {
                d for d in trading_days
                if d_min <= d <= d_max
            }
            missing = sorted(expected - dates)

            # Group consecutive missing days into ranges for compact display
            if missing:
                gaps = _group_gaps(missing)
                n_missing = len(missing)
                all_clean = False
                sym_issues.append({
                    "db":       db.name,
                    "symbol":   sym,
                    "issue":    "missing days",
                    "detail":   f"{n_missing} missing trading day(s): "
                                + ", ".join(gaps[:5])
                                + (" …" if len(gaps) > 5 else ""),
                    "n_bars":   len(dates),
                    "date_from": d_min,
                    "date_to":   d_max,
                })

            # Thin coverage warning: < 60% of expected bars present
            n_expected = len(expected)
            if n_expected > 10 and len(dates) < n_expected * 0.6:
                all_clean = False
                sym_issues.append({
                    "db":       db.name,
                    "symbol":   sym,
                    "issue":    "thin coverage",
                    "detail":   f"{len(dates)} bars vs {n_expected} expected "
                                f"({100*len(dates)//n_expected}%)",
                    "n_bars":   len(dates),
                    "date_from": d_min,
                    "date_to":   d_max,
                })

        issues += sym_issues

    # ── report ────────────────────────────────────────────────────────────────
    if all_clean and not issues:
        syms_checked = len({r[0] for db in dbs for r in _fetch_symbols(db)})
        print(f"✓  No consistency issues found across {len(dbs)} database(s).")
        return

    # Group by issue type for summary
    by_type: dict[str, list] = defaultdict(list)
    for iss in issues:
        by_type[iss["issue"]].append(iss)

    for issue_type, items in sorted(by_type.items()):
        print(f"\n{'─'*60}")
        print(f"  {issue_type.upper()}  ({len(items)} symbol(s))")
        print(f"{'─'*60}")
        headers = ["Symbol", "DB", "From", "To", "Bars", "Detail"]
        rows_out = [
            [i["symbol"], i["db"], i["date_from"], i["date_to"],
             str(i["n_bars"]), i["detail"]]
            for i in items
        ]
        print_table(headers, rows_out)

    total_issues = len(issues)
    print(f"\n{total_issues} issue(s) found across {len(dbs)} database(s).")
    print("To fill gaps: run `stock_collector.py` to collect missing data.")


def _group_gaps(dates: list[str]) -> list[str]:
    """Compress consecutive dates into ranges: ['2024-01-03..05', '2024-01-08']."""
    if not dates:
        return []
    from datetime import date, timedelta
    result = []
    start = end = date.fromisoformat(dates[0])
    for ds in dates[1:]:
        d = date.fromisoformat(ds)
        if d == end + timedelta(days=1):
            end = d
        else:
            result.append(str(start) if start == end else f"{start}..{end}")
            start = end = d
    result.append(str(start) if start == end else f"{start}..{end}")
    return result


def _fetch_symbols(db: Path) -> list[tuple]:
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT DISTINCT symbol FROM prices WHERE interval='1d'"
        ).fetchall()
        con.close()
        return rows
    except Exception:
        return []


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="List, manage, and check stock data on disk",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python3 stock_inventory.py                     # full inventory
  python3 stock_inventory.py -s AAPL MSFT        # filter symbols
  python3 stock_inventory.py --summary           # one row per symbol
  python3 stock_inventory.py --json              # machine-readable
  python3 stock_inventory.py --remove TSLA       # delete symbol (prompts)
  STOCK_INV_REMOVE=allow inventory --remove TSLA # delete without prompt
  python3 stock_inventory.py --check             # consistency report
  python3 stock_inventory.py --check -s AAPL     # check one symbol
        """
    )
    parser.add_argument("-s", "--symbols", nargs="+", metavar="TICKER",
                        help="Filter to these symbols only")
    parser.add_argument("--db", metavar="DIR_OR_FILE",
                        help="Extra directory (or single .db file) to scan")
    parser.add_argument("--json", action="store_true",
                        help="Output raw JSON instead of a table")
    parser.add_argument("--summary", action="store_true",
                        help="One line per symbol (collapsed across sources)")
    parser.add_argument("--remove", metavar="TICKER",
                        help="Remove all data for this symbol from every database. "
                             "Requires confirmation unless STOCK_INV_REMOVE=allow is set.")
    parser.add_argument("--check", action="store_true",
                        help="Check data consistency: missing trading days, thin coverage")
    args = parser.parse_args()

    # ── discover databases ────────────────────────────────────────────────────
    extra = Path(args.db) if args.db else None
    if extra and extra.is_file():
        dbs = [extra]
    else:
        dbs = discover_dbs(extra_dir=extra)

    if not dbs:
        print("No database files found.")
        print(f"  Expected:  {LIVE_DB}")
        print(f"  And/or:    {HIST_DIR}/*.db")
        print("Run stock_collector.py first to collect some data.")
        sys.exit(1)

    # ── dispatch ──────────────────────────────────────────────────────────────
    if args.remove:
        cmd_remove(args.remove, dbs)
        return

    if args.check:
        print(f"Checking consistency across {len(dbs)} database(s)…\n")
        cmd_check(dbs, args.symbols)
        return

    # ── inventory display ─────────────────────────────────────────────────────
    print(f"Scanning {len(dbs)} database(s):\n")
    for db in dbs:
        size_kb = db.stat().st_size / 1024
        print(f"  {db.name:<40}  {size_kb:>8.1f} KB")
    print()

    all_records: list[dict] = []
    for db in dbs:
        all_records += query_db(db, args.symbols)

    if not all_records:
        msg = "No data found"
        if args.symbols:
            msg += f" for symbol(s): {', '.join(args.symbols)}"
        print(msg + ".")
        sys.exit(0)

    if args.json:
        print(json.dumps(all_records, indent=2))
        return

    # ── summary mode ──────────────────────────────────────────────────────────
    if args.summary:
        collapsed: dict[tuple, dict] = {}
        for r in all_records:
            key = (r["symbol"], r["interval"])
            if key not in collapsed:
                collapsed[key] = {
                    "symbol":    r["symbol"],
                    "interval":  r["interval"],
                    "n_rows":    0,
                    "date_from": r["date_from"],
                    "date_to":   r["date_to"],
                    "sources":   set(),
                    "dbs":       set(),
                }
            c = collapsed[key]
            c["n_rows"]    += r["n_rows"]
            c["sources"].add(r["source"])
            c["dbs"].add(r["db"])
            if r["date_from"] < c["date_from"]:
                c["date_from"] = r["date_from"]
            if r["date_to"]   > c["date_to"]:
                c["date_to"]   = r["date_to"]

        headers = ["Symbol", "Interval", "From", "To", "Span", "Rows", "Sources", "DBs"]
        rows = []
        for (sym, intv), c in sorted(collapsed.items()):
            rows.append([
                sym, intv,
                c["date_from"], c["date_to"],
                date_span(c["date_from"], c["date_to"]),
                f"{c['n_rows']:,}",
                ", ".join(sorted(c["sources"])),
                ", ".join(sorted(c["dbs"])),
            ])
        print_table(headers, rows)

    # ── detailed mode ──────────────────────────────────────────────────────────
    else:
        headers = ["Symbol", "Interval", "Source", "From", "To", "Span", "Rows", "Database"]
        rows = [
            [
                r["symbol"], r["interval"], r["source"],
                r["date_from"], r["date_to"],
                date_span(r["date_from"], r["date_to"]),
                f"{r['n_rows']:,}",
                r["db"],
            ]
            for r in all_records
        ]
        print_table(headers, rows)

    # ── footer ────────────────────────────────────────────────────────────────
    total_rows  = sum(r["n_rows"] for r in all_records)
    n_symbols   = len({r["symbol"] for r in all_records})
    n_intervals = len({r["interval"] for r in all_records})
    n_sources   = len({r["source"] for r in all_records})

    print(f"\n{total_rows:,} total rows  ·  "
          f"{n_symbols} symbol(s)  ·  "
          f"{n_intervals} interval type(s)  ·  "
          f"{n_sources} source(s)  ·  "
          f"{len(dbs)} database(s)")


if __name__ == "__main__":
    main()


# ── paths (mirror stock_collector.py) ────────────────────────────────────────

BASE_DIR = Path(__file__).parent
LIVE_DB  = BASE_DIR / "stock_data.db"
HIST_DIR = BASE_DIR / "data"

# ── helpers ───────────────────────────────────────────────────────────────────

def discover_dbs(extra_dir: Path | None = None) -> list[Path]:
    dbs = []
    if LIVE_DB.exists():
        dbs.append(LIVE_DB)
    hist = extra_dir or HIST_DIR
    if hist.exists():
        dbs += sorted(hist.glob("*.db"))
    return dbs


def query_db(db: Path, symbol_filter: list[str] | None) -> list[dict]:
    """
    Return one record per (symbol, interval, source) from this database.
    """
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        con = sqlite3.connect(db)   # fallback for older SQLite

    where = ""
    params: list = []
    if symbol_filter:
        placeholders = ",".join("?" * len(symbol_filter))
        where  = f" WHERE symbol IN ({placeholders})"
        params = [s.upper() for s in symbol_filter]

    try:
        rows = con.execute(
            f"""
            SELECT
                symbol,
                source,
                interval,
                COUNT(*)                      AS n_rows,
                MIN(data_date)                AS date_from,
                MAX(data_date)                AS date_to
            FROM prices{where}
            GROUP BY symbol, source, interval
            ORDER BY symbol, interval, source
            """,
            params,
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []   # table doesn't exist yet
    finally:
        con.close()

    return [
        {
            "db":        db.name,
            "symbol":    r[0],
            "source":    r[1],
            "interval":  r[2],
            "n_rows":    r[3],
            "date_from": r[4][:10] if r[4] else "—",
            "date_to":   r[5][:10] if r[5] else "—",
        }
        for r in rows
    ]


def col_widths(headers: list[str], rows: list[list]) -> list[int]:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, v in enumerate(row):
            widths[i] = max(widths[i], len(str(v)))
    return widths


def print_table(headers: list[str], rows: list[list]):
    widths = col_widths(headers, rows)
    fmt    = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print("  ".join("─" * w for w in widths))
    for row in rows:
        print(fmt.format(*[str(v) for v in row]))


def date_span(date_from: str, date_to: str) -> str:
    """Human-readable span label, e.g. '3 years 2 months'."""
    if date_from == "—" or date_to == "—":
        return "—"
    from datetime import date
    try:
        d0 = date.fromisoformat(date_from)
        d1 = date.fromisoformat(date_to)
        days = (d1 - d0).days
        if days < 7:
            return f"{days}d"
        if days < 60:
            return f"{days // 7}w"
        months = days // 30
        if months < 24:
            return f"{months}mo"
        return f"{months // 12}yr {months % 12}mo"
    except ValueError:
        return "—"


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="List all stock data available on disk",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python3 stock_inventory.py
  python3 stock_inventory.py -s AAPL MSFT
  python3 stock_inventory.py --db ./data
  python3 stock_inventory.py --json
  python3 stock_inventory.py --summary
        """
    )
    parser.add_argument("-s", "--symbols", nargs="+", metavar="TICKER",
                        help="Filter to these symbols only")
    parser.add_argument("--db", metavar="DIR_OR_FILE",
                        help="Extra directory (or single .db file) to scan")
    parser.add_argument("--json", action="store_true",
                        help="Output raw JSON instead of a table")
    parser.add_argument("--summary", action="store_true",
                        help="One line per symbol (collapsed across sources)")
    args = parser.parse_args()

    # ── discover databases ────────────────────────────────────────────────────
    extra = Path(args.db) if args.db else None
    if extra and extra.is_file():
        dbs = [extra]
    else:
        dbs = discover_dbs(extra_dir=extra)

    if not dbs:
        print("No database files found.")
        print(f"  Expected:  {LIVE_DB}")
        print(f"  And/or:    {HIST_DIR}/*.db")
        print("Run stock_collector.py first to collect some data.")
        sys.exit(1)

    print(f"Scanning {len(dbs)} database(s):\n")
    for db in dbs:
        size_kb = db.stat().st_size / 1024
        print(f"  {db.name:<40}  {size_kb:>8.1f} KB")
    print()

    # ── query ─────────────────────────────────────────────────────────────────
    all_records: list[dict] = []
    for db in dbs:
        all_records += query_db(db, args.symbols)

    if not all_records:
        msg = "No data found"
        if args.symbols:
            msg += f" for symbol(s): {', '.join(args.symbols)}"
        print(msg + ".")
        sys.exit(0)

    # ── JSON output ───────────────────────────────────────────────────────────
    if args.json:
        print(json.dumps(all_records, indent=2))
        return

    # ── summary mode: one row per (symbol, interval) ──────────────────────────
    if args.summary:
        # collapse across sources and DBs
        collapsed: dict[tuple, dict] = {}
        for r in all_records:
            key = (r["symbol"], r["interval"])
            if key not in collapsed:
                collapsed[key] = {
                    "symbol":    r["symbol"],
                    "interval":  r["interval"],
                    "n_rows":    0,
                    "date_from": r["date_from"],
                    "date_to":   r["date_to"],
                    "sources":   set(),
                    "dbs":       set(),
                }
            c = collapsed[key]
            c["n_rows"]    += r["n_rows"]
            c["sources"].add(r["source"])
            c["dbs"].add(r["db"])
            # expand date range
            if r["date_from"] < c["date_from"]:
                c["date_from"] = r["date_from"]
            if r["date_to"]   > c["date_to"]:
                c["date_to"]   = r["date_to"]

        headers = ["Symbol", "Interval", "From", "To", "Span", "Rows", "Sources", "DBs"]
        rows = []
        for (sym, intv), c in sorted(collapsed.items()):
            rows.append([
                sym,
                intv,
                c["date_from"],
                c["date_to"],
                date_span(c["date_from"], c["date_to"]),
                f"{c['n_rows']:,}",
                ", ".join(sorted(c["sources"])),
                ", ".join(sorted(c["dbs"])),
            ])
        print_table(headers, rows)

    # ── detailed mode: one row per (symbol, interval, source, db) ─────────────
    else:
        headers = ["Symbol", "Interval", "Source", "From", "To", "Span", "Rows", "Database"]
        rows = [
            [
                r["symbol"],
                r["interval"],
                r["source"],
                r["date_from"],
                r["date_to"],
                date_span(r["date_from"], r["date_to"]),
                f"{r['n_rows']:,}",
                r["db"],
            ]
            for r in all_records
        ]
        print_table(headers, rows)

    # ── footer totals ─────────────────────────────────────────────────────────
    total_rows  = sum(r["n_rows"] for r in all_records)
    n_symbols   = len({r["symbol"] for r in all_records})
    n_intervals = len({r["interval"] for r in all_records})
    n_sources   = len({r["source"] for r in all_records})

    print(f"\n{total_rows:,} total rows  ·  "
          f"{n_symbols} symbol(s)  ·  "
          f"{n_intervals} interval type(s)  ·  "
          f"{n_sources} source(s)  ·  "
          f"{len(dbs)} database(s)")


if __name__ == "__main__":
    main()
