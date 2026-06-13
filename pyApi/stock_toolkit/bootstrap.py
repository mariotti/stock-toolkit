"""
stock-bootstrap
===============
One-command historical backfill via yfinance (no API key, no rate
budget, global coverage). Shorthand for:

    stock-collect --sources yfinance --historical <range>

so a new user can seed years of data with a single command instead of
discovering the right flags.

Writes to data/stock_data_<range>.db — the live stock_data.db is never
touched. Re-runs are safe: already-loaded (symbol, source) pairs are
skipped.
"""

import argparse
import sys

from stock_toolkit.common import CONFIG_PATH


def main():
    parser = argparse.ArgumentParser(
        prog="stock-bootstrap",
        description=(
            "Backfill historical OHLCV via yfinance — the easy way. "
            "Takes ~2 minutes for a typical watchlist with 20+ years of "
            "history. Subsequent runs skip what's already in the DB."
        ),
    )
    parser.add_argument(
        "-s", "--symbols", nargs="+", metavar="TICKER",
        help="Symbols to backfill (default: watchlist from config.env "
             "plus anything already in the live DB)",
    )
    parser.add_argument(
        "--range", default="ALL", metavar="RANGE", dest="range_",
        help='History to fetch: "ALL" (default), a year ("2024"), or a '
             'range ("2010-2024")',
    )
    args = parser.parse_args()

    if not CONFIG_PATH.exists():
        print(f"[stock-bootstrap] {CONFIG_PATH} not found.")
        print("  Run 'stock-setup' first (yfinance needs no key, but the "
              "config file does need to exist).")
        sys.exit(1)

    print(f"[stock-bootstrap] Backfilling historical OHLCV via yfinance "
          f"(range: {args.range_}). This may take a minute or two.")
    print( "                  Output → data/stock_data_<range>.db "
           "(live stock_data.db is untouched).")
    print()

    # Delegate to the collector's --historical path.
    new_argv = ["stock-collect", "--sources", "yfinance",
                "--historical", args.range_]
    if args.symbols:
        new_argv += ["-s", *args.symbols]
    sys.argv = new_argv

    from stock_toolkit.collector.cli import main as collect_main
    collect_main()

    print()
    print("[stock-bootstrap] Done. Run 'stock-inventory --summary' to see "
          "what landed, or open the dashboard with 'stock-ui'.")


if __name__ == "__main__":
    main()
