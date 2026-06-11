# Quick Start — Developer / Command Line

Five minutes to your first results.

---

## 1. Install

```bash
pip install requests yfinance pandas scipy matplotlib
```

---

## 2. Configure

Copy `config.env` to the same folder as the scripts and fill in your symbols.
API keys are optional — `yfinance` works without any key.

```bash
# config.env
SYMBOLS=AAPL,MSFT,TSLA

ALPHAVANTAGE_KEY=
FINNHUB_KEY=
FMP_KEY=
TWELVEDATA_KEY=
```

---

## 3. Collect data

```bash
stock-collect
```

You should see something like:

```
INFO  Stock collector starting
INFO  Symbols: ['AAPL', 'MSFT', 'TSLA']
INFO  [yfinance] AAPL: 5 daily + 35 hourly bars
INFO  [yfinance] MSFT: 5 daily + 35 hourly bars
INFO  [yfinance] TSLA: 5 daily + 35 hourly bars
INFO  Fetched 300 rows | 300 new rows inserted into stock_data.db
```

---

## 4. Check what you have

```bash
stock-inventory --summary

# check for missing trading days or thin coverage
stock-inventory --check

# remove a symbol you no longer want
stock-inventory --remove TSLA
```

```
Symbol  Interval  From        To          Span  Rows  Sources
──────  ────────  ──────────  ──────────  ────  ────  ────────
AAPL    1d        2026-03-22  2026-03-28  6d     5    yfinance
AAPL    1h        2026-03-24  2026-03-28  4d    35    yfinance
...
```

---

## 5. Run your first analysis

```bash
stock-analyse -s AAPL MSFT TSLA --analysis summary
```

## 6. Score your symbols (which looks best right now?)

```bash
stock-score --from 2023-01-01 --horizon quarter --top 3
```

## 7. Launch the browser dashboard (optional)

```bash
pip install streamlit plotly
stock-ui
# opens at http://localhost:8501
```

---

## Use case 1 — track a handful of stocks daily

**Goal:** collect data automatically every 30 minutes and get a quick
daily overview with a trend line.

**Step 1 — set your symbols in `config.env`:**
```
SYMBOLS=AAPL,MSFT,TSLA,NVDA
```

**Step 2 — add a cron job:**
```bash
crontab -e
# add this line:
*/30 * * * * /usr/bin/python3 /path/to/stock_toolkit/collector/
```

**Step 3 — run the morning briefing:**
```bash
stock-analyse -s AAPL MSFT TSLA NVDA --analysis summary
```

**Step 4 — check the trend and moving averages with a plot:**
```bash
stock-analyse -s AAPL \
    --analysis regression sma \
    --sma-windows 20 50 \
    --plot
```

That's it. Each day you get an up-to-date database and can run the
analysis commands above whenever you want a snapshot.

---

## Use case 2 — investigate a single stock before buying

**Goal:** before deciding whether to buy a stock, get a full picture
of its historical behaviour, risk, and momentum.

**Step 1 — pull the full available history:**
```bash
stock-collect -s AAPL --historical ALL
```

**Step 2 — check what came back:**
```bash
stock-inventory -s AAPL --summary
```

**Step 3 — run a full analysis for the last 3 years:**
```bash
stock-analyse -s AAPL \
    --from 2022-01-01 \
    --analysis summary drawdown volatility rsi \
    --window 14 \
    --plot
```

This tells you:
- `summary` — total return and Sharpe ratio over the period
- `drawdown` — worst drop and how long recovery took
- `volatility` — how the riskiness has changed over time
- `rsi` — whether the stock is currently overbought or oversold

**Step 4 — simulate where the price might go:**
```bash
stock-analyse -s AAPL \
    --from 2022-01-01 \
    --analysis montecarlo \
    --mc-paths 2000 --mc-horizon 63 \
    --plot
```

This runs 2000 simulated price paths over the next 63 trading days
(≈ 1 quarter) and shows you the P5–P95 probability fan.

---

## Next steps

- Add more symbols to `config.env` and re-run the collector
- See `README.md` for all available flags
- See `QUICKSTART.md` for the simple 3-step install from the distribution package
- See `ANALYSIS.md` for a detailed explanation of every analysis tool
