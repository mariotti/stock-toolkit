"""In-app Help page — orients new users to the dashboard tabs and
sidebar pages, without trying to replace QUICKSTART.md.

Kept as a single ``render()`` function so it follows the same pattern
as ``admin.py`` and ``game.py`` and is exercised by AppTest in CI.
"""

import streamlit as st

from stock_toolkit.ui.icons import icon


def render() -> None:
    from stock_toolkit.ui.theme import setup_page
    setup_page("Stock Toolkit — Help")
    st.title(f"{icon('page.help')}  Help — using Stock Toolkit")
    st.caption(
        "First time here? Read top-to-bottom. Already comfortable? "
        "Jump to the section that matches what you want to do."
    )

    # ── orient new users ──────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### What this tool does")
    st.markdown(
        "Stock Toolkit pulls daily OHLCV data from up to **seven free APIs** "
        "(yfinance, Alpha Vantage, Finnhub, Polygon, FMP, Twelve Data, "
        "Marketstack), runs **eleven statistical analyses** on it, ranks "
        "your watchlist symbols with a **5-horizon score**, lets you "
        "**backtest strategies**, **alerts you** on conditions you care "
        "about, and offers a **paper-trading Game** so you can practise "
        f"ideas without risking real money. The {icon('tab.briefing')} "
        "**Briefing** tab brings "
        "Claude into the loop to interpret the numbers and propose trades."
    )
    st.info(
        "**This is a learning and analysis tool, not financial advice.** "
        "Paper trades are fake money. Real trading involves real risk — "
        "consult a qualified advisor before acting on anything you see "
        "here."
    )

    # ── first-time path ───────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Where to start")
    st.markdown(
        f"""
1. **Pick your symbols.** Open **{icon("page.admin")} Admin** (sidebar)
   → *Watchlist* and add tickers. Use exchange suffixes for non-US:
   `ENEL.MI` (Milan), `BMW.DE` (Frankfurt), `DOCM.SW` (Swiss).
2. **Collect some data.** From **{icon("page.admin")} Admin** →
   *Collect now* run `yfinance` (no key required). For years of
   history, run the CLI command `stock-bootstrap` on the host.
3. **Run a briefing.** Open the **{icon("tab.briefing")} Briefing**
   tab on the main page, click *Generate today's briefing*. Claude
   reads your scores and indicators and writes a plain-English
   summary. Add an `ANTHROPIC_API_KEY` in `config.env` first.
4. **Try the Game.** Open **{icon("page.game")} Game** (sidebar), buy
   a position, watch it across the next few sessions. The strategy
   comparison expander shows how every strategy is doing relative to
   a do-nothing watchlist baseline.
        """
    )

    # ── tab-by-tab guide ──────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Main page tabs")
    st.markdown(
        f"""
| Tab | What it does |
|---|---|
| **{icon("tab.score")} Score** | Ranks watchlist 0–100 across nine components (Sharpe, Calmar, momentum, drawdown, RSI, Bollinger Bands, Monte Carlo, Hurst persistence). Pick the **horizon** (week / month / quarter / year / life) to reweight the components for that timeframe. |
| **{icon("tab.analysis")} Analysis** | The eleven statistical tools, one chart per tool: returns histogram, regression, rolling volatility, correlation matrix, SMA crossovers, drawdown, RSI, Bollinger Bands, Monte Carlo paths, Hurst exponent. Useful for *understanding* a symbol, not picking one. |
| **{icon("tab.backtest")} Backtest** | Run RSI, SMA-cross, Bollinger Bands, breakout, or MACD on any symbol with commission + slippage. Compares against buy-and-hold so you can see whether the strategy actually adds value. |
| **{icon("tab.alerts")} Alerts** | Edge-triggered conditions (fires once on False → True): RSI cross, Bollinger squeeze, 52-week high/low, MACD cross, etc. Channels: email (SMTP), Pushover, Slack — configured in `config.env`. |
| **{icon("tab.briefing")} Briefing** | Claude reads your scores + fundamentals + indicators (optionally + Alpha Vantage news sentiment for the top-5 symbols, US-heavy coverage) and writes a plain-English summary you can chat with. The "Ask Claude to propose trades" button drops 0–3 confirmable trade cards into a dedicated *Briefing strategy* paper portfolio. |
| **{icon("tab.collect")} Collect** | Trigger on-demand collection from any enabled source. Faster than dropping to the CLI. |
        """
    )

    st.markdown("### Sidebar pages")
    st.markdown(
        f"""
| Page | What it does |
|---|---|
| **{icon("page.admin")} Admin** | Edit your watchlist, kick off collections, inspect the DB (sources, intervals, rows per symbol), see which `(symbol, source)` pairs are suppressed by the failure tracker. |
| **{icon("page.game")} Game** | Paper-trading portfolios. Multiple named "strategies" in parallel: each has its own cash, positions, return curve. Compare them side-by-side; track win rate, expectancy, CAGR, Sharpe, Sortino, max drawdown per strategy. The Briefing tab can feed trades here. |
| **{icon("page.help")} Help** | This page. |
        """
    )

    # ── concepts worth knowing ────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Concepts worth knowing")
    st.markdown(
        """
- **Horizon.** A symbol that looks great over a *week* might look
  awful over a *year*. The Score tab's horizon selector reweights the
  nine score components for the timeframe you care about.
- **Source priority.** When multiple APIs have data for the same
  symbol, the score / backtest engines pick **alphavantage > fmp >
  yfinance** > others. Override with `--source` on the CLI.
- **Slippage and commissions** are modelled in the Game and Backtest.
  Default is 10 bps (0.1 %) each side — realistic for retail brokers
  with marketable orders. Edit in code or pass flags on the CLI.
- **Paper-trade notes.** Every Game trade can carry a free-text note
  ("why I bought"). Claude's proposals auto-archive its reason as the
  note. The Outcome stats panel reads notes back so future-you can
  audit past-you.
- **Edge-triggered alerts.** Alerts fire **once** on a False → True
  transition, not every time the condition holds. State is stored in
  `.alerts_state.json` and survives restarts.
- **Where your data lives.** `pyApi/stock_data.db` for native
  installs, `./data/` next to the launcher for the Docker app
  bundle, `%APPDATA%\\stock-toolkit\\` for the Windows .exe.
        """
    )

    # ── deeper docs + escape hatch ────────────────────────────────────
    st.markdown("---")
    st.markdown("### Need more?")
    st.markdown(
        """
- **`QUICKSTART.md`** in the repo covers install paths, configuration,
  scheduling (cron / launchd / Docker).
- **`README.md`** is the full reference: every CLI flag, module, and
  workflow.
- **`README_SCORE.md`**, **`README_BACKTEST.md`**,
  **`README_ALERTS.md`**, **`ANALYSIS.md`** drill into each area.
- **Releases & changelog:**
  [gitlab.com/Mariotti/stock-toolkit/-/releases](https://gitlab.com/Mariotti/stock-toolkit/-/releases)
- **Source:**
  [gitlab.com/Mariotti/stock-toolkit](https://gitlab.com/Mariotti/stock-toolkit)
        """
    )
