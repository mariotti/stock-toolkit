"""
stock_analysis.py
=================
Reader and analysis tool for data collected by stock_collector.py.

Auto-discovers stock_data.db in the script directory and all *.db files
in the ./data/ subfolder, merges them, then runs the requested analyses.

Usage examples:
    python3 stock_analysis.py -s AAPL
    python3 stock_analysis.py -s AAPL MSFT --from 2020-01-01 --to 2023-12-31
    python3 stock_analysis.py -s AAPL --analysis regression --plot
    python3 stock_analysis.py -s AAPL MSFT --analysis correlation --plot
    python3 stock_analysis.py -s AAPL --granularity 1w --analysis returns --plot
    python3 stock_analysis.py -s AAPL --analysis volatility --window 30 --plot
    python3 stock_analysis.py -s AAPL MSFT --analysis summary regression sma --plot
    python3 stock_analysis.py -s AAPL --interval 1h --granularity 2h --analysis summary --plot
    python3 stock_analysis.py --list-symbols

Install dependencies:
    pip install pandas scipy matplotlib
"""

import argparse
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import numpy as np

# ─────────────────────────────────────────────
#  PATHS  (mirror stock_collector.py layout)
# ─────────────────────────────────────────────

BASE_DIR  = Path(__file__).parent
LIVE_DB   = BASE_DIR / "stock_data.db"
HIST_DIR  = BASE_DIR / "data"

# ─────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────

# Source preference when multiple APIs have the same (symbol, date).
# Earlier in the list = higher priority.
SOURCE_PRIORITY = [
    "alphavantage",   # exchange-licensed, adjusted closes
    "fmp",            # 30+ year history, rich fundamentals
    "yfinance",       # broad coverage, unofficial
    "finnhub",        # real-time strong
    "twelvedata",     # good multi-asset
    "polygon",        # US equities, limited free history
    "marketstack",    # wide exchange coverage
]

# pandas resample offset aliases (pandas ≥ 2.2 uses ME/QE/YE)
_GRAN_ALIAS = {
    "1h":  "1h",
    "2h":  "2h",
    "4h":  "4h",
    "1d":  "D",
    "1w":  "W-FRI",
    "1M":  "ME",
    "1Q":  "QE",
    "1Y":  "YE",
}

# For "auto" granularity: choose based on date-range span in days
_AUTO_GRAN = [
    (365,        "1d"),    # ≤ 1 year  → daily
    (365 * 5,    "1w"),    # ≤ 5 years → weekly
    (365 * 20,   "1M"),    # ≤ 20 years → monthly
    (float("inf"), "1Q"),  # > 20 years → quarterly
]

# For "auto" granularity when source interval is intraday (span in hours)
_AUTO_GRAN_INTRADAY = [
    (48,          "1h"),   # ≤ 2 days  → keep hourly
    (168,         "2h"),   # ≤ 1 week  → 2-hour bars
    (720,         "4h"),   # ≤ 30 days → 4-hour bars
    (float("inf"), "1d"),  # > 30 days → collapse to daily
]

VALID_ANALYSES = ["summary", "regression", "returns", "volatility", "correlation",
                  "sma", "drawdown", "rsi", "bbands", "montecarlo", "hurst"]
VALID_GRAN     = ["raw", "1h", "2h", "4h", "1d", "1w", "1M", "1Q", "1Y", "auto"]
VALID_INTERVAL = ["1d", "1h", "auto"]   # source-row filter

# Annualisation factors for Sharpe / volatility
_ANN_FACTOR = {
    "1h":  252 * 6.5,    # ~1638 trading hours/year
    "2h":  252 * 3.25,
    "4h":  252 * 1.625,
    "1d":  252,
    "1w":  52,
    "1M":  12,
    "1Q":  4,
    "1Y":  1,
    "raw": 252,
    "auto": 252,
}

def ann_factor(gran: str) -> float:
    """Annualisation factor for Sharpe and volatility calculations."""
    return _ANN_FACTOR.get(gran, 252)

# ─────────────────────────────────────────────
#  DATA LOADING
# ─────────────────────────────────────────────

def discover_dbs() -> list[Path]:
    """Return all readable .db files: live DB + historical DBs in ./data/."""
    dbs = []
    if LIVE_DB.exists():
        dbs.append(LIVE_DB)
    if HIST_DIR.exists():
        dbs += sorted(HIST_DIR.glob("*.db"))
    if not dbs:
        print(f"[error] No database files found.\n"
              f"  Looked for: {LIVE_DB}\n"
              f"  And:        {HIST_DIR}/*.db\n"
              f"  Run stock_collector.py first to collect data.")
        sys.exit(1)
    return dbs


def load_raw(dbs: list[Path], symbols: list[str] | None = None) -> pd.DataFrame:
    """
    Load all rows from every DB, optionally filtered to the requested symbols.
    Columns: fetched_at, symbol, source, timestamp, interval,
             open, high, low, close, volume, vwap, change_pct, extra
    """
    frames = []
    for db in dbs:
        try:
            con = sqlite3.connect(db)
            where = ""
            params = []
            if symbols:
                placeholders = ",".join("?" * len(symbols))
                where  = f" WHERE symbol IN ({placeholders})"
                params = [s.upper() for s in symbols]
            df = pd.read_sql(
                f"SELECT * FROM prices{where} ORDER BY timestamp", con,
                params=params
            )
            con.close()
            if not df.empty:
                df["_db"] = db.name
                frames.append(df)
        except Exception as e:
            print(f"[warning] Could not read {db.name}: {e}")

    if not frames:
        syms = ", ".join(symbols) if symbols else "any symbol"
        print(f"[error] No data found for {syms}.")
        sys.exit(1)

    raw = pd.concat(frames, ignore_index=True)
    # format="mixed" handles both "2024-03-25" (daily) and
    # "2024-03-25T10:00:00+00:00" (intraday) in the same column
    raw["timestamp"] = pd.to_datetime(raw["timestamp"], format="mixed", utc=True, errors="coerce")
    for col in ["open", "high", "low", "close", "volume", "vwap", "change_pct"]:
        raw[col] = pd.to_numeric(raw[col], errors="coerce")
    return raw.dropna(subset=["timestamp", "close"])


def resolve_source(df: pd.DataFrame, preferred: str | None) -> pd.DataFrame:
    """
    When multiple sources report the same (symbol, timestamp), keep only one.

    If --source is given, filter to that source first.
    Otherwise use SOURCE_PRIORITY: for each (symbol, date) keep the
    highest-priority source that has data.
    """
    if preferred:
        filtered = df[df["source"] == preferred]
        if filtered.empty:
            available = sorted(df["source"].unique())
            print(f"[warning] Source '{preferred}' not found. "
                  f"Available: {available}.  Using all sources.")
        else:
            df = filtered

    # Build a priority rank; sources not in the list get lowest priority
    prio = {s: i for i, s in enumerate(SOURCE_PRIORITY)}
    df = df.copy()
    df["_prio"] = df["source"].map(lambda s: prio.get(s, len(SOURCE_PRIORITY)))
    df = df.sort_values("_prio")
    # Keep the highest-priority (lowest rank) row per (symbol, date, interval)
    df = df.drop_duplicates(subset=["symbol", "timestamp", "interval"], keep="first")
    return df.drop(columns=["_prio"])


def auto_granularity(df: pd.DataFrame, intraday: bool = False) -> str:
    """Choose a sensible granularity based on the actual date/time span."""
    if intraday:
        span_h = (df["timestamp"].max() - df["timestamp"].min()).total_seconds() / 3600
        for threshold, gran in _AUTO_GRAN_INTRADAY:
            if span_h <= threshold:
                return gran
        return "1d"
    span = (df["timestamp"].max() - df["timestamp"].min()).days
    for threshold, gran in _AUTO_GRAN:
        if span <= threshold:
            return gran
    return "1Q"


def apply_granularity(df: pd.DataFrame, gran: str, field: str = "close") -> pd.DataFrame:
    """
    Resample df to the requested granularity.

    For 'raw', returns the data as-is (daily bars, no resampling).
    For all others, resamples OHLCV to the period: open=first, high=max,
    low=min, close=last, volume=sum.  Missing periods are forward-filled.
    """
    if gran == "raw":
        return df.sort_values(["symbol", "timestamp"]).reset_index(drop=True)

    alias = _GRAN_ALIAS[gran]
    results = []

    for sym, grp in df.groupby("symbol"):
        grp = grp.set_index("timestamp").sort_index()
        # keep one source label (already resolved before this call)
        source = grp["source"].iloc[0] if "source" in grp.columns else "merged"
        ohlcv = grp[["open", "high", "low", "close", "volume"]].copy()

        try:
            resampled = ohlcv.resample(alias).agg({
                "open":   "first",
                "high":   "max",
                "low":    "min",
                "close":  "last",
                "volume": "sum",
            }).dropna(subset=["close"])
        except Exception:
            # fallback: older pandas offset alias names
            alias_old = {"ME": "M", "QE": "Q", "YE": "Y"}.get(alias, alias)
            resampled = ohlcv.resample(alias_old).agg({
                "open":   "first",
                "high":   "max",
                "low":    "min",
                "close":  "last",
                "volume": "sum",
            }).dropna(subset=["close"])

        resampled["symbol"] = sym
        resampled["source"] = source
        resampled["interval"] = gran
        results.append(resampled.reset_index().rename(columns={"index": "timestamp"}))

    if not results:
        return df
    return pd.concat(results, ignore_index=True)


def load_data(symbols: list[str] | None,
              date_from: str | None,
              date_to:   str | None,
              gran:      str,
              source:    str | None,
              interval:  str = "1d",
              field:     str = "close") -> tuple[pd.DataFrame, str]:
    """
    Full pipeline: discover → load → filter interval → filter dates
                   → resolve source → resample.
    Returns (DataFrame, effective_granularity) ready for analysis.

    interval: which source rows to load
      "1d"   — daily bars only (default)
      "1h"   — hourly bars only
      "auto" — hourly if available, else daily
    """
    dbs = discover_dbs()
    print(f"Loading from: {[d.name for d in dbs]}")

    df = load_raw(dbs, symbols)

    # ── interval filter ───────────────────────────────────────────────────
    if interval == "auto":
        # prefer hourly if any hourly rows exist, else fall back to daily
        has_hourly = df["interval"].isin(["1h"]).any()
        interval   = "1h" if has_hourly else "1d"
        print(f"Auto interval selected: {interval}")

    if interval == "1h":
        df = df[df["interval"] == "1h"]
        is_intraday = True
    else:
        df = df[df["interval"].isin(["1d", "1day"])]
        is_intraday = False

    if df.empty:
        print(f"[error] No {interval} bars found. "
              f"Available intervals: {sorted(load_raw(discover_dbs(), symbols)['interval'].unique())}")
        sys.exit(1)

    # ── date range filter ─────────────────────────────────────────────────
    if date_from:
        df = df[df["timestamp"] >= pd.Timestamp(date_from, tz="UTC")]
    if date_to:
        df = df[df["timestamp"] <= pd.Timestamp(date_to, tz="UTC")]

    if df.empty:
        print("[error] No data in the requested date range.")
        sys.exit(1)

    df = resolve_source(df, source)

    # ── granularity ───────────────────────────────────────────────────────
    # Validate that requested granularity makes sense for the interval
    if gran == "auto":
        effective_gran = auto_granularity(df, intraday=is_intraday)
        print(f"Auto granularity selected: {effective_gran}")
    elif is_intraday and gran in ("1w", "1M", "1Q", "1Y"):
        print(f"[warning] Granularity '{gran}' is coarser than daily — "
              f"switching to '1d' for intraday source data.")
        effective_gran = "1d"
    else:
        effective_gran = gran

    df = apply_granularity(df, effective_gran, field)

    span_str = (f"{df['timestamp'].min().strftime('%Y-%m-%d %H:%M')}" 
                f" → {df['timestamp'].max().strftime('%Y-%m-%d %H:%M')}")
    print(f"Loaded {len(df)} rows  |  "
          f"symbols: {sorted(df['symbol'].unique())}  |  "
          f"source interval: {interval}  |  "
          f"granularity: {effective_gran}  |  "
          f"range: {span_str}")
    return df, effective_gran


# ─────────────────────────────────────────────
#  CONSOLE OUTPUT HELPERS
# ─────────────────────────────────────────────

def _sep(title: str = "", width: int = 64):
    if title:
        pad = max(0, width - len(title) - 4)
        print(f"\n── {title} {'─' * pad}")
    else:
        print("─" * width)


def _tbl(rows: list[tuple], headers: list[str]):
    """Print a simple fixed-width table."""
    col_w = [len(h) for h in headers]
    for row in rows:
        for i, v in enumerate(row):
            col_w[i] = max(col_w[i], len(str(v)))
    fmt = "  ".join(f"{{:<{w}}}" for w in col_w)
    print(fmt.format(*headers))
    print("  ".join("─" * w for w in col_w))
    for row in rows:
        print(fmt.format(*[str(v) for v in row]))


# ─────────────────────────────────────────────
#  ANALYSIS FUNCTIONS
# ─────────────────────────────────────────────

def analysis_summary(df: pd.DataFrame, field: str, gran: str = "1d"):
    """Descriptive statistics + basic financial metrics per symbol."""
    _sep("SUMMARY")
    rows = []
    for sym, grp in df.groupby("symbol"):
        grp = grp.sort_values("timestamp")
        s = grp[field].dropna()
        if s.empty:
            continue
        rets = s.pct_change().dropna()
        af = ann_factor(gran)
        ann_vol = rets.std() * np.sqrt(af) * 100
        total_ret = (s.iloc[-1] / s.iloc[0] - 1) * 100 if len(s) > 1 else float("nan")
        sharpe = (rets.mean() / rets.std() * np.sqrt(af)) if rets.std() > 0 else float("nan")
        rows.append((
            sym,
            f"{s.iloc[0]:.2f}",
            f"{s.iloc[-1]:.2f}",
            f"{total_ret:+.1f}%",
            f"{s.min():.2f}",
            f"{s.max():.2f}",
            f"{s.mean():.2f}",
            f"{s.std():.2f}",
            f"{ann_vol:.1f}%",
            f"{sharpe:.2f}",
            str(len(s)),
        ))
    _tbl(rows, ["Symbol", "First", "Last", "Total ret", "Min", "Max",
                "Mean", "Std", "Ann.vol", "Sharpe", "Bars"])


def analysis_regression(df: pd.DataFrame, field: str, plot: bool):
    """
    Linear regression of field vs time per symbol.
    Reports: slope (units/day), annualised trend %, R², p-value.
    Optionally plots the series with regression line + 95% CI band.
    """
    try:
        from scipy import stats as sp_stats
    except ImportError:
        print("[error] scipy required:  pip install scipy")
        return

    _sep("LINEAR REGRESSION")
    rows = []
    plot_data = {}   # sym → (x_dates, y_values, slope, intercept, r2, ci_upper, ci_lower)

    for sym, grp in df.groupby("symbol"):
        grp = grp.sort_values("timestamp").dropna(subset=[field])
        if len(grp) < 3:
            continue
        # x = elapsed days from first bar
        x0 = grp["timestamp"].iloc[0]
        x  = (grp["timestamp"] - x0).dt.days.values.astype(float)
        y  = grp[field].values.astype(float)

        slope, intercept, r, p, se = sp_stats.linregress(x, y)

        # Annualised trend as % of starting price
        base_price = intercept if intercept > 0 else y[0]
        ann_pct = (slope * 252 / base_price) * 100 if base_price != 0 else float("nan")

        # 95% confidence band
        n      = len(x)
        t_crit = sp_stats.t.ppf(0.975, df=n - 2)
        y_hat  = intercept + slope * x
        mse    = np.sum((y - y_hat) ** 2) / (n - 2)
        x_bar  = x.mean()
        se_fit = np.sqrt(mse * (1/n + (x - x_bar)**2 / np.sum((x - x_bar)**2)))
        ci_u   = y_hat + t_crit * se_fit
        ci_l   = y_hat - t_crit * se_fit

        rows.append((
            sym,
            f"{slope:+.4f}/day",
            f"{ann_pct:+.1f}%/yr",
            f"{r**2:.4f}",
            f"{p:.4e}",
            "yes" if p < 0.05 else "no",
        ))

        plot_data[sym] = (grp["timestamp"], y, y_hat, ci_u, ci_l)

    _tbl(rows, ["Symbol", "Slope", "Ann.trend", "R²", "p-value", "Sig(5%)"])

    if plot and plot_data:
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates

        fig, ax = plt.subplots(figsize=(14, 6))
        colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
                  "#9467bd", "#8c564b", "#e377c2", "#7f7f7f"]
        for i, (sym, (dates, y, y_hat, ci_u, ci_l)) in enumerate(plot_data.items()):
            c = colors[i % len(colors)]
            ax.plot(dates, y, color=c, linewidth=1.2, alpha=0.8, label=f"{sym} price")
            ax.plot(dates, y_hat, color=c, linewidth=1.8, linestyle="--",
                    label=f"{sym} trend")
            ax.fill_between(dates, ci_l, ci_u, color=c, alpha=0.12,
                            label=f"{sym} 95% CI")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        fig.autofmt_xdate(rotation=35)
        ax.set_xlabel("Date")
        ax.set_ylabel(field.replace("_", " ").title())
        ax.set_title(f"Linear regression — {field}")
        ax.legend(fontsize=8, framealpha=0.75)
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.show()


def analysis_returns(df: pd.DataFrame, field: str, plot: bool, gran: str = "1d"):
    """
    Periodic returns: mean, std, min/max, Sharpe, and a return distribution.
    """
    _sep("RETURNS")
    rows = []
    hist_data = {}

    for sym, grp in df.groupby("symbol"):
        grp  = grp.sort_values("timestamp")
        rets = grp[field].pct_change().dropna() * 100   # in %
        if rets.empty:
            continue
        af = ann_factor(gran)
        sharpe = (rets.mean() / rets.std() * np.sqrt(af)) if rets.std() > 0 else float("nan")
        rows.append((
            sym,
            f"{rets.mean():+.3f}%",
            f"{rets.std():.3f}%",
            f"{rets.min():+.2f}%",
            f"{rets.max():+.2f}%",
            f"{(rets > 0).mean() * 100:.1f}%",
            f"{sharpe:.2f}",
        ))
        hist_data[sym] = rets

    _tbl(rows, ["Symbol", "Mean ret", "Std", "Worst", "Best", "% positive", "Sharpe"])

    if plot and hist_data:
        import matplotlib.pyplot as plt

        n   = len(hist_data)
        fig, axes = plt.subplots(1, n, figsize=(6 * n, 5), sharey=False)
        if n == 1:
            axes = [axes]
        for ax, (sym, rets) in zip(axes, hist_data.items()):
            ax.hist(rets, bins=50, edgecolor="white", linewidth=0.3, color="#1f77b4")
            ax.axvline(0, color="red", linewidth=1, linestyle="--")
            ax.axvline(rets.mean(), color="orange", linewidth=1.5,
                       label=f"mean {rets.mean():+.3f}%")
            ax.set_xlabel("Return (%)")
            ax.set_title(f"{sym} return distribution")
            ax.legend(fontsize=9)
            ax.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.show()


def analysis_volatility(df: pd.DataFrame, field: str, window: int, plot: bool, gran: str = "1d"):
    """
    Rolling annualised volatility (std of returns × √252).
    """
    _sep(f"ROLLING VOLATILITY  (window={window})")
    print(f"  Annualised, based on {field} returns\n")

    plot_data = {}
    for sym, grp in df.groupby("symbol"):
        grp  = grp.sort_values("timestamp")
        rets = grp[field].pct_change()
        vol  = rets.rolling(window).std() * np.sqrt(ann_factor(gran)) * 100
        latest = vol.dropna().iloc[-1] if not vol.dropna().empty else float("nan")
        mean_v = vol.dropna().mean()
        print(f"  {sym:10s}  latest={latest:.1f}%  mean={mean_v:.1f}%  "
              f"min={vol.min():.1f}%  max={vol.max():.1f}%")
        plot_data[sym] = (grp["timestamp"].values, vol.values)

    if plot and plot_data:
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates

        fig, ax = plt.subplots(figsize=(14, 5))
        colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"]
        for i, (sym, (dates, vol)) in enumerate(plot_data.items()):
            ax.plot(dates, vol, linewidth=1.3, color=colors[i % len(colors)],
                    label=sym)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        fig.autofmt_xdate(rotation=35)
        ax.set_ylabel(f"Ann. volatility % (window={window})")
        ax.set_title(f"Rolling volatility — {field}")
        ax.legend(fontsize=9)
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.show()


def analysis_correlation(df: pd.DataFrame, field: str, plot: bool):
    """
    Pearson correlation matrix of periodic returns between all requested symbols.
    """
    _sep("CORRELATION MATRIX")
    syms = sorted(df["symbol"].unique())
    if len(syms) < 2:
        print("  Need ≥ 2 symbols for correlation.  Use -s SYM1 SYM2 ...")
        return

    # Pivot to wide format: date × symbol
    wide = df.pivot_table(index="timestamp", columns="symbol", values=field)
    rets = wide.pct_change().dropna(how="all")
    corr = rets.corr()

    # Pretty print
    col_w = max(10, max(len(s) for s in syms))
    header = f"{'':>{col_w}}" + "".join(f"  {s:>{col_w}}" for s in syms)
    print(header)
    for sym in syms:
        row_str = f"{sym:>{col_w}}"
        for s2 in syms:
            val = corr.loc[sym, s2] if (sym in corr.index and s2 in corr.columns) else float("nan")
            row_str += f"  {val:>{col_w}.4f}"
        print(row_str)

    if plot:
        try:
            import matplotlib.pyplot as plt
        except ImportError:
            return

        fig, ax = plt.subplots(figsize=(max(6, len(syms)), max(5, len(syms) - 1)))
        im = ax.imshow(corr.values, vmin=-1, vmax=1, cmap="RdYlGn", aspect="auto")
        plt.colorbar(im, ax=ax, label="Pearson r")
        ax.set_xticks(range(len(syms)));  ax.set_xticklabels(syms, rotation=45, ha="right")
        ax.set_yticks(range(len(syms)));  ax.set_yticklabels(syms)
        for i in range(len(syms)):
            for j in range(len(syms)):
                val = corr.iloc[i, j]
                ax.text(j, i, f"{val:.2f}", ha="center", va="center",
                        fontsize=9, color="black" if abs(val) < 0.7 else "white")
        ax.set_title(f"Return correlation matrix — {field}")
        plt.tight_layout()
        plt.show()


def analysis_sma(df: pd.DataFrame, field: str, windows: list[int], plot: bool):
    """
    Simple moving averages overlay: plots the series with SMA lines.
    Prints the latest SMA values and whether price is above/below each.
    """
    _sep(f"MOVING AVERAGES  (windows={windows})")

    plot_data = {}
    for sym, grp in df.groupby("symbol"):
        grp  = grp.sort_values("timestamp").reset_index(drop=True)
        smas = {}
        rows = []
        for w in windows:
            col = f"SMA{w}"
            grp[col] = grp[field].rolling(w).mean()
            latest_sma   = grp[col].dropna().iloc[-1] if not grp[col].dropna().empty else float("nan")
            latest_price = grp[field].iloc[-1]
            direction    = "above ↑" if latest_price > latest_sma else "below ↓"
            rows.append((sym, w, f"{latest_sma:.2f}", f"{latest_price:.2f}", direction))
            smas[col] = grp[col]
        _tbl(rows, ["Symbol", "Window", "SMA", "Price", "Position"])
        plot_data[sym] = (grp["timestamp"], grp[field], smas)

    if plot and plot_data:
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates

        n    = len(plot_data)
        fig, axes = plt.subplots(n, 1, figsize=(14, 5 * n), sharex=False)
        if n == 1:
            axes = [axes]
        sma_colors = ["#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]
        for ax, (sym, (dates, prices, smas)) in zip(axes, plot_data.items()):
            ax.plot(dates, prices, color="#1f77b4", linewidth=1.2,
                    alpha=0.85, label=f"{sym} {field}")
            for j, (col, vals) in enumerate(smas.items()):
                ax.plot(dates, vals, linewidth=1.4, linestyle="--",
                        color=sma_colors[j % len(sma_colors)], label=col)
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator())
            fig.autofmt_xdate(rotation=35)
            ax.set_ylabel(field.replace("_", " ").title())
            ax.set_title(f"{sym} — moving averages")
            ax.legend(fontsize=9)
            ax.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.show()



# ─────────────────────────────────────────────
#  ANALYSIS — drawdown
# ─────────────────────────────────────────────

def analysis_drawdown(df: pd.DataFrame, field: str, plot: bool):
    """
    Drawdown analysis per symbol:
      - Max drawdown (peak-to-trough %)
      - Longest drawdown duration (bars)
      - Calmar ratio (annualised return / |max drawdown|)
      - Underwater chart when --plot is set
    """
    _sep("DRAWDOWN")
    rows = []
    plot_data = {}

    for sym, grp in df.groupby("symbol"):
        grp   = grp.sort_values("timestamp").reset_index(drop=True)
        price = grp[field].values.astype(float)
        dates = grp["timestamp"].values

        # Rolling maximum (high-water mark)
        hwm = np.maximum.accumulate(price)
        dd  = (price - hwm) / hwm * 100   # drawdown in %

        max_dd   = dd.min()
        max_dd_i = dd.argmin()

        # Find the peak before the trough
        peak_i = np.argmax(price[:max_dd_i + 1])

        # Duration of the max drawdown episode (peak → trough)
        dur = max_dd_i - peak_i

        # Recovery: first bar after trough where price >= peak price
        peak_price = price[peak_i]
        recovered  = np.where(price[max_dd_i:] >= peak_price)[0]
        rec_bars   = int(recovered[0]) if len(recovered) > 0 else None
        rec_str    = str(rec_bars) if rec_bars is not None else "not yet"

        # Calmar ratio: annualised return / |max drawdown|
        total_ret  = (price[-1] / price[0] - 1) if price[0] > 0 else float("nan")
        n_years    = (len(price) / 252) or 1
        ann_ret    = (1 + total_ret) ** (1 / n_years) - 1
        calmar     = ann_ret / abs(max_dd / 100) if max_dd != 0 else float("nan")

        rows.append((
            sym,
            f"{max_dd:.2f}%",
            str(dur) + " bars",
            rec_str + (" bars" if rec_bars is not None else ""),
            f"{ann_ret * 100:+.1f}%/yr",
            f"{calmar:.2f}",
        ))
        plot_data[sym] = (dates, dd)

    _tbl(rows, ["Symbol", "Max DD", "DD duration", "Recovery", "Ann. return", "Calmar"])

    if plot and plot_data:
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates

        n    = len(plot_data)
        fig, axes = plt.subplots(n, 1, figsize=(14, 4 * n), sharex=False)
        if n == 1:
            axes = [axes]
        colors = ["#d62728", "#ff7f0e", "#9467bd", "#8c564b"]
        for ax, (sym, (dates, dd)) in zip(axes, plot_data.items()):
            ax.fill_between(dates, dd, 0,
                            color=colors[list(plot_data).index(sym) % len(colors)],
                            alpha=0.35, label="Drawdown")
            ax.plot(dates, dd, linewidth=0.8,
                    color=colors[list(plot_data).index(sym) % len(colors)])
            ax.axhline(0, color="gray", linewidth=0.5, linestyle="--")
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator())
            fig.autofmt_xdate(rotation=35)
            ax.set_ylabel("Drawdown (%)")
            ax.set_title(f"{sym} — underwater chart")
            ax.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.show()


# ─────────────────────────────────────────────
#  ANALYSIS — RSI
# ─────────────────────────────────────────────

def analysis_rsi(df: pd.DataFrame, field: str, window: int, plot: bool):
    """
    Relative Strength Index (Wilder smoothing).
    Prints latest RSI and overbought/oversold signal per symbol.
    Plots price + RSI panel when --plot is set.
    """
    _sep(f"RSI  (window={window})")

    def _rsi(series: pd.Series, w: int) -> pd.Series:
        delta = series.diff()
        gain  = delta.clip(lower=0)
        loss  = (-delta).clip(lower=0)
        # Wilder smoothing = EMA with alpha = 1/w
        avg_gain = gain.ewm(alpha=1 / w, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / w, adjust=False).mean()
        rs  = avg_gain / avg_loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    rows = []
    plot_data = {}

    for sym, grp in df.groupby("symbol"):
        grp   = grp.sort_values("timestamp").reset_index(drop=True)
        rsi   = _rsi(grp[field], window)
        latest = rsi.dropna().iloc[-1] if not rsi.dropna().empty else float("nan")

        if latest >= 70:
            signal = "overbought ↑"
        elif latest <= 30:
            signal = "oversold ↓"
        else:
            signal = "neutral"

        # Count bars in overbought / oversold territory
        ob = int((rsi >= 70).sum())
        os = int((rsi <= 30).sum())

        rows.append((sym, f"{latest:.1f}", signal, str(ob), str(os)))
        plot_data[sym] = (grp["timestamp"].values, grp[field].values, rsi.values)

    _tbl(rows, ["Symbol", "RSI", "Signal", "Bars ≥70", "Bars ≤30"])

    if plot and plot_data:
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates

        n    = len(plot_data)
        fig, axes = plt.subplots(n, 2, figsize=(14, 4 * n),
                                 gridspec_kw={"height_ratios": [1] * n})
        # ensure axes is always 2D
        if n == 1:
            axes = np.array([axes])
        colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"]
        for i, (sym, (dates, prices, rsi)) in enumerate(plot_data.items()):
            c = colors[i % len(colors)]
            ax_p, ax_r = axes[i]

            ax_p.plot(dates, prices, color=c, linewidth=1.2)
            ax_p.set_title(f"{sym} — price")
            ax_p.grid(True, alpha=0.3)
            ax_p.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
            ax_p.xaxis.set_major_locator(mdates.AutoDateLocator())

            ax_r.plot(dates, rsi, color=c, linewidth=1.2)
            ax_r.axhline(70, color="red",   linewidth=0.8, linestyle="--", alpha=0.7)
            ax_r.axhline(30, color="green", linewidth=0.8, linestyle="--", alpha=0.7)
            ax_r.fill_between(dates, rsi, 70,
                              where=np.array(rsi) >= 70, color="red",   alpha=0.15)
            ax_r.fill_between(dates, rsi, 30,
                              where=np.array(rsi) <= 30, color="green", alpha=0.15)
            ax_r.set_ylim(0, 100)
            ax_r.set_title(f"{sym} — RSI({window})")
            ax_r.grid(True, alpha=0.3)
            ax_r.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
            ax_r.xaxis.set_major_locator(mdates.AutoDateLocator())

        fig.autofmt_xdate(rotation=35)
        plt.tight_layout()
        plt.show()


# ─────────────────────────────────────────────
#  ANALYSIS — Bollinger Bands
# ─────────────────────────────────────────────

def analysis_bbands(df: pd.DataFrame, field: str, window: int, plot: bool):
    """
    Bollinger Bands: SMA ± 2 standard deviations.
    Reports bandwidth, %B position, and squeeze signal per symbol.
    """
    _sep(f"BOLLINGER BANDS  (window={window}, ±2σ)")

    rows = []
    plot_data = {}

    for sym, grp in df.groupby("symbol"):
        grp    = grp.sort_values("timestamp").reset_index(drop=True)
        price  = grp[field]
        dates  = grp["timestamp"].values

        mid    = price.rolling(window).mean()
        std    = price.rolling(window).std()
        upper  = mid + 2 * std
        lower  = mid - 2 * std

        # %B: where is price within the bands? 0=lower, 1=upper
        pct_b  = (price - lower) / (upper - lower)

        # Bandwidth: (upper - lower) / mid  — squeeze = low bandwidth
        bw     = ((upper - lower) / mid * 100).dropna()
        latest_bw = bw.iloc[-1] if not bw.empty else float("nan")
        min_bw    = bw.min()

        # Squeeze: bandwidth near its 20-bar minimum
        squeeze = "yes" if latest_bw < bw.quantile(0.2) else "no"

        latest_pct_b = pct_b.dropna().iloc[-1] if not pct_b.dropna().empty else float("nan")
        latest_price = price.iloc[-1]
        latest_upper = upper.dropna().iloc[-1] if not upper.dropna().empty else float("nan")
        latest_lower = lower.dropna().iloc[-1] if not lower.dropna().empty else float("nan")

        rows.append((
            sym,
            f"{latest_lower:.2f}",
            f"{mid.dropna().iloc[-1]:.2f}" if not mid.dropna().empty else "—",
            f"{latest_upper:.2f}",
            f"{latest_pct_b:.2f}",
            f"{latest_bw:.1f}%",
            squeeze,
        ))
        plot_data[sym] = (dates, price.values, upper.values, mid.values, lower.values)

    _tbl(rows, ["Symbol", "Lower", "Mid (SMA)", "Upper", "%B", "Bandwidth", "Squeeze"])

    if plot and plot_data:
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates

        n    = len(plot_data)
        fig, axes = plt.subplots(n, 1, figsize=(14, 5 * n))
        if n == 1:
            axes = [axes]
        colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"]
        for ax, (sym, (dates, price, upper, mid, lower)) in zip(axes, plot_data.items()):
            c = colors[list(plot_data).index(sym) % len(colors)]
            ax.plot(dates, price, color=c, linewidth=1.2, label="Price")
            ax.plot(dates, mid,   color=c, linewidth=1.0, linestyle="--",
                    alpha=0.6, label=f"SMA{window}")
            ax.plot(dates, upper, color=c, linewidth=0.8, linestyle=":",
                    alpha=0.8, label="Upper band")
            ax.plot(dates, lower, color=c, linewidth=0.8, linestyle=":",
                    alpha=0.8, label="Lower band")
            ax.fill_between(dates, lower, upper, color=c, alpha=0.08)
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator())
            fig.autofmt_xdate(rotation=35)
            ax.set_ylabel(field.replace("_", " ").title())
            ax.set_title(f"{sym} — Bollinger Bands ({window}, ±2σ)")
            ax.legend(fontsize=9)
            ax.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.show()


# ─────────────────────────────────────────────
#  ANALYSIS — Monte Carlo
# ─────────────────────────────────────────────

def analysis_montecarlo(df: pd.DataFrame, field: str, n_paths: int,
                        horizon: int, plot: bool):
    """
    Geometric Brownian Motion Monte Carlo simulation.
    Parameters (μ, σ) are estimated from the historical returns in df.
    Prints percentile fan at the horizon.  Plots path fan when --plot is set.
    """
    _sep(f"MONTE CARLO  (paths={n_paths}, horizon={horizon} bars)")

    for sym, grp in df.groupby("symbol"):
        grp    = grp.sort_values("timestamp").reset_index(drop=True)
        price  = grp[field].dropna().values.astype(float)
        if len(price) < 10:
            print(f"  {sym}: not enough data")
            continue

        rets  = np.diff(np.log(price))
        mu    = rets.mean()
        sigma = rets.std()
        S0    = price[-1]

        # GBM: S(t+1) = S(t) × exp((μ - σ²/2) + σ × ε)
        rng   = np.random.default_rng(seed=42)
        eps   = rng.standard_normal((horizon, n_paths))
        daily = np.exp((mu - 0.5 * sigma ** 2) + sigma * eps)
        paths = S0 * np.cumprod(daily, axis=0)   # shape: (horizon, n_paths)

        final      = paths[-1]
        pcts       = np.percentile(final, [5, 25, 50, 75, 95])
        prob_up    = (final > S0).mean() * 100
        exp_return = (final.mean() / S0 - 1) * 100

        print(f"\n  {sym}  (S0={S0:.2f}, mu={mu*252:.1%}/yr, sigma={sigma*np.sqrt(252):.1%}/yr)")
        print(f"  After {horizon} bars:")
        print(f"    P5  = {pcts[0]:.2f}   P25 = {pcts[1]:.2f}   P50 = {pcts[2]:.2f}"
              f"   P75 = {pcts[3]:.2f}   P95 = {pcts[4]:.2f}")
        print(f"    Expected return: {exp_return:+.1f}%   Prob(price > S0): {prob_up:.1f}%")

        if plot:
            import matplotlib.pyplot as plt

            fig, axes = plt.subplots(1, 2, figsize=(14, 5))

            # Left: path fan
            ax = axes[0]
            idx = np.arange(1, horizon + 1)
            p5  = np.percentile(paths, 5,  axis=1)
            p25 = np.percentile(paths, 25, axis=1)
            p50 = np.percentile(paths, 50, axis=1)
            p75 = np.percentile(paths, 75, axis=1)
            p95 = np.percentile(paths, 95, axis=1)

            # Show a random sample of paths faintly
            sample = rng.choice(n_paths, min(80, n_paths), replace=False)
            for col in sample:
                ax.plot(idx, paths[:, col], color="#1f77b4",
                        alpha=0.04, linewidth=0.5)

            ax.fill_between(idx, p5,  p95, alpha=0.15, color="#1f77b4", label="P5–P95")
            ax.fill_between(idx, p25, p75, alpha=0.25, color="#1f77b4", label="P25–P75")
            ax.plot(idx, p50, color="#1f77b4", linewidth=1.8, label="Median")
            ax.axhline(S0, color="gray", linewidth=0.8, linestyle="--", label="Current")
            ax.set_xlabel("Bars forward")
            ax.set_ylabel("Price")
            ax.set_title(f"{sym} — GBM simulation ({n_paths} paths)")
            ax.legend(fontsize=9)
            ax.grid(True, alpha=0.3)

            # Right: final price distribution
            ax2 = axes[1]
            ax2.hist(final, bins=60, color="#1f77b4", edgecolor="white",
                     linewidth=0.3, alpha=0.8)
            for p, lbl, c in zip(pcts, ["P5","P25","P50","P75","P95"],
                                  ["#d62728","#ff7f0e","#2ca02c","#ff7f0e","#d62728"]):
                ax2.axvline(p, color=c, linewidth=1.2, linestyle="--", label=lbl)
            ax2.axvline(S0, color="black", linewidth=1.2, label="Current")
            ax2.set_xlabel("Final price")
            ax2.set_title(f"{sym} — terminal distribution")
            ax2.legend(fontsize=8)
            ax2.grid(True, alpha=0.3)

            plt.tight_layout()
            plt.show()


# ─────────────────────────────────────────────
#  ANALYSIS — Hurst exponent
# ─────────────────────────────────────────────

def analysis_hurst(df: pd.DataFrame, field: str, plot: bool):
    """
    Hurst exponent via R/S analysis.
      H < 0.5  → mean-reverting (anti-persistent)
      H ≈ 0.5  → random walk (no memory)
      H > 0.5  → trending (persistent)

    Uses lags from 10 to N/2 to fit log(R/S) ~ H × log(n).
    """
    _sep("HURST EXPONENT")

    def _hurst(ts: np.ndarray, min_lag: int = 10) -> tuple[float, np.ndarray, np.ndarray]:
        n      = len(ts)
        lags   = np.unique(np.logspace(np.log10(min_lag),
                                        np.log10(n // 2), 20).astype(int))
        rs_vals = []
        for lag in lags:
            segments = n // lag
            if segments < 2:
                continue
            rs_list = []
            for s in range(segments):
                seg    = ts[s * lag : (s + 1) * lag].astype(float)
                mean   = seg.mean()
                dev    = np.cumsum(seg - mean)
                r      = dev.max() - dev.min()
                std_s  = seg.std(ddof=1)
                if std_s > 0:
                    rs_list.append(r / std_s)
            if rs_list:
                rs_vals.append((lag, np.mean(rs_list)))

        if len(rs_vals) < 3:
            return float("nan"), np.array([]), np.array([])

        lags_fit = np.array([x[0] for x in rs_vals])
        rs_fit   = np.array([x[1] for x in rs_vals])
        log_l    = np.log(lags_fit)
        log_rs   = np.log(rs_fit)
        h, _     = np.polyfit(log_l, log_rs, 1)
        return h, lags_fit, rs_fit

    rows = []
    plot_data = {}

    for sym, grp in df.groupby("symbol"):
        grp   = grp.sort_values("timestamp").reset_index(drop=True)
        price = grp[field].dropna().values.astype(float)
        if len(price) < 40:
            print(f"  {sym}: not enough data (need ≥40 bars)")
            continue

        h, lags, rs = _hurst(price)
        if np.isnan(h):
            print(f"  {sym}: could not compute Hurst exponent")
            continue

        if h < 0.45:
            regime = "mean-reverting ↩"
        elif h > 0.55:
            regime = "trending ↗"
        else:
            regime = "random walk ↔"

        rows.append((sym, f"{h:.4f}", regime, str(len(price))))
        plot_data[sym] = (lags, rs, h)

    if rows:
        _tbl(rows, ["Symbol", "H", "Regime", "Bars used"])

    if plot and plot_data:
        import matplotlib.pyplot as plt

        n    = len(plot_data)
        fig, axes = plt.subplots(1, n, figsize=(6 * n, 5))
        if n == 1:
            axes = [axes]
        colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"]
        for ax, (sym, (lags, rs, h)) in zip(axes, plot_data.items()):
            c = colors[list(plot_data).index(sym) % len(colors)]
            ax.scatter(np.log(lags), np.log(rs), color=c, s=30, zorder=3)
            # Fitted line
            x_fit = np.array([np.log(lags[0]), np.log(lags[-1])])
            b     = np.log(rs).mean() - h * np.log(lags).mean()
            ax.plot(x_fit, h * x_fit + b, color=c, linewidth=1.5,
                    linestyle="--", label=f"H = {h:.4f}")
            ax.set_xlabel("log(lag)")
            ax.set_ylabel("log(R/S)")
            ax.set_title(f"{sym} — Hurst exponent")
            ax.legend(fontsize=9)
            ax.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.show()

# ─────────────────────────────────────────────
#  UTILITY: list available symbols
# ─────────────────────────────────────────────

def list_symbols():
    """Print all symbols found across all DBs."""
    dbs = discover_dbs()
    rows = []
    for db in dbs:
        try:
            con = sqlite3.connect(db)
            res = con.execute(
                "SELECT symbol, COUNT(*) as n, MIN(timestamp), MAX(timestamp) "
                "FROM prices WHERE interval='1d' "
                "GROUP BY symbol ORDER BY symbol"
            ).fetchall()
            con.close()
            for sym, n, d_min, d_max in res:
                rows.append((db.name, sym, str(n), d_min[:10], d_max[:10]))
        except Exception as e:
            print(f"[warning] {db.name}: {e}")
    if rows:
        _tbl(rows, ["Database", "Symbol", "Bars", "From", "To"])
    else:
        print("No data found.")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Analysis tool for stock data collected by stock_collector.py",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""
examples:
  python3 stock_analysis.py --list-symbols
  python3 stock_analysis.py -s AAPL
  python3 stock_analysis.py -s AAPL MSFT --from 2020-01-01 --to 2023-12-31
  python3 stock_analysis.py -s AAPL --analysis regression --plot
  python3 stock_analysis.py -s AAPL MSFT --analysis correlation --plot
  python3 stock_analysis.py -s AAPL --granularity 1w --analysis returns volatility --plot
  python3 stock_analysis.py -s AAPL --analysis sma --sma-windows 20 50 200 --plot
  python3 stock_analysis.py -s AAPL --interval 1h --granularity 2h --analysis summary --plot
  python3 stock_analysis.py -s AAPL --interval 1h --analysis volatility --window 12 --plot
  python3 stock_analysis.py -s AAPL --analysis drawdown --plot
  python3 stock_analysis.py -s AAPL --analysis rsi --window 14 --plot
  python3 stock_analysis.py -s AAPL --analysis bbands --window 20 --plot
  python3 stock_analysis.py -s AAPL --analysis montecarlo --mc-paths 2000 --mc-horizon 126 --plot
  python3 stock_analysis.py -s AAPL --analysis hurst --plot
        """
    )

    parser.add_argument("--list-symbols", action="store_true",
                        help="List all available symbols across all DBs and exit")

    parser.add_argument("-s", "--symbols", nargs="+", metavar="TICKER",
                        help="Symbols to analyse (e.g. -s AAPL MSFT GOOGL)")

    parser.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD",
                        help="Start of date range (inclusive)")
    parser.add_argument("--to",   dest="date_to",   metavar="YYYY-MM-DD",
                        help="End of date range (inclusive)")

    parser.add_argument("--interval", default="1d",
                        choices=VALID_INTERVAL,
                        help=(
                            "Which stored bar type to load. "
                            "'1d' = daily (default), "
                            "'1h' = intraday hourly, "
                            "'auto' = hourly if available, else daily."
                        ))

    parser.add_argument("--granularity", default="auto",
                        choices=VALID_GRAN,
                        help=(
                            "Time granularity for analysis. "
                            "'auto' picks based on data span: "
                            "daily→≤1yr:1d, ≤5yr:1w, ≤20yr:1M, >20yr:1Q; "
                            "intraday→≤2d:1h, ≤1w:2h, ≤30d:4h, else:1d. "
                            "(default: auto)"
                        ))

    parser.add_argument("--field", default="close",
                        choices=["close", "open", "high", "low", "volume",
                                 "vwap", "change_pct"],
                        help="Which price field to analyse (default: close)")

    parser.add_argument("--source", metavar="NAME",
                        help="Use only this data source (e.g. yfinance, fmp)")

    parser.add_argument("--analysis", nargs="+",
                        default=["summary"],
                        choices=VALID_ANALYSES,
                        metavar="TOOL",
                        help=(
                            "One or more analyses to run (default: summary).\n\n"
                            "Available tools:\n"
                            "  summary     descriptive stats: mean, std, total return, "
                                          "annualised volatility, Sharpe ratio\n"
                            "  regression  linear trend vs time: slope, R\u00b2, p-value, "
                                          "annualised trend %%, 95%% confidence band\n"
                            "  returns     periodic return distribution: histogram, "
                                          "mean, Sharpe, %% positive bars\n"
                            "  volatility  rolling annualised volatility  (see --window)\n"
                            "  correlation Pearson return correlation matrix "
                                          "(needs 2+ symbols); heatmap with --plot\n"
                            "  sma         moving average overlay  (see --sma-windows)\n"
                            "  drawdown    max drawdown, duration, recovery, Calmar ratio; "
                                          "underwater chart with --plot\n"
                            "  rsi         Relative Strength Index, overbought/oversold "
                                          "signals  (see --window)\n"
                            "  bbands      Bollinger Bands (SMA \u00b12\u03c3), bandwidth, "
                                          "squeeze signal  (see --window)\n"
                            "  montecarlo  GBM price simulation, fan chart, terminal "
                                          "distribution  (see --mc-paths, --mc-horizon)\n"
                            "  hurst       Hurst exponent: trending H>0.5 / random H\u22480.5 "
                                          "/ mean-reverting H<0.5"
                        ))

    parser.add_argument("--window", type=int, default=30,
                        help="Rolling window size for volatility (default: 30)")

    parser.add_argument("--sma-windows", nargs="+", type=int,
                        default=[20, 50, 200],
                        metavar="N",
                        help="SMA periods for the 'sma' analysis (default: 20 50 200)")

    parser.add_argument("--mc-paths", type=int, default=1000,
                        metavar="N",
                        help="Number of Monte Carlo paths (default: 1000)")
    parser.add_argument("--mc-horizon", type=int, default=252,
                        metavar="BARS",
                        help="Monte Carlo forecast horizon in bars (default: 252)")

    parser.add_argument("--plot", action="store_true",
                        help="Show matplotlib plots for each analysis")

    parser.add_argument("--save", metavar="FILE.csv",
                        help="Save the processed dataset to a CSV file")

    args = parser.parse_args()

    # ── list-symbols shortcut ─────────────────────────────────────────────────
    if args.list_symbols:
        list_symbols()
        return

    # ── require at least one symbol for analysis ──────────────────────────────
    if not args.symbols:
        parser.error("Specify at least one symbol with -s / --symbols, "
                     "or use --list-symbols to see what's available.")

    # ── load and prepare data ─────────────────────────────────────────────────
    df, effective_gran = load_data(
        symbols   = args.symbols,
        date_from = args.date_from,
        date_to   = args.date_to,
        gran      = args.granularity,
        source    = args.source,
        interval  = args.interval,
        field     = args.field,
    )

    if args.save:
        df.to_csv(args.save, index=False)
        print(f"Dataset saved to {args.save}")

    # ── run analyses ──────────────────────────────────────────────────────────
    for tool in args.analysis:
        if tool == "summary":
            analysis_summary(df, args.field, gran=effective_gran)

        elif tool == "regression":
            analysis_regression(df, args.field, args.plot)

        elif tool == "returns":
            analysis_returns(df, args.field, args.plot, gran=effective_gran)

        elif tool == "volatility":
            analysis_volatility(df, args.field, args.window, args.plot, gran=effective_gran)

        elif tool == "correlation":
            analysis_correlation(df, args.field, args.plot)

        elif tool == "sma":
            analysis_sma(df, args.field, args.sma_windows, args.plot)

        elif tool == "drawdown":
            analysis_drawdown(df, args.field, args.plot)

        elif tool == "rsi":
            analysis_rsi(df, args.field, args.window, args.plot)

        elif tool == "bbands":
            analysis_bbands(df, args.field, args.window, args.plot)

        elif tool == "montecarlo":
            analysis_montecarlo(df, args.field, args.mc_paths,
                                args.mc_horizon, args.plot)

        elif tool == "hurst":
            analysis_hurst(df, args.field, args.plot)

    print()


if __name__ == "__main__":
    main()
