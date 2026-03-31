# Quick Start — Installed from distribution package

Three steps from unpack to running dashboard.

---

## 1. Unpack

The package creates its own directory when unpacked — no need to create one first.

```bash
tar xzf stock-toolkit.tar.gz   # creates stock-toolkit/ directory
cd stock-toolkit/
```

Or if you received a zip:
```bash
unzip stock-toolkit.zip         # creates stock-toolkit/ directory
cd stock-toolkit/
```

---

## 2. Install

```bash
bash install.sh
```

The installer will:
- Check your Python version (3.10+ required — see note below)
- Create a virtual environment in `.venv/`
- Install all dependencies including Streamlit
- Set up the `bin/` command wrappers
- Walk you through API key configuration (all optional — yfinance works without any key)
- Download full historical data for your symbols (recommended — takes ~1 minute)

> **macOS note:** The stock Python that ships with macOS is too old (3.9 or earlier).
> If the installer complains about the Python version, install a modern one first:
> ```bash
> # Option A — Homebrew (recommended)
> brew install python@3.12
>
> # Option B — download directly
> # https://www.python.org/downloads/
> ```
> Then re-run `bash install.sh`.

---

## 3. Start the dashboard

```bash
./startUI.sh
```

Your browser opens automatically at `http://localhost:8501`.

---

## What's in the dashboard

| Tab | What it does |
|---|---|
| 🏆 Score | Ranks your symbols by investment score across 5 time horizons |
| 📊 Analysis | 11 analytical tools: RSI, Bollinger Bands, drawdown, Monte Carlo, etc. |
| 🔁 Backtest | Tests 4 trading strategies against your historical data |
| 🔔 Alerts | Monitors conditions and sends notifications |
| 🤖 Briefing | Claude AI interprets the numbers in plain English (API key required) |
| 📥 Collect | Fetch fresh data on demand, add new symbols |

---

## Keeping data fresh

The toolkit is designed to run on a schedule. Add this to your crontab
(`crontab -e`) to collect data automatically:

```bash
# collect every 30 minutes on weekdays
*/30 7-22 * * 1-5  /path/to/stock-toolkit/bin/collect --sources yfinance
```

See `crontab.demo` for a full tiered schedule using all API sources.

---

## Adding a symbol

Open the **📥 Collect** tab in the dashboard, type the ticker, and click Run.
Once collected, the symbol is automatically included in all future cron runs.

Or from the command line:
```bash
bin/collect -s NVDA --sources yfinance --historical ALL
```

---

## Useful commands

All commands live in the `bin/` folder. Add it to your PATH or use full paths:

```bash
bin/inventory --summary          # what's in the database
bin/inventory --check            # gap detection
bin/score --horizon quarter      # rank your watchlist
bin/collect --sources yfinance   # fetch latest data
```

---

## Next steps

- Edit `config.env` to add API keys for more data sources
- See `README.md` for complete documentation of every flag and feature
- See `QUICKSTART_DEV.md` for command-line usage without the UI
