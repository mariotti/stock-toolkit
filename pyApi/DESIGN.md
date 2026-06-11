# Design Notes — Stock Toolkit

## The Idea

What I knew going in:

- I needed data first. Without it you cannot do anything.
- I needed local data so that any analysis can be done offline and does not depend on external, eventually paid, APIs.

This is why the first question to Claude was about free API availability. Paid might work but you need to choose the right low-cost provider that matches what you want to do — and understand the limits before you build around them.

Then comes a bit of software design discipline.

## Architecture from the Start

I already had these steps in mind before writing a single line:

1. **Download data** — later called the collector
2. **Run analysis tools** — offline, on local data
3. **Represent data in a UI** — initially just graphs

What came later:

4. **Make the UI interactive** — selectable symbols, horizons, tools
5. **Tools to manage the actual data** — inventory, gap detection, cleanup
6. **Deployment** — how to run it somewhere beyond my laptop

As a good developer I added tests throughout. The AI helped keep them green.

## Why This Order Matters

I think that if you asked an AI to build a full UI with all the features we ended up with from day one, it would be very hard to control what is going on. You would get something that looks complete but falls apart under the surface.

I needed to control my steps. Each piece was working and tested before the next one was added.

## What the AI Actually Did

### Free API research
It would have taken days to read all the free API documentation and build the interfaces. Claude checked coverage, limits, and authentication for seven APIs in one session. In addition it tracked all the different rate limits and free tier restrictions — some of which changed mid-project (FMP dropped v3, Polygon became Massive, Marketstack moved to v2).

### CSV → SQLite switch
I went from CSV file downloads to a proper SQLite database with a connection manager, deduplication, and multi-DB support in a single session. This is weeks of coding without AI, not days.

### Live debugging
I did not have to debug API errors myself. I passed error messages and log output directly to Claude and it identified the root cause and fixed it. Examples: FMP 402 errors, Twelve Data rate limit crashes (`TypeError: int is not iterable`), Marketstack 406 errors, the double `main()` bug in stock_toolkit/inventory.py.

### Iteration speed
Features that would normally require a design session, implementation, debugging cycle, and test writing happened in single conversations. The scoring system (7-step, 5 horizons), the backtest engine, the Streamlit UI with 6 tabs — all built incrementally without losing what came before.

## Main Steps Taken

### Phase 1 — Data
- Collector with 7 API sources (yfinance, Alpha Vantage, Finnhub, Massive/Polygon, FMP, Twelve Data, Marketstack)
- SQLite backend with deduplication and per-source rate limiting
- Tiered cron scheduling (real-time, hourly, daily sources run at different frequencies)
- Historical data collection with date range support
- Symbol auto-discovery from DB (collect once → keep collecting forever)

### Phase 2 — Analysis
- 11 analysis tools (summary, RSI, Bollinger Bands, Monte Carlo, drawdown, regression, correlation, etc.)
- 5-horizon scoring system (week / month / quarter / year / life) with weighted metrics
- 4 backtest strategies with commission modelling
- Alert system with edge detection and multi-channel notification

### Phase 3 — UI
- Streamlit dashboard with 6 tabs: Score, Analysis, Backtest, Alerts, Briefing, Collect
- Briefing tab: full 7-step analysis passed to Claude API, multi-turn chat, prompt inspector
- Collect tab: on-demand collection from UI, gated by `UI_COLLECT_SOURCES` in config

### Phase 4 — Data quality
- `stock_toolkit/inventory.py` with `--remove` and `--check` flags
- Gap detection with per-symbol trading calendar (75% threshold)
- `quote` interval merged into `1d` so all tools see today's data
- `SYMBOLS_IGNORE` to block ghost symbols (bare EU tickers like ENI vs ENI.MI)
- Minimum bar threshold in `_symbols_from_db()` to filter near-empty symbols

### Phase 5 — Distribution and ops
- `make_dist.py` with personal path scrubbing
- Shell wrappers in `~/bin` for all tools
- `crontab.demo` with tiered scheduling
- `config.env` with all keys, limits, and flags documented

## Lessons

**Free APIs are messier than they look.** Every one of them changed something during the project — endpoints moved, auth requirements changed, rate limits tightened. The collector needed updating multiple times just to keep working.

**The dot heuristic was a mistake.** We tried to be clever about EU symbols by detecting `.MI`, `.DE` suffixes and routing them differently. It caused more problems than it solved. The right answer was simpler: let APIs return "symbol not found", log it clearly, and let the user decide what to add.

**Persistence beats intelligence.** The `_symbols_from_db()` feature — once collected, always collected — is one of the most useful things in the toolkit. Simple idea, works perfectly.

**The briefing tab depends on a paid API.** The Anthropic API free tier exists but requires account activation. At ~$0.01 per briefing on Sonnet, $5 of credits lasts a long time.
