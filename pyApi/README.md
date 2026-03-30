# Stock Data Toolkit

A collection of seven Python scripts for collecting, analysing, backtesting, and monitoring stock market data from multiple free (and optionally paid) APIs — with a browser-based Streamlit dashboard that brings it all together.

```
stock_collector.py    — fetch and store data (live + historical)
stock_analysis.py     — load, resample, and run 11 analytical tools
stock_inventory.py    — inspect what data is on disk
stock_score.py        — rank symbols by investment score across five horizons
stock_backtest.py     — backtest strategies against historical data
stock_alerts.py       — watch for conditions and send notifications
stock_ui.py           — Streamlit dashboard (Score · Analysis · Backtest · Alerts)
```

---

## Table of contents

- [Quick start](#quick-start)
- [Installation](#installation)
- [Directory layout](#directory-layout)
- [API keys](#api-keys)
- [stock\_collector.py](#stock_collectorpy)
  - [Configuration](#configuration)
  - [Live collection](#live-collection)
  - [Historical collection](#historical-collection)
  - [Plotting](#plotting)
  - [Deduplication and skip logic](#deduplication-and-skip-logic)
  - [Rate limits and budgets](#rate-limits-and-budgets)
  - [Paid tier flags](#paid-tier-flags)
- [stock\_analysis.py](#stock_analysispy)
  - [Loading data](#loading-data)
  - [Date ranges](#date-ranges)
  - [Interval and granularity](#interval-and-granularity)
- [stock\_score.py](#stock_scorepy)
- [stock\_backtest.py](#stock_backtestpy)
- [stock\_alerts.py](#stock_alertspy)
- [stock\_ui.py](#stock_uipy)
- [Common workflows](#common-workflows)
- [Testing](#testing)
- [Git and data files](#git-and-data-files)
- [Troubleshooting](#troubleshooting)
  - [Analysis tools](#analysis-tools)
  - [Exporting data](#exporting-data)
- [stock\_inventory.py](#stock_inventorypy)
- [Common workflows](#common-workflows)
- [Git and data files](#git-and-data-files)
- [Troubleshooting](#troubleshooting)

---

## Quick start

```bash
# 1. Install dependencies
pip install requests yfinance pandas scipy matplotlib

# 2. Edit stock_collector.py — set your symbols and API keys (see below)

# 3. Collect today's data (yfinance works without any key)
python3 stock_collector.py

# 4. See what you have
python3 stock_inventory.py --summary

# 5. Analyse it
python3 stock_analysis.py -s AAPL --analysis summary regression --plot
```

---

## Installation

```bash
pip install requests yfinance pandas scipy matplotlib
```

All five packages are required. `scipy` is used by `regression` and statistical
analyses. `matplotlib` is used by all `--plot` outputs. `yfinance` is the only
data source that needs no API key.

---

## Directory layout

After a typical run the directory looks like this:

```
stock_collector.py
stock_analysis.py
stock_inventory.py
stock_score.py
stock_backtest.py
stock_alerts.py
stock_ui.py                 ← Streamlit dashboard
test_toolkit.py             ← offline test suite (no API calls)
test_live_apis.py           ← live API connectivity tests
make_dist.py                ← create a clean public distribution
crontab.demo                ← example crontab (copy and edit)
config.env                  ← API keys and symbols (keep out of git)
README.md
ANALYSIS.md
README_SCORE.md
README_BACKTEST.md
README_ALERTS.md
requirements.txt

stock_data.db               ← live collection (SQLite)
stock_data.csv              ← legacy output (--csv flag only)
collector.log               ← timestamped run log
.collector_state.json       ← internal: daily API call counters
.alerts_state.json          ← internal: alert edge-detection state

data/                       ← historical DBs (--historical flag)
    stock_data_2024.db
    stock_data_2010-2020.db
    stock_data_all.db

gnuplot-data/               ← gnuplot output (--plot-gnuplot)
    stock_gnuplot_AAPL.dat
    stock_plot.gp

matplot/                    ← matplotlib output (--plot-matplotlib)
    stock_plot_close.png
```

The `data/`, `gnuplot-data/`, and `matplot/` folders are created automatically
on first use.

---

## API keys

Six data sources are supported. All have a free tier. Edit the `API_KEYS`
section at the top of `stock_collector.py`:

```python
API_KEYS = {
    "alphavantage": "",   # https://www.alphavantage.co/support/#api-key
    "finnhub":      "",   # https://finnhub.io/register
    "polygon":      "",   # https://massive.com/dashboard (formerly Polygon.io)
    "fmp":          "",   # https://financialmodelingprep.com/developer/docs
    "twelvedata":   "",   # https://twelvedata.com/register
    "marketstack":  "",   # https://marketstack.com/signup/free
}
```

Any key left as `""` is silently skipped. `yfinance` requires no key.

### Free tier limits

| Source | Free daily limit | History depth | Notes |
|---|---|---|---|
| yfinance | No limit (unofficial) | Full (to IPO) | Web scraper — can break |
| Alpha Vantage | 25 calls/day | ~100 days (free) / 20+ yr (paid) | `outputsize=full` is premium |
| Finnhub | 60 calls/min | Quote only (free) / full (paid) | `/stock/candle` needs paid plan |
| Massive (Polygon.io) | 5 calls/min | ~2 years | US equities only |
| FMP | 250 calls/day | 30+ years | Best free historical source |
| Twelve Data | 800 calls/day | ~19 years per chunk | 50+ global exchanges |
| Marketstack | ~3/day (100/month) | 30+ years EOD | Wide exchange coverage |

**Recommended first keys to get:** FMP and Twelve Data — both sign up instantly,
no credit card, and together they give you decades of clean daily history with
generous call budgets.

---

## stock\_collector.py

### Configuration

All settings are at the top of the file in the `CONFIG` section:

```python
# Symbols to track
SYMBOLS = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]

# API keys (see above)
API_KEYS = { ... }

# Paid tier flags
FINNHUB_PAID      = False   # set True to unlock /stock/candle bars
ALPHAVANTAGE_PAID = False   # set True to unlock adjusted closes + full history
```

### Live collection

Fetches the latest data from all configured sources and appends it to
`stock_data.db`.

```bash
# collect all configured symbols (all sources)
python3 stock_collector.py

# collect only specific sources — useful for tiered cron scheduling
python3 stock_collector.py --sources finnhub fmp          # real-time quotes only
python3 stock_collector.py --sources yfinance twelvedata  # hourly bars only
python3 stock_collector.py --sources alphavantage polygon marketstack  # daily only

# collect a single symbol only
python3 stock_collector.py -s AAPL
python3 stock_collector.py --symbol CAT

# write to CSV instead of SQLite (legacy mode)
python3 stock_collector.py --csv
```

**What gets stored per run:**

| Source | Interval | What |
|---|---|---|
| yfinance | `1d` | Last 7 days of daily OHLCV |
| yfinance | `1h` | Last 5 days of hourly OHLCV |
| Alpha Vantage | `1d` | Last ~100 days (free) |
| Finnhub | `quote` | Real-time snapshot (open, high, low, close, volume, % change) |
| Polygon | `1d` | Last 30 days |
| FMP | `quote` | Real-time snapshot + P/E, market cap, 52-week high/low |
| FMP | `1d` | Last 90 days |
| Twelve Data | `1d` | Last 30 days |
| Twelve Data | `1h` | Last 24 hours |
| Marketstack | `1d` | Last batch (all symbols in one call) |

**Running via cron:**

```bash
# every 30 minutes
*/30 * * * * /usr/bin/python3 /path/to/stock_collector.py

# every 10 minutes
*/10 * * * * /usr/bin/python3 /path/to/stock_collector.py

# single symbol, every hour
0 * * * * /usr/bin/python3 /path/to/stock_collector.py -s AAPL
```

### Historical collection

Fetches a full date range from all sources that support it, and saves to a
dedicated database in `./data/` — never touching `stock_data.db`.

```bash
# a single year  → data/stock_data_2024.db
python3 stock_collector.py --historical 2024

# a year range   → data/stock_data_2010-2020.db
python3 stock_collector.py --historical 2010-2020

# everything available  → data/stock_data_all.db
python3 stock_collector.py --historical ALL

# historical for a specific symbol only
python3 stock_collector.py -s AAPL --historical 2020-2023
```

Re-running `--historical` is safe. Before calling each API the script checks
whether `(symbol, source)` already has rows in the requested date range. If it
does, that API call is skipped entirely.

**Historical source capabilities:**

| Source | Free depth | Notes |
|---|---|---|
| yfinance | Full (to IPO) | Best free option for long history |
| Alpha Vantage | ~100 days (free) | `outputsize=full` needs paid plan |
| Finnhub | Skipped (free) | Needs `FINNHUB_PAID = True` |
| Polygon | ~2 years | Paginates automatically |
| FMP | 30+ years | Uses `from`/`to` date params |
| Twelve Data | ~19 yr/chunk | Chunked automatically for long ranges |
| Marketstack | Skipped | Monthly budget too small for bulk loads |

### Plotting

After collecting, plots can be generated immediately:

```bash
# generate gnuplot files
python3 stock_collector.py --plot-gnuplot

# generate matplotlib PNG (opens interactive window too)
python3 stock_collector.py --plot-matplotlib

# choose which field to plot (default: close)
python3 stock_collector.py --plot-gnuplot --plot-data volume
python3 stock_collector.py --plot-matplotlib --plot-data change_pct

# valid --plot-data choices:
# close (default), open, high, low, volume, vwap, change_pct

# combine with --historical
python3 stock_collector.py --historical 2020-2023 --plot-matplotlib --plot-data close
```

**gnuplot usage** — after running `--plot-gnuplot`:

```bash
gnuplot gnuplot-data/stock_plot.gp          # render to PNG
gnuplot -p gnuplot-data/stock_plot.gp       # interactive window + PNG
```

The `.gp` script and `.dat` files are written to `gnuplot-data/`. Each `.dat`
file contains one gnuplot index block per data source, so each source appears
as a separate line in the plot.

### Deduplication and skip logic

Data is never duplicated. The pipeline has two layers:

1. **Before the API call** — `_live_has_today()` checks if today's row already
   exists in the DB. If it does, the HTTP request is skipped entirely,
   protecting your daily API budgets.
2. **After fetching** — `INSERT OR IGNORE` on a `UNIQUE (symbol, source,
   data_date, interval)` constraint catches anything that slips through (e.g.
   two cron jobs overlapping).

The same logic applies to `--historical` via `_hist_has_data()`, which checks
whether a `(symbol, source)` pair already has rows in the requested date range.

### Rate limits and budgets

The script automatically tracks daily call counts in `.collector_state.json`
(resets at midnight). When a source would exceed its free-tier daily budget,
it is skipped for the rest of the day's runs with a warning in the log.

```
[alphavantage] daily budget exhausted (25/25), skipping.
```

Per-minute limits (Finnhub: 60/min, Polygon: 5/min) are enforced with
`time.sleep()` between calls.

### Paid tier flags

```python
# Alpha Vantage
ALPHAVANTAGE_PAID = False   # free:  TIME_SERIES_DAILY (unadjusted, ~100 days)
ALPHAVANTAGE_PAID = True    # paid:  TIME_SERIES_DAILY_ADJUSTED (split/dividend
                             #        adjusted closes, 20+ years full history)

# Finnhub
FINNHUB_PAID = False         # free:  /quote only (real-time snapshot)
FINNHUB_PAID = True          # paid:  /quote + /stock/candle (OHLCV bars,
                             #        live 30 days + full historical range)
```

---

## stock\_analysis.py

Reads from all databases (`stock_data.db` + `data/*.db`), merges them,
deduplicates by source priority, resamples to the requested granularity,
and runs the requested analyses.

```bash
# see what symbols are available before analysing
python3 stock_analysis.py --list-symbols

# basic summary for one symbol
python3 stock_analysis.py -s AAPL

# multiple symbols, multiple tools, with plots
python3 stock_analysis.py -s AAPL MSFT GOOGL \
    --analysis summary regression correlation \
    --plot
```

### Loading data

The script automatically discovers and merges:
- `./stock_data.db` (live collection)
- `./data/*.db` (all historical databases)

**Source priority** — when multiple APIs have data for the same
`(symbol, date)`, one is kept based on this priority order:
`alphavantage → fmp → yfinance → finnhub → twelvedata → polygon → marketstack`

Override with `--source`:

```bash
# use only yfinance data
python3 stock_analysis.py -s AAPL --source yfinance

# use only FMP data
python3 stock_analysis.py -s AAPL --source fmp
```

### Date ranges

Both `--from` and `--to` are optional and can be used independently:

```bash
# specific range
python3 stock_analysis.py -s AAPL --from 2022-01-01 --to 2023-12-31

# everything from a date onwards
python3 stock_analysis.py -s AAPL --from 2020-01-01

# everything up to a date
python3 stock_analysis.py -s AAPL --to 2024-12-31

# no date filter — loads all available data
python3 stock_analysis.py -s AAPL
```

### Interval and granularity

**`--interval`** controls which *source rows* are loaded from the database:

| Value | Loads |
|---|---|
| `1d` (default) | Daily bars |
| `1h` | Hourly intraday bars |
| `auto` | Hourly if available, else daily |

```bash
# daily bars (default)
python3 stock_analysis.py -s AAPL

# intraday hourly bars
python3 stock_analysis.py -s AAPL --interval 1h

# auto-detect
python3 stock_analysis.py -s AAPL --interval auto
```

**`--granularity`** controls how loaded bars are *resampled* before analysis:

| Value | Result |
|---|---|
| `auto` (default) | Adapts to date span (see table below) |
| `raw` | No resampling — use data as stored |
| `1h` | Hourly buckets (intraday only) |
| `2h` | 2-hour buckets |
| `4h` | 4-hour buckets |
| `1d` | Daily bars |
| `1w` | Weekly (close on Friday) |
| `1M` | Monthly |
| `1Q` | Quarterly |
| `1Y` | Yearly |

**Auto granularity thresholds:**

| Source interval | Date span | Auto picks |
|---|---|---|
| Daily | ≤ 1 year | `1d` |
| Daily | ≤ 5 years | `1w` |
| Daily | ≤ 20 years | `1M` |
| Daily | > 20 years | `1Q` |
| Intraday | ≤ 2 days | `1h` |
| Intraday | ≤ 1 week | `2h` |
| Intraday | ≤ 30 days | `4h` |
| Intraday | > 30 days | `1d` |

```bash
# force weekly granularity on daily data
python3 stock_analysis.py -s AAPL --granularity 1w

# intraday hourly data, resampled to 4-hour bars
python3 stock_analysis.py -s AAPL --interval 1h --granularity 4h

# raw data, no resampling
python3 stock_analysis.py -s AAPL --granularity raw
```

**`--field`** selects which price column to analyse (default: `close`):

```bash
python3 stock_analysis.py -s AAPL --field volume --analysis summary
python3 stock_analysis.py -s AAPL --field high --analysis regression --plot
# choices: close, open, high, low, volume, vwap, change_pct
```

### Analysis tools

Any number of tools can be combined in a single run:

```bash
python3 stock_analysis.py -s AAPL \
    --analysis summary regression returns volatility sma drawdown rsi bbands montecarlo hurst \
    --plot
```

---

#### `summary`

Descriptive statistics per symbol: first/last price, total return, min/max,
mean, standard deviation, annualised volatility, Sharpe ratio, and bar count.

```bash
python3 stock_analysis.py -s AAPL MSFT --analysis summary
```

---

#### `regression`

Linear trend fitted to price vs time. Reports slope (price units/day),
annualised trend percentage, R², p-value, and significance at 5%.
With `--plot`: renders the price series with the regression line and a 95%
confidence interval band.

```bash
python3 stock_analysis.py -s AAPL --analysis regression --plot
python3 stock_analysis.py -s AAPL MSFT --from 2020-01-01 --analysis regression --plot
```

---

#### `returns`

Periodic return distribution per symbol. Reports mean return, standard
deviation, worst/best single-period return, percentage of positive periods,
and Sharpe ratio. With `--plot`: histogram per symbol with mean line marked.

```bash
python3 stock_analysis.py -s AAPL --analysis returns --plot
python3 stock_analysis.py -s AAPL --granularity 1w --analysis returns --plot
```

---

#### `volatility`

Rolling annualised volatility (standard deviation of returns × √annualisation
factor). Reports latest, mean, min, and max volatility. With `--plot`: line
chart per symbol.

```bash
python3 stock_analysis.py -s AAPL --analysis volatility --window 20 --plot
python3 stock_analysis.py -s AAPL --interval 1h --analysis volatility --window 12 --plot
```

`--window N` — rolling window size in bars (default: 30).

The annualisation factor is automatically adjusted for granularity:
`1d` → 252, `1h` → 1638, `1w` → 52, `1M` → 12, `1Q` → 4.

---

#### `correlation`

Pearson correlation matrix of periodic returns between all requested symbols.
Requires at least 2 symbols. With `--plot`: colour-coded heatmap
(green = positive, red = negative correlation).

```bash
python3 stock_analysis.py -s AAPL MSFT GOOGL AMZN --analysis correlation --plot
python3 stock_analysis.py -s AAPL MSFT --from 2020-01-01 --analysis correlation --plot
```

---

#### `sma`

Simple moving average overlay. Reports current SMA values and whether price
is above or below each. With `--plot`: price chart with SMA lines.

```bash
python3 stock_analysis.py -s AAPL --analysis sma --plot
python3 stock_analysis.py -s AAPL --analysis sma --sma-windows 20 50 200 --plot
```

`--sma-windows N [N ...]` — SMA periods to compute (default: `20 50 200`).

---

#### `drawdown`

Drawdown analysis from peak to trough. Reports maximum drawdown (%), drawdown
duration in bars, recovery time, annualised return, and Calmar ratio
(annualised return / |max drawdown|). With `--plot`: underwater chart showing
the drawdown over time.

```bash
python3 stock_analysis.py -s AAPL --analysis drawdown --plot
python3 stock_analysis.py -s AAPL MSFT --from 2020-01-01 --analysis drawdown --plot
```

---

#### `rsi`

Relative Strength Index using Wilder smoothing (EMA with α = 1/window).
Reports latest RSI value, overbought (≥70) / oversold (≤30) / neutral signal,
and bar counts spent in each zone. With `--plot`: dual-panel chart of price
and RSI with threshold lines.

```bash
python3 stock_analysis.py -s AAPL --analysis rsi --window 14 --plot
python3 stock_analysis.py -s AAPL --interval 1h --analysis rsi --window 14 --plot
```

`--window N` — RSI period (default: 30; standard is 14).

---

#### `bbands`

Bollinger Bands: SMA ± 2 standard deviations. Reports the current lower, mid,
and upper band values, %B (0 = at lower band, 1 = at upper band), bandwidth
(as % of mid), and a squeeze signal (bandwidth in bottom 20th percentile —
often precedes a breakout). With `--plot`: price chart with bands and shaded
fill.

```bash
python3 stock_analysis.py -s AAPL --analysis bbands --window 20 --plot
```

`--window N` — lookback period for SMA and standard deviation (default: 30;
standard is 20).

---

#### `montecarlo`

Geometric Brownian Motion simulation. Estimates drift (μ) and volatility (σ)
from the historical returns in the loaded data, then simulates N price paths
forward for the given horizon. Reports the P5/P25/P50/P75/P95 fan at the
horizon, expected return, and probability of finishing above the current price.
With `--plot`: path fan chart and terminal price histogram.

```bash
python3 stock_analysis.py -s AAPL --analysis montecarlo --plot
python3 stock_analysis.py -s AAPL --analysis montecarlo --mc-paths 5000 --mc-horizon 63 --plot
```

`--mc-paths N` — number of simulated paths (default: 1000).
`--mc-horizon BARS` — forecast horizon in bars (default: 252 ≈ 1 trading year).

---

#### `hurst`

Hurst exponent estimated via R/S analysis over log-spaced lags. Classifies the
series as:

- **H > 0.55** — trending (persistent, momentum follows through)
- **H ≈ 0.5** — random walk (no memory)
- **H < 0.45** — mean-reverting (anti-persistent, overshoots revert)

With `--plot`: log-log scatter of R/S vs lag with fitted slope line.

```bash
python3 stock_analysis.py -s AAPL --analysis hurst --plot
python3 stock_analysis.py -s AAPL MSFT GOOGL --analysis hurst --plot
```

Requires at least 40 bars.

---

### Exporting data

The `--save` flag writes the processed dataset (after source dedup and
resampling) to a CSV file. It can be combined with any analysis run:

```bash
# save without running any analysis
python3 stock_analysis.py -s AAPL --save aapl.csv

# save and also run analyses
python3 stock_analysis.py -s AAPL MSFT \
    --from 2022-01-01 --to 2024-12-31 \
    --granularity 1w \
    --analysis summary regression \
    --save aapl_msft_weekly.csv \
    --plot
```

For a truly raw export from the database (no processing):

```bash
sqlite3 stock_data.db ".mode csv" ".headers on" \
    "SELECT * FROM prices WHERE symbol='AAPL'" > aapl_raw.csv
```

---

## stock\_inventory.py

Lists all data available on disk across all databases, with symbol, interval,
date range, span, row count, sources, and database file.

```bash
# detailed view: one row per (symbol, interval, source, database)
python3 stock_inventory.py

# summary view: one row per (symbol, interval), sources merged
python3 stock_inventory.py --summary

# filter to specific symbols
python3 stock_inventory.py -s AAPL MSFT

# filter to specific symbols, summary view
python3 stock_inventory.py -s AAPL --summary

# scan a specific folder or file
python3 stock_inventory.py --db ./data
python3 stock_inventory.py --db data/stock_data_2020-2023.db

# machine-readable JSON output (pipe to jq etc.)
python3 stock_inventory.py --json
python3 stock_inventory.py --json | jq '.[] | select(.symbol=="AAPL")'

# remove a symbol from every database (prompts for confirmation)
python3 stock_inventory.py --remove TSLA

# remove without prompt — set env var to allow (safe for scripts/cron)
STOCK_INV_REMOVE=allow python3 stock_inventory.py --remove TSLA

# check data consistency: missing trading days, thin coverage
python3 stock_inventory.py --check
python3 stock_inventory.py --check -s AAPL MSFT   # specific symbols only
```

**`--remove`** deletes all rows for the symbol across every database and runs
`VACUUM` to reclaim disk space. By default it requires you to type the symbol
name to confirm. Setting `STOCK_INV_REMOVE=allow` in the environment skips
the prompt — useful in cron jobs or scripts.

**`--check`** reports two types of issue:

| Issue | What it means |
|---|---|
| Missing days | Trading days within the symbol's date range that have no bar. Consecutive gaps are shown as ranges: `2024-08-05..07`. |
| Thin coverage | Fewer than 60% of expected bars present — likely a partial or failed collection. |

The trading-day calendar is derived automatically from your own data — days
where ≥50% of your symbols have a bar are treated as real trading days,
so holidays and non-trading days are excluded from gap counts without needing
an external market calendar.

Example output:

```
Scanning 2 database(s):

  stock_data.db                             1,204.0 KB
  stock_data_2020-2023.db                   8,441.0 KB

Symbol  Interval  From        To          Span     Rows   Sources             DBs
──────  ────────  ──────────  ──────────  ───────  ─────  ──────────────────  ─────────────────────────
AAPL    1d        2020-01-02  2024-04-30  4yr 4mo  2,430  fmp, yfinance       stock_data.db, stock_...
AAPL    1h        2024-04-01  2024-04-03  2d          48  yfinance            stock_data.db
MSFT    1d        2024-01-02  2024-04-30  3mo        240  fmp, yfinance       stock_data.db

2,718 total rows  ·  2 symbol(s)  ·  2 interval type(s)  ·  2 source(s)  ·  2 database(s)
```

---

## stock\_score.py

Runs all seven analysis steps and ranks symbols by a 0–100 investment score.
The `--horizon` flag reshapes the scoring weights to match your intended
holding period — entry timing dominates for short horizons, trend quality
and risk dominate for long ones.

```bash
# Rank all symbols for a quarterly hold (default)
python3 stock_score.py --from 2023-01-01

# What looks good to buy this week?
python3 stock_score.py -s AAPL MSFT GOOGL CSMIB.MI TSLA ENEL.MI \
    --from 2023-01-01 --horizon week

# Best long-term compounder?
python3 stock_score.py --from 2023-01-01 --horizon life --top 3

# Per-metric breakdown
python3 stock_score.py --from 2023-01-01 --horizon quarter --detail
```

Available horizons: `week` `month` `quarter` `year` `life`

See **README\_SCORE.md** for the full scoring model, weight profiles, and
output guide.

---

## stock\_backtest.py

Replays a trading strategy against historical price data and compares it
to a buy-and-hold benchmark. Signals are generated at bar close with no
lookahead. Commission and slippage are configurable.

```bash
# RSI reversal strategy
python3 stock_backtest.py -s AAPL --strategy rsi --window 14 --plot

# Moving average crossover
python3 stock_backtest.py -s AAPL --strategy sma_cross --fast 20 --slow 50 --plot

# Walk-forward validation (train 2018–2022, test 2023+)
python3 stock_backtest.py -s AAPL --strategy sma_cross \
    --from 2018-01-01 --test-from 2023-01-01 --plot
```

Available strategies: `rsi` `sma_cross` `bbands` `breakout`

See **README\_BACKTEST.md** for all strategies, flags, output metrics,
and limitations.

---

## stock\_alerts.py

Evaluates conditions against the latest collected data and fires
notifications when a condition transitions from false to true (edge
detection — no repeated alerts for the same ongoing condition).

```bash
# Watch for RSI oversold
python3 stock_alerts.py -s AAPL MSFT --when "rsi14 < 30"

# Multiple conditions, push notification
python3 stock_alerts.py -s AAPL GOOGL CSMIB.MI \
    --when "rsi14 < 30" --when "bbands_squeeze" --notify pushover

# See all available indicator names
python3 stock_alerts.py --list-conditions

# Check current alert state
python3 stock_alerts.py --status
```

Configure notification channels (email, Pushover, Slack) in `config.env`.
See **README\_ALERTS.md** for all conditions, channels, cron setup,
and state management.

---

## stock\_ui.py

A Streamlit browser dashboard that wraps all six scripts into a single UI.
No duplication — it imports directly from the other scripts, so any update
to the analysis or scoring logic is immediately reflected in the UI.

**Install and run:**

```bash
pip install streamlit plotly
streamlit run stock_ui.py
# opens at http://localhost:8501
```

**Four tabs:**

| Tab | What it does |
|---|---|
| 🏆 Score | Ranks symbols by investment score for any horizon (week/month/quarter/year/life). Bar chart, metrics table, per-symbol breakdown, suggested pair. |
| 📊 Analysis | Interactive charts: price, RSI, Bollinger Bands, drawdown, Monte Carlo, summary stats. Symbol and tool selectable from the sidebar. |
| 🔁 Backtest | All four strategies with parameter sliders. Equity curve vs buy-and-hold, performance metrics, trade log. |
| 🔔 Alerts | Evaluate conditions against live data. Results table with TRUE/false badges, full indicator snapshot per symbol. |

The sidebar controls which symbols and date range are active across all tabs.
Data is cached for 5 minutes — run `stock_collector.py` to refresh.

---

### Rank symbols before investing

```bash
python3 stock_score.py \
    -s AAPL MSFT GOOGL CSMIB.MI TSLA ENEL.MI \
    --from 2023-01-01 \
    --horizon quarter \
    --top 3 \
    --detail
```

### Backtest a strategy before using it

```bash
python3 stock_backtest.py -s AAPL \
    --strategy rsi --window 14 \
    --from 2018-01-01 --test-from 2023-01-01 \
    --plot
```

### Set up daily alerts

```bash
# add to crontab — every 30 min during market hours
*/30 9-17 * * 1-5  python3 /path/to/stock_alerts.py \
    -s AAPL MSFT GOOGL CSMIB.MI \
    --when "rsi14 < 30" \
    --when "change_pct < -3" \
    --notify email
```

### Starting from scratch

```bash
# 1. Edit SYMBOLS and API_KEYS in stock_collector.py
# 2. Run once to test
python3 stock_collector.py
# 3. Check what was collected
python3 stock_inventory.py --summary
# 4. Set up cron for ongoing collection
crontab -e
# add: */30 * * * * /usr/bin/python3 /path/to/stock_collector.py
```

### Backfilling historical data

```bash
# get everything available for your symbols
python3 stock_collector.py --historical ALL

# or a specific range
python3 stock_collector.py --historical 2015-2023

# check the result
python3 stock_inventory.py --summary
```

### Quick daily overview

```bash
python3 stock_analysis.py -s AAPL MSFT GOOGL --analysis summary
```

### Trend analysis with plot

```bash
python3 stock_analysis.py -s AAPL \
    --from 2020-01-01 \
    --analysis regression sma \
    --sma-windows 50 200 \
    --plot
```

### Risk snapshot

```bash
python3 stock_analysis.py -s AAPL MSFT \
    --analysis volatility drawdown \
    --window 20 \
    --plot
```

### Technical indicator dashboard

```bash
python3 stock_analysis.py -s AAPL \
    --analysis rsi bbands sma \
    --window 14 \
    --sma-windows 20 50 \
    --plot
```

### Portfolio correlation

```bash
python3 stock_analysis.py -s AAPL MSFT GOOGL AMZN TSLA NVDA \
    --from 2022-01-01 \
    --analysis correlation \
    --plot
```

### Forward simulation

```bash
python3 stock_analysis.py -s AAPL \
    --analysis montecarlo \
    --mc-paths 5000 \
    --mc-horizon 126 \
    --plot
```

### Intraday analysis

```bash
# collect hourly data first (yfinance, Finnhub paid, Twelve Data)
python3 stock_collector.py -s AAPL

# then analyse at 2-hour granularity
python3 stock_analysis.py -s AAPL \
    --interval 1h \
    --granularity 2h \
    --analysis summary volatility rsi \
    --window 12 \
    --plot
```

### Export for sharing

```bash
# export weekly OHLCV for the last 3 years
python3 stock_analysis.py -s AAPL MSFT \
    --from 2022-01-01 \
    --granularity 1w \
    --save weekly_data.csv

# raw export via sqlite3
sqlite3 stock_data.db ".mode csv" ".headers on" \
    "SELECT * FROM prices ORDER BY symbol, data_date" \
    > full_export.csv
```

### Loading data in Python

```python
import sqlite3
import pandas as pd

# load from the live DB
con = sqlite3.connect("stock_data.db")
df  = pd.read_sql(
    "SELECT * FROM prices WHERE symbol='AAPL' AND interval='1d' ORDER BY data_date",
    con
)
con.close()

# load from a historical DB
con2 = sqlite3.connect("data/stock_data_2020-2023.db")
df2  = pd.read_sql("SELECT * FROM prices WHERE symbol='AAPL'", con2)
con2.close()
```

---

## Git and data files

**Do not commit database files or generated outputs.** Add this `.gitignore`:

```gitignore
# databases
*.db
*.db-shm
*.db-wal

# CSV exports and logs
stock_data.csv
collector.log
.collector_state.json
.alerts_state.json

# config — contains API keys
config.env

# generated plot output
gnuplot-data/
matplot/
data/

# Python
__pycache__/
*.pyc
.env
```

**What to commit:**

```
stock_collector.py   ✓
stock_analysis.py    ✓
stock_inventory.py   ✓
stock_score.py       ✓
stock_backtest.py    ✓
stock_alerts.py      ✓
stock_ui.py          ✓
test_toolkit.py      ✓
test_live_apis.py    ✓
make_dist.py         ✓
crontab.demo         ✓
README.md            ✓
ANALYSIS.md          ✓
README_SCORE.md      ✓
README_BACKTEST.md   ✓
README_ALERTS.md     ✓
requirements.txt     ✓
.gitignore           ✓
```

Generate `requirements.txt` with:

```bash
pip freeze | grep -E "requests|yfinance|pandas|scipy|matplotlib" > requirements.txt
```

---

## Testing

Two test files cover the toolkit at different levels.

### Offline tests — `test_toolkit.py`

Fully self-contained. Creates a synthetic SQLite fixture database with seeded
OHLCV data for four symbols and runs everything against it. Zero API calls,
zero external dependencies beyond the toolkit itself. Completes in ~3 seconds.

```bash
# standard runner
python3 test_toolkit.py

# pytest (if installed)
python3 -m pytest test_toolkit.py -v --tb=short

# run a single class
python3 -m pytest test_toolkit.py::TestBacktest -v
```

**What is covered:**

| Class | What it tests |
|---|---|
| `TestCollectorConfig` | `config.env` parser — inline comments, quoted values, missing file |
| `TestCollectorDedup` | `_live_has_today` and `_hist_has_data` — hit, miss, wrong symbol, future range |
| `TestScoreSteps` | All seven step functions, all five horizon profiles, penalty logic |
| `TestBacktest` | All four strategies, equity length, no-lookahead, commission effect |
| `TestAlerts` | `build_context`, `evaluate_condition`, edge detection, state persistence |
| `TestPipeline` | End-to-end: score → backtest → alert on the same data; determinism check |

Expected output:
```
Ran 55 tests in 2.9s
55/55 passed  ✓ all green
```

---

### Live API tests — `test_live_apis.py`

Hits the real API endpoints. Skipped entirely unless `RUN_LIVE=1` is set,
so the main suite stays fast and offline at all times.

```bash
RUN_LIVE=1 python3 test_live_apis.py
```

**Cost per run:**

| Source | Endpoint | Quota cost |
|---|---|---|
| yfinance | `Ticker.fast_info` + 5-day history | 0 (no key) |
| Alpha Vantage | `GLOBAL_QUOTE` with public `demo` key (IBM only) | 0 of your 25/day |
| FMP | Real-key auth check only (demo key revoked in 2025) | 0–1 of your 250/day |
| Finnhub | `/quote?symbol=AAPL` | 1 of 60/min |
| Polygon | `/v2/aggs` single day | 1 of 5/min |
| Twelve Data | `/quote?symbol=AAPL` | 1 of 800/day |
| Marketstack | `/tickers/AAPL` metadata | 1 of 100/month |

Sources without a key in `config.env` are skipped automatically with a clear
message. The connectivity tests (can we reach each domain?) always run first.

Expected output with Finnhub + Alpha Vantage configured:
```
14 passed  |  6 skipped  |  0 failed  ✓ all green
```

---

## Troubleshooting

### `[alphavantage] TSLA: Thank you for using Alpha Vantage! This is a premium endpoint`

`TIME_SERIES_DAILY_ADJUSTED` (adjusted closes) is a paid feature. Set
`ALPHAVANTAGE_PAID = False` (the default) to use `TIME_SERIES_DAILY` instead.
Set `ALPHAVANTAGE_PAID = True` if you have a paid plan.

### `[alphavantage] TSLA: Thank you for using Alpha Vantage! The outputsize=full parameter value is a premium feature`

Historical full-range data from Alpha Vantage requires a paid plan. On the free
tier the script automatically falls back to `compact` (~100 days). Use yfinance
or FMP for free long-range historical data.

### `[finnhub] TSLA: 403 Client Error: Forbidden`

`/stock/candle` (OHLCV bars) requires a paid Finnhub plan. Set
`FINNHUB_PAID = False` (the default) to use the free `/quote` endpoint only.
Set `FINNHUB_PAID = True` if you have a paid plan.

### `[alphavantage] daily budget exhausted (25/25), skipping`

The 25 calls/day free limit has been reached. The counter resets at midnight.
Reduce the number of symbols in `SYMBOLS` to stay within the daily budget
(each symbol = 1 call).

### `No database files found`

Run `stock_collector.py` at least once to create `stock_data.db`. The analysis
and inventory scripts read from existing databases — they do not create them.

### `[error] No 1h bars found`

Hourly bars are only collected by yfinance (last 5 days) and Finnhub paid tier.
Run `stock_collector.py` first, then check `stock_inventory.py` to confirm
hourly data exists before using `--interval 1h`.

### `yfinance` data is missing or inconsistent

yfinance is an unofficial web scraper and can be rate-limited or blocked. It
is suitable for personal use and prototyping but not for production. For
reliable data, use FMP, Alpha Vantage, or Twelve Data with proper API keys.
