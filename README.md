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

**Docker** — dashboard + scheduled collector in one stack, runs on macOS,
Linux, ARM NAS, etc. Multi-arch image, no Python on the host.

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
stock-collect             # fetch today's data
stock-ui                  # open the dashboard
```

Or grab a packaged release from the
[Releases page](https://gitlab.com/Mariotti/stock-toolkit/-/releases) and run
`bash install.sh`.

## Documentation

All documentation lives in [`pyApi/`](pyApi/):

| Doc | What it covers |
|---|---|
| [`pyApi/README.md`](pyApi/README.md) | Full reference: every module, flag, and workflow |
| [`pyApi/QUICKSTART.md`](pyApi/QUICKSTART.md) | 3-step install for release packages |
| [`pyApi/QUICKSTART_DEV.md`](pyApi/QUICKSTART_DEV.md) | CLI usage from a source checkout |
| [`pyApi/ANALYSIS.md`](pyApi/ANALYSIS.md) | The 11 analysis tools in depth |
| [`pyApi/README_SCORE.md`](pyApi/README_SCORE.md) | How the 5-horizon scoring works |
| [`pyApi/README_BACKTEST.md`](pyApi/README_BACKTEST.md) | Backtesting strategies and metrics |
| [`pyApi/README_ALERTS.md`](pyApi/README_ALERTS.md) | Alert conditions and notification channels |

## Project facts

- Python 3.10+, packaged with `pyproject.toml`, 8 console entry points
- 214 offline tests (no network, no keys needed) run on every push in CI
- Free-tier friendly: per-source rate limiting and daily/monthly call budgets
- Not financial advice — this is a data analysis and learning tool

## License

[MIT](LICENSE)
