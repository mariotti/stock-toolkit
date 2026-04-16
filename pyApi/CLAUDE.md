# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A stock market data toolkit with collection, analysis, scoring, backtesting, alerting, and a Streamlit UI. Configuration lives in `config.env` (not in git). The SQLite database `stock_data.db` is the live data store.

## Commands

**Install dependencies:**
```bash
pip install -r requirements.txt
```

**Run the Streamlit dashboard:**
```bash
streamlit run stock_ui.py
```

**Run tests (no external API calls, fully self-contained):**
```bash
python3 test_toolkit.py
python3 -m pytest test_toolkit.py -v --tb=short   # if pytest installed
```

**Run live API integration tests (requires valid API keys in config.env):**
```bash
python3 test_live_apis.py
```

**Generate docs and module diagrams (requires pdoc, pylint):**
```bash
python3 make_docs.py
```

**Build distribution package:**
```bash
python3 make_dist.py --package toolkit
```

**Interactive config setup:**
```bash
python3 stock_setup.py
```

## Architecture

### Three-Phase Design

**Phase 1 — Collection (`stock_collector.py`)**
- Fetches OHLCV data from 7 sources: yfinance, Alpha Vantage, Finnhub, Polygon/Massive, FMP, Twelve Data, Marketstack
- Stores in SQLite with `UNIQUE(symbol, source, timestamp)` deduplication
- Per-source rate limiting; tiered cron scheduling (real-time / hourly / daily)
- Tracks failures in `stock_failures.db`; suppresses broken `(symbol, source)` pairs after N failures

**Phase 2 — Analysis (`stock_analysis.py`, `stock_score.py`, `stock_backtest.py`)**
- 11 analysis tools: summary, regression, returns, volatility, correlation, SMA, drawdown, RSI, Bollinger Bands, Monte Carlo, Hurst
- 5-horizon scoring (week/month/quarter/year/life) with dynamic weight profiles; ranks symbols 0–100
- 4 backtest strategies (RSI, SMA cross, Bollinger Bands, breakout) with commission modeling vs buy-and-hold benchmark
- Source priority: alphavantage > fmp > yfinance > others; resampling from 1h → daily/weekly/monthly/quarterly
- Multi-DB support: live `stock_data.db` + historical DBs in `./data/`

**Phase 3 — UI & Alerts (`stock_ui.py`, `stock_alerts.py`)**
- Streamlit dashboard with 6 tabs: Score, Analysis, Backtest, Alerts, Briefing, Collect
- Briefing tab integrates with Claude API (`ANTHROPIC_API_KEY` in config.env) for multi-turn chat
- Alert system uses edge detection (fires once on False→True transition) with state in `.alerts_state.json`
- Notification channels: email (SMTP), Pushover, Slack

### Data Flow

```
config.env (symbols, API keys) → stock_collector.py → stock_data.db
                                                            ↓
                          stock_analysis.py / stock_score.py / stock_backtest.py
                                                            ↓
                                              stock_ui.py (Streamlit)
                                              stock_alerts.py (cron)
```

### Key Conventions

- `_symbols_from_db()`: filters symbols with <2 daily bars to exclude stale/broken entries
- `SYMBOLS_IGNORE` in config: blocks bare EU tickers (e.g., `ENI` vs `ENI.MI`)
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

`config.env_empty` is the committed template with all values blanked.

### Cron Scheduling

See `crontab.demo` for example tiered scheduling:
- Every 10 min: yfinance, Finnhub (real-time sources)
- Hourly: hourly sources
- 16:00 daily: daily sources (after US market close)
