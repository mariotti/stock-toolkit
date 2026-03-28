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
"""

import argparse
import json
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

