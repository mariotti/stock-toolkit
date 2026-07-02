"""
stock_toolkit.collector
=======================
Collects stock market data from multiple free APIs and appends to a
SQLite database, deduplicating via a UNIQUE constraint. Pass --csv to
write to a legacy CSV file instead.

Run via the stock-collect entry point, python -m stock_toolkit.collector,
or the bin/collect wrapper (see crontab.demo for tiered cron scheduling).

Configuration comes from config.env in $STOCK_DIR / the working
directory — see stock_toolkit.common and stock_toolkit.collector.config.

Submodules: config (cfg), db, state, failures, http, sources.*,
historical, plotting, cli.
"""

from stock_toolkit.common import load_config           # noqa: F401

from . import config as cfg                            # noqa: F401
from .cli import main                                  # noqa: F401
from .db import (                                      # noqa: F401
    make_row, db_connect, db_insert_rows, dedup_key,
    load_existing_keys, csv_append_rows,
    _to_timestamp, _infer_interval, _sort_by_staleness, _symbols_from_db,
    _symbols_from_portfolios,
    _hist_has_data, _live_has_today, _quote_is_fresh, _hourly_bar_is_current,
)
from .failures import record_failure, is_suppressed, flush_failures  # noqa: F401
from .historical import parse_historical_arg, run_historical         # noqa: F401
from .sources import LIVE_FETCHERS, HIST_FETCHERS                    # noqa: F401
from .state import load_state, save_state, budget_ok, record_call    # noqa: F401
