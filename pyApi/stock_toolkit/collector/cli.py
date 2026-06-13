"""Command-line entry point for the collector."""

import argparse

from . import config as cfg
from .config import log
from .db import (
    _sort_by_staleness, _symbols_from_db,
    csv_append_rows, db_insert_rows, load_existing_keys,
)
from .failures import flush_failures, is_suppressed
from .historical import run_historical
from .plotting import PLOT_FIELDS, plot_gnuplot, plot_matplotlib
from .sources.alphavantage import fetch_alphavantage
from .sources.finnhub import fetch_finnhub
from .sources.fmp import fetch_fmp
from .sources.marketstack import fetch_marketstack
from .sources.polygon import fetch_polygon
from .sources.twelvedata import fetch_twelvedata
from .sources.yfinance import fetch_yfinance
from .state import load_state, save_state

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
            "Useful for tiered cron scheduling (see crontab.demo):\n"
            "  --sources yfinance           (08:00 — overnight/EU pre-market)\n"
            "  --sources yfinance finnhub   (13:00 — midday quotes)\n"
            "  --sources yfinance alphavantage polygon fmp twelvedata marketstack\n"
            "                               (23:00 — full EOD sweep after US close)"
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
    #      (cfg.SYMBOLS_IGNORE still applies even with -s)
    #   2. No flag             → config cfg.SYMBOLS ∪ symbols already in the DB,
    #      minus anything in cfg.SYMBOLS_IGNORE
    if args.symbol:
        sym = args.symbol.upper()
        if sym in cfg.SYMBOLS_IGNORE:
            log.warning(f"Symbol '{sym}' is in cfg.SYMBOLS_IGNORE — skipping.")
            return
        symbols = [sym]
    else:
        db_syms  = _symbols_from_db()
        cfg_syms = [s for s in cfg.SYMBOLS if s not in cfg.SYMBOLS_IGNORE]
        # merge, preserve config order first, then any DB-only extras
        seen     = set(cfg_syms)
        symbols  = list(cfg_syms) + [s for s in db_syms
                                      if s not in seen
                                      and s not in cfg.SYMBOLS_IGNORE]
        if cfg.SYMBOLS_IGNORE:
            blocked = [s for s in (list(cfg.SYMBOLS) + db_syms) if s in cfg.SYMBOLS_IGNORE]
            if blocked:
                log.info(f"Symbols blocked by cfg.SYMBOLS_IGNORE: {sorted(set(blocked))}")
        if db_syms:
            extras = [s for s in db_syms
                      if s not in set(cfg.SYMBOLS) and s not in cfg.SYMBOLS_IGNORE]
            if extras:
                log.info(f"Symbols from DB not in config (kept): {extras}")

        # Sort so least-recently-updated symbols run first — ensures budget-limited
        # sources serve stale symbols before fresh ones when limits are hit mid-run
        symbols = _sort_by_staleness(symbols)
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
    if state.get('monthly_calls'):
        log.info(f"Monthly call counts ({state.get('month','?')}): {state['monthly_calls']}")

    # ── historical mode ──────────────────────────────────────

    if args.historical:
        try:
            active_db = run_historical(symbols, args.historical, state)
        except ValueError as e:
            log.error(str(e))
            return
        save_state(state)
        log.info(f"Updated call counts: {state['calls']}")
        if state.get('monthly_calls'):
            log.info(f"Monthly totals ({state.get('month','?')}): {state['monthly_calls']}")
        if args.plot_gnuplot:
            plot_gnuplot(symbols, use_csv=False, field=plot_field, db_path=active_db)
        if args.plot_matplotlib:
            plot_matplotlib(symbols, use_csv=False, field=plot_field, db_path=active_db)
        flush_failures()
        log.info("Done.\n")
        return

    # ── live collection mode ─────────────────────────────────

    log.info(f"Backend: {'CSV → ' + str(cfg.CSV_PATH) if use_csv else 'SQLite → ' + str(cfg.DB_PATH)}")

    # Each fetcher is a (name, label, callable) tuple.
    # Fetchers run in parallel via ThreadPoolExecutor — safe because:
    #   - HTTP calls are I/O-bound, GIL is released during socket waits
    #   - SQLite skip-function reads use WAL mode (readers never block)
    #   - state["calls"] keys are per-source, no two fetchers share one
    #   - rows are collected per-fetcher and merged after all complete
    #
    # SYMBOL_ALIASES (incl. built-in suffix-strip defaults — see config.py):
    # each source is queried with the names it understands; returned rows
    # are stored under the canonical names.
    def _aliased(source, fetch, *fetch_args):
        return cfg.canonicalize_rows(
            source,
            fetch(cfg.aliased_symbols(source, symbols), *fetch_args),
            symbols)

    fetchers = [
        ("yfinance",     "── yfinance ─────────────────────────────────────────",
         lambda: _aliased("yfinance", fetch_yfinance)),
        ("alphavantage", "── Alpha Vantage ────────────────────────────────────",
         lambda: _aliased("alphavantage", fetch_alphavantage, state)),
        ("finnhub",      "── Finnhub ───────────────────────────────────────────────",
         lambda: _aliased("finnhub", fetch_finnhub, state)),
        ("polygon",      "── Massive (formerly Polygon.io) ────────────────────────",
         lambda: _aliased("polygon", fetch_polygon, state)),
        ("fmp",          "── Financial Modeling Prep (FMP) ────────────────",
         lambda: _aliased("fmp", fetch_fmp, state)),
        ("twelvedata",   "── Twelve Data ──────────────────────────────────────────",
         lambda: _aliased("twelvedata", fetch_twelvedata, state)),
        ("marketstack",  "── Marketstack ──────────────────────────────────────────",
         lambda: _aliased("marketstack", fetch_marketstack, state)),
    ]

    active = [(name, label, fn)
              for name, label, fn in fetchers
              if _should_run(name)]

    # Log suppression summary so it's visible at the start of each run
    if cfg.FAILURES_DB_PATH.exists():
        sources = [name for name, _, _ in active]
        suppressed_counts = {
            src: sum(1 for s in symbols if is_suppressed(s, src))
            for src in sources
        }
        total_suppressed = sum(suppressed_counts.values())
        if total_suppressed:
            summary = ", ".join(
                f"{src}:{n}" for src, n in suppressed_counts.items() if n > 0
            )
            log.info(f"Suppressed (symbol, source) pairs: {total_suppressed} — {summary}")

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
        log.info(f"Fetched {len(all_rows)} rows | {added} new rows appended to {cfg.CSV_PATH.name}")
    else:
        added = db_insert_rows(all_rows)
        log.info(f"Fetched {len(all_rows)} rows | {added} new rows inserted into {cfg.DB_PATH.name}")

    log.info(f"Updated call counts: {state['calls']}")
    if state.get('monthly_calls'):
        log.info(f"Monthly totals ({state.get('month','?')}): {state['monthly_calls']}")

    # ── plot ───────────────────────────────────────────

    if args.plot_gnuplot:
        plot_gnuplot(symbols, use_csv, plot_field)

    if args.plot_matplotlib:
        plot_matplotlib(symbols, use_csv, plot_field)

    log.info("Done.\n")

    # ── cleanup ────────────────────────────────────────
    flush_failures()          # write accumulated failures to stock_failures.csv


