# stock-toolkit

A self-hosted stock market data toolkit: collect OHLCV data from **7 free
APIs**, analyse it with **11 statistical tools**, rank symbols with a
**5-horizon investment score**, **backtest** strategies, get **alerts**, and
drive it all from a **Streamlit dashboard** — including a Claude-powered
briefing that explains your watchlist in plain English.

Everything runs locally: your watchlist, API keys, and data never leave your
machine (except the calls to the data providers you configure).

```
┌ collect ──────────┐   ┌ analyse ─────────────────┐   ┌ use ──────────────┐
│ yfinance          │   │ summary · regression     │   │ Streamlit UI      │
│ Alpha Vantage     │   │ returns · volatility     │   │ (6 tabs, incl.    │
│ Finnhub           │ → │ correlation · SMA · RSI  │ → │  Claude briefing) │
│ Massive (Polygon) │   │ drawdown · Bollinger     │   │ CLI entry points  │
│ FMP · Twelve Data │   │ Monte Carlo · Hurst      │   │ cron / launchd    │
│ Marketstack       │   │ scoring · backtesting    │   │ alerts (email,    │
└───────────────────┘   └──────────────────────────┘   │  Pushover, Slack) │
                                                       └───────────────────┘
```

## Quick start

Pick the install style that fits how you want to run it.

**Click-to-run app bundle (easiest)** — for users who just want the
dashboard without touching a terminal. Needs only Docker Desktop.

1. Grab `stock-app-X.Y.Z.zip` from the
   [Releases page](https://gitlab.com/Mariotti/stock-toolkit/-/releases).
2. Unzip → double-click `Stock Toolkit.command` (Mac) or
   `./Stock\ Toolkit.sh` (Linux).
3. First run launches a 1-minute wizard for your watchlist + API keys
   (yfinance works with no key). Subsequent runs go straight to the
   dashboard at http://localhost:8501.

All state persists in `./data/` next to the launcher — surviving
`docker compose down` and re-launches.

**Docker (manual)** — same stack, no launcher. Use if you prefer
explicit commands or are deploying on a NAS/server.

```bash
git clone https://gitlab.com/Mariotti/stock-toolkit.git
cd stock-toolkit
mkdir -p data && docker compose run --rm ui stock-setup   # creates data/config.env
docker compose up -d                                       # dashboard + collector
open http://localhost:8501
```

→ See [`docker/README.md`](docker/README.md) for operations.

**Native Python** — install on the host, schedule collection with cron or
launchd. Best for development or when you already have a Python env.

```bash
git clone https://gitlab.com/Mariotti/stock-toolkit.git
cd stock-toolkit/pyApi
pip install -e .          # installs stock-collect, stock-score, stock-ui, …
stock-setup               # interactive config (yfinance works with no key)
stock-bootstrap           # seed years of history via yfinance (one command)
stock-ui                  # open the dashboard
```

Or grab `stock-toolkit-X.Y.Z.tar.gz` from the Releases page and run
`bash install.sh` — pre-bundled tarball with a one-shot installer.

## Documentation

All documentation lives in [`pyApi/`](pyApi/):

| Doc | What it covers |
|---|---|
| [`pyApi/README.md`](pyApi/README.md) | Full reference: every module, flag, and workflow |
| [`pyApi/QUICKSTART.md`](pyApi/QUICKSTART.md) | Getting started — install, configure, dashboard walkthrough, scheduling |
| [`pyApi/DEVELOPING.md`](pyApi/DEVELOPING.md) | Dev environment, tests, release pipeline, conventions, CI |
| [`pyApi/SCHEMA.md`](pyApi/SCHEMA.md) | SQLite schemas + 2.x compatibility commitment |
| [`pyApi/CHANGELOG.md`](pyApi/CHANGELOG.md) | Curated release highlights |
| [`pyApi/ANALYSIS.md`](pyApi/ANALYSIS.md) | The 11 analysis tools in depth |
| [`pyApi/README_SCORE.md`](pyApi/README_SCORE.md) | How the 5-horizon scoring works |
| [`pyApi/README_BACKTEST.md`](pyApi/README_BACKTEST.md) | Backtesting strategies and metrics |
| [`pyApi/README_ALERTS.md`](pyApi/README_ALERTS.md) | Alert conditions and notification channels |

## Project facts

- Python 3.10+, packaged with `pyproject.toml`, 10 console entry points
- 304 offline tests (no network, no keys needed) run on every push in CI
- Free-tier friendly: per-source rate limiting and daily/monthly call budgets
- Not financial advice — this is a data analysis and learning tool

## License

[MIT](LICENSE)
