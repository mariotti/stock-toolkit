# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A stock market data toolkit with collection, analysis, scoring, backtesting, alerting, and a Streamlit UI. Configuration lives in `config.env` (not in git). The SQLite database `stock_data.db` is the live data store.

For the human-facing dev workflow (test layout, release pipeline,
conventions, CI), see [`DEVELOPING.md`](DEVELOPING.md) — most
patterns you'd encounter while editing this codebase are described
there in one place.

## Commands

**Install dependencies:**
```bash
pip install -e .
```

**Run the Streamlit dashboard:**
```bash
stock-ui
```

**Run tests (no external API calls, fully self-contained):**
```bash
python3 -m unittest discover -s tests        # everything (what CI runs)
python3 tests/test_toolkit.py                # core: collector/analysis/score/backtest/alerts/inventory
python3 tests/test_ui.py                     # Streamlit dashboard via AppTest
python3 tests/test_sources.py                # API fetchers against canned responses
python3 tests/test_collector_units.py        # budgets, safe_get, historical orchestration
python3 tests/test_engine_rust.py            # --engine rust dispatcher (binary discovery, argv, exit codes)
```

**Run live API integration tests (requires valid API keys in config.env):**
```bash
python3 tests/test_live_apis.py
```

**Generate docs and module diagrams (requires pdoc, pylint):**
```bash
python3 make_docs.py
```

**Build distribution package:**
```bash
python3 make_dist.py --package toolkit   # Python source dist → stock-toolkit-X.Y.Z.{tar.gz,zip}
python3 make_dist.py --package app       # Double-click Docker bundle → stock-app-X.Y.Z.{tar.gz,zip}
                                         #   writes to ./dist-app/ (separate from --package toolkit's ./dist/)
```

**Interactive config setup:**
```bash
stock-setup
```

**Bootstrap historical data (years of OHLCV via yfinance, no key):**
```bash
stock-bootstrap                  # all configured symbols, full history → data/stock_data_all.db
stock-bootstrap -s AAPL --range 2020-2024
```

**Fill missed days (targeted backfill, no key):**
```bash
stock-gap-fill --dry-run         # show what would be re-fetched
stock-gap-fill                   # fetch only the missing date ranges via yfinance
```

**Drive the Rust fetcher from Python (opt-in, v2.3.1+):**
```bash
cd ../rust-fetcher && cargo build --release && cd ../pyApi   # one-time build
stock-collect --engine rust --sources alphavantage           # subprocess to Rust binary
```
Default engine remains `python` (in-process collector — no observable change for existing users).
`--engine rust` writes to the same `stock_data.db` Python uses; cross-language dedup via
`UNIQUE(symbol, source, timestamp)`. Currently Rust supports only `alphavantage` — adding
others requires updates in two places (see `stock_toolkit/collector/engine.py:RUST_SUPPORTED_SOURCES`
and `rust-fetcher/src/main.rs` `match source_name`).

## Architecture

### Three-Phase Design

**Phase 1 — Collection (`stock_toolkit/collector/`)**
- Fetches OHLCV data from 7 sources: yfinance, Alpha Vantage, Finnhub, Polygon/Massive, FMP, Twelve Data, Marketstack
- Stores in SQLite with `UNIQUE(symbol, source, timestamp)` deduplication
- Per-source rate limiting; tiered cron scheduling (real-time / hourly / daily)
- Tracks failures in `stock_failures.db`; suppresses broken `(symbol, source)` pairs after N failures

**Phase 2 — Analysis (`stock_toolkit/analysis.py`, `stock_toolkit/score.py`, `stock_toolkit/backtest.py`)**
- 11 analysis tools: summary, regression, returns, volatility, correlation, SMA, drawdown, RSI, Bollinger Bands, Monte Carlo, Hurst
- 5-horizon scoring (week/month/quarter/year/life) with dynamic weight profiles; ranks symbols 0–100 across 9 components (incl. 12-1/3m momentum and returns-based Hurst persistence); optional `--fundamentals` valuation adjustment (±5 pts via yfinance)
- 4 backtest strategies (RSI, SMA cross, Bollinger Bands, breakout) with commission modeling vs buy-and-hold benchmark
- Source priority: alphavantage > fmp > yfinance > others; resampling from 1h → daily/weekly/monthly/quarterly
- Multi-DB support: live `stock_data.db` + historical DBs in `./data/`
- `stock_toolkit/inventory.py`: lists what's on disk per symbol (sources, intervals, date ranges); `--check` consistency report (missing trading days, thin coverage); `--remove` deletes a symbol from all DBs

**Phase 3 — UI, Alerts & Game (`stock_toolkit/ui/`, `stock_toolkit/alerts.py`, `stock_toolkit/game.py`)**
- Streamlit dashboard with 6 analytical tabs (Score, Analysis, Backtest, Alerts, Briefing, Collect) plus 3 sidebar pages: ⚙️ Admin (edit watchlist, trigger collects, inspect DB), 🎮 Game (paper-trading portfolio), and ❓ Help (in-app orientation for new users)
- Briefing tab integrates with Claude API (`ANTHROPIC_API_KEY` in config.env) for multi-turn chat; the prompt context includes a yfinance fundamentals snapshot (P/E, forward P/E, revenue/EPS growth)
- Alert system uses edge detection (fires once on False→True transition) with state in `.alerts_state.json`
- Notification channels: email (SMTP), Pushover, Slack
- Game: multiple named paper-trading "strategies" in one `portfolio.db` (v2 schema: portfolios + meta + trades-with-FK); active one persisted in `meta('active_portfolio_id')`; old single-portfolio DBs auto-migrate to a "Default" strategy on first open

### Data Flow

```
config.env (symbols, API keys) → stock_toolkit/collector/ → stock_data.db
                                                            ↓
                          stock_toolkit/analysis.py / stock_toolkit/score.py / stock_toolkit/backtest.py
                                                            ↓
                                              stock_toolkit/ui/ (Streamlit)
                                              stock_toolkit/alerts.py (cron)
```

### Key Conventions

- `stock_toolkit/common.py`: shared `config.env` parser (`load_config`) and path constants (`BASE_DIR`, `CONFIG_PATH`, `LIVE_DB`, `HIST_DIR`) — import from here, don't re-implement
- `_symbols_from_db()`: filters symbols with <2 daily bars to exclude stale/broken entries
- `SYMBOLS_IGNORE` in config: blocks bare EU tickers (e.g., `ENI` vs `ENI.MI`)
- `SYMBOL_ALIASES` in config (`source:CANONICAL=ALIAS`): per-source symbol translation — the source is queried with the alias, rows are stored under the canonical symbol (e.g., Marketstack wants `ENEL`, everything else `ENEL.MI`)
- `UI_COLLECT_SOURCES` in config: gates which sources can be triggered from the Streamlit UI
- Quote intervals are merged to `1d` so all tools see same-day data consistently
- `bin/` contains shell wrappers (`collect`, `analyse`, `score`, `backtest`, `inventory`, `alerts`) for CLI use

### Configuration (`config.env`)

Key sections:
- `SYMBOLS` — comma-separated watchlist; `SYMBOLS_IGNORE` — tickers to exclude
- `*_KEY` — API keys per source; `FINNHUB_PAID`, `ALPHAVANTAGE_PAID` — enable paid-tier endpoints
- `DB_FILE`, `OUTPUT_DIR`, `LOG_FILE`, `STATE_FILE` — paths
- `UI_COLLECT_SOURCES` — sources allowed for on-demand collection in UI
- `ALERT_EMAIL`, `PUSHOVER_*`, `SLACK_WEBHOOK_URL` — notification channels
- `ANTHROPIC_API_KEY` — for Briefing tab Claude integration

All `config.env*` files are gitignored (local copies may contain real keys).
A blanked template `config.env.template` is generated by `make_dist.py` for
distribution packages.

### Cron Scheduling

See `crontab.demo` for example tiered scheduling (times in UTC, weekdays):
- 08:00: yfinance (overnight/EU pre-market pickup)
- 13:00: yfinance, Finnhub (midday quotes)
- 23:00: all sources — full EOD sweep after US close
- Weekly: score report (Mon 06:00), DB `VACUUM; ANALYZE` and live API tests (Sun)
