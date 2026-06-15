# Getting started

From zero to a running dashboard in 5–10 minutes. Three install paths,
same toolkit — pick whichever fits.

---

## Pick your install path

| Path | Best for | Needs |
|---|---|---|
| [**A. Docker**](#path-a--docker) | Production-style use, NAS, "just give me a stack" | Docker Desktop |
| [**B. Native Python**](#path-b--native-python) | Development, scripting, you already have Python | Python 3.10+ |
| [**C. Distribution package**](#path-c--distribution-package) | A pre-bundled tarball with one install script | Python 3.10+ on host |

---

### Path A — Docker

Dashboard + scheduled collector in one self-contained stack.

```bash
git clone https://gitlab.com/Mariotti/stock-toolkit.git
cd stock-toolkit                                 # repo root, NOT pyApi/
mkdir -p data                                    # host directory for state
docker compose run --rm ui stock-setup           # interactive config wizard
docker compose run --rm ui stock-bootstrap       # seed years of history
docker compose up -d                             # dashboard + collector
open http://localhost:8501
```

Your `config.env`, the SQLite database, and the logs all live in
`./data/` on the host. The image itself is throwaway. See
[`docker/README.md`](../docker/README.md) for operations.

---

### Path B — Native Python

```bash
git clone https://gitlab.com/Mariotti/stock-toolkit.git
cd stock-toolkit/pyApi
python3 -m venv venv && source venv/bin/activate
pip install -e .                                 # installs stock-* commands
stock-setup                                      # interactive config wizard
stock-bootstrap                                  # seed years of history
stock-ui                                         # dashboard opens in browser
```

> **macOS note.** The Python that ships with macOS is too old (3.9).
> Install a modern one: `brew install python@3.12`, or download from
> [python.org](https://www.python.org/downloads/), then retry.

---

### Path C — Distribution package

```bash
# Grab stock-toolkit-X.Y.Z.tar.gz from the Releases page
tar xzf stock-toolkit-0.3.2.tar.gz && cd toolkit
bash install.sh                                  # venv + setup wizard + bootstrap
./startUI.sh                                     # dashboard opens in browser
```

The installer handles everything — venv, deps, the `bin/` wrappers,
config, and an initial historical seed. About 2 minutes end-to-end.

---

## Step-by-step (what each command does)

### 1. Configure — `stock-setup`

The wizard walks you through:

- **Your watchlist** (e.g. `SYMBOLS=AAPL,MSFT,ENEL.MI,DOCM.SW`) —
  comma-separated, exchange suffixes for non-US tickers
- **API keys**, all optional — yfinance works with none. Get the
  others for free at their respective sign-up pages; the wizard shows
  the URL for each one
- **Notification channels** (email, Pushover, Slack) — only if you
  plan to use the Alerts tab
- **`ANTHROPIC_API_KEY`** — only if you want the AI-powered Briefing tab

Output: a `config.env` file in your data directory. You can edit it by
hand any time.

### 2. Seed historical data — `stock-bootstrap`

```
[stock-bootstrap] Backfilling historical OHLCV via yfinance (range: ALL).
                  Output → data/stock_data_<range>.db (live untouched).

  [hist/yfinance] AAPL: 11023 bars
  [hist/yfinance] MSFT:  9876 bars
  [hist/yfinance] ENEL.MI: 5614 bars
  ...
```

About 1–2 minutes for a typical 10–20 symbol watchlist with 20+ years
of daily bars. No API key, no rate budget — `yfinance` only. Re-runs
skip what's already in the DB.

### 3. Open the dashboard — `stock-ui`

Browser opens at `http://localhost:8501`. Six analytical tabs along
the top, plus an **⚙️ Admin** page in the sidebar nav for operations:

| Tab | What you'll see |
|---|---|
| **🏆 Score** | Pick a horizon (Week / Month / Quarter / Year / Life), click "Run scoring" → a ranked table of your watchlist scored 0–100 across 9 components (Sharpe, Calmar, R², trend, RSI, %B, MC probability, momentum, Hurst persistence), with a bar chart and per-symbol score breakdowns |
| **📊 Analysis** | Pick a symbol → returns/volatility/RSI/Bollinger/Monte Carlo charts side by side. Multi-symbol mode shows price-normalized overlay + correlation heatmap |
| **🔁 Backtest** | Pick a symbol and a strategy (RSI / SMA cross / Bollinger / breakout), click "Run backtest" → equity curve vs buy-and-hold, drawdown chart, trade log |
| **🔔 Alerts** | Configure conditions (RSI < 30, drawdown > 20%, …) and notification channel. Edge-triggered: fires once on False→True transition |
| **🤖 Briefing** | Click "Generate today's briefing" → Claude analyses your scores + fundamentals + indicators and writes a plain-English summary. Then ask follow-up questions in chat — context is prompt-cached so follow-ups are cheap. After Claude responds, two action panels appear: a **🤖 Claude-driven Briefing strategy** ("Ask Claude to propose trades" — Claude returns 0-3 structured proposals you confirm or skip; first confirmation auto-creates a dedicated `Briefing strategy` portfolio) and an inline **🎮 manual paper-trade panel** for the currently active Game strategy |
| **📥 Collect** | One-click data refresh for the current sidebar selection |
| **⚙️ Admin** *(sidebar)* | Edit your watchlist (SYMBOLS / SYMBOLS_IGNORE) and save back to `config.env`; trigger a scheduled collection tier, a historical `stock-bootstrap`, or `stock-gap-fill` to plug missed days; view inventory summary and gap-check; see the failure-tracker's suppressed (symbol, source) pairs |
| **🎮 Game** *(sidebar)* | Paper-trading portfolios (multiple "strategies" in parallel): start with virtual cash, buy fractional shares of any symbol with collected data at the latest close (+0.1% slippage), check back tomorrow / next week / next month to see how it played out. Switch between strategies in the top-of-page selector (each one's current return % is shown inline so you can pick the winner at a glance); create new ones via the "+ New strategy" expander; per-strategy rename / reset / archive / delete in Settings. The value chart overlays a dotted **equal-weight buy-and-hold of your watchlist** so you can tell whether the strategy actually beats sitting still. With two or more strategies an additional **📈 Compare strategies** expander overlays every portfolio's value curve on one chart for side-by-side comparison. State lives in `portfolio.db` |

---

## Step 4 — Keep data fresh (scheduling)

The toolkit needs to collect daily to stay useful. Pick the scheduler
that matches your install path:

### Docker

Already done. The `collector` service runs supercronic on the same
tiered schedule as the launchd plists and `crontab.demo`:

- 08:00 UTC weekdays — yfinance only (overnight pickup)
- 13:00 UTC weekdays — yfinance + Finnhub (midday quotes)
- 23:30 UTC weekdays — full sweep across all configured sources
- 00:30 UTC Sunday — `VACUUM / ANALYZE` housekeeping

### macOS native (launchd)

For native installs on Mac use **launchd plists** in
`~/Library/LaunchAgents/`. The same four tiered jobs as the Docker
scheduler — see `crontab.demo` for the canonical schedule and adapt
to plist XML, or copy the working set documented in this repo's
operational notes.

### Linux / NAS native (cron)

```bash
crontab -e
# add (paths to your venv):
0 8  * * 1-5  /path/to/venv/bin/stock-collect --sources yfinance
0 13 * * 1-5  /path/to/venv/bin/stock-collect --sources yfinance finnhub
30 23 * * 1-5 /path/to/venv/bin/stock-collect --sources yfinance alphavantage polygon fmp twelvedata marketstack
```

See `crontab.demo` for the canonical version (including DB maintenance).

---

## Common workflows

### Daily tracking — "what changed today?"

```bash
stock-inventory --summary             # totals: rows, symbols, date range
stock-score --horizon quarter --top 5  # top 5 names right now
```

Or open the dashboard's **Score** tab and click Run.

### Investigate a single stock before buying

```bash
stock-bootstrap -s NVDA               # pull full history for NVDA
stock-analyse -s NVDA \
    --from 2022-01-01 \
    --analysis summary drawdown volatility rsi montecarlo \
    --plot
```

Or open the dashboard's **Analysis** tab, select NVDA, set the date
range. You get the same charts inline.

### Add a non-US ticker

Just add it to `SYMBOLS` with the exchange suffix:
`ENEL.MI` (Milan), `DOCM.SW` (Swiss SIX), `SAP.DE` (XETRA). The
collector auto-translates the suffix per source (Marketstack strips
the suffix, yfinance keeps it) — no extra config.

### Add a new API source key

Edit `config.env` (or re-run `stock-setup`), add the key, restart the
collector (Docker: `docker compose restart collector`; native:
nothing — your next `stock-collect` picks it up). The toolkit
auto-detects which sources are configured.

---

## Where to read next

- **[`README.md`](README.md)** — full reference: every module, flag, and tunable
- **[`README_SCORE.md`](README_SCORE.md)** — what each scoring component means and which horizon to pick
- **[`README_BACKTEST.md`](README_BACKTEST.md)** — the 4 strategies, signal generation, position sizing
- **[`README_ALERTS.md`](README_ALERTS.md)** — alert syntax, edge-trigger semantics, notification setup
- **[`ANALYSIS.md`](ANALYSIS.md)** — the 11 analysis tools in depth
- **[`../docker/README.md`](../docker/README.md)** — Docker operations: logs, manual runs, multi-arch builds

This is a data analysis and learning tool. Nothing here is financial
advice.
