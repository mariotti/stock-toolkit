"""
stock_backtest.py
=================
Backtesting engine for strategies built on data collected by stock_collector.py.

Signals are generated at bar close t and executed at bar close t with slippage
applied (end-of-day assumption). A buy-and-hold benchmark is always included.

Usage examples:
    python3 stock_backtest.py -s AAPL --strategy rsi --window 14
    python3 stock_backtest.py -s AAPL --strategy rsi --window 14 --buy-at 30 --sell-at 70 --plot
    python3 stock_backtest.py -s AAPL --strategy sma_cross --fast 20 --slow 50 --plot
    python3 stock_backtest.py -s AAPL --strategy bbands --window 20 --plot
    python3 stock_backtest.py -s AAPL --strategy breakout --window 20 --plot
    python3 stock_backtest.py -s AAPL --strategy rsi --from 2020-01-01 --test-from 2023-01-01 --plot
    python3 stock_backtest.py -s AAPL MSFT --strategy sma_cross --fast 20 --slow 50 --plot

Install dependencies:
    pip install pandas numpy matplotlib
"""

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from stock_toolkit.common import LIVE_DB, HIST_DIR, NoDataError

SOURCE_PRIORITY = [
    "alphavantage", "fmp", "yfinance",
    "finnhub", "twelvedata", "polygon", "marketstack",
]

# ─────────────────────────────────────────────
#  DATA LOADING  (mirrors stock_analysis.py)
# ─────────────────────────────────────────────

def discover_dbs() -> list[Path]:
    dbs = []
    if LIVE_DB.exists():
        dbs.append(LIVE_DB)
    if HIST_DIR.exists():
        dbs += sorted(HIST_DIR.glob("*.db"))
    if not dbs:
        raise NoDataError("No databases found. Run the collector first.")
    return dbs


def load_prices(symbol: str, date_from: str | None, date_to: str | None,
                source: str | None) -> pd.DataFrame:
    """Load daily close prices for a single symbol, sorted by date."""
    dbs = discover_dbs()
    frames = []
    for db in dbs:
        try:
            con = sqlite3.connect(db)
            df  = pd.read_sql(
                "SELECT timestamp, source, open, high, low, close, volume "
                "FROM prices WHERE symbol=? AND interval='1d' ORDER BY timestamp",
                con, params=[symbol.upper()]
            )
            con.close()
            if not df.empty:
                frames.append(df)
        except Exception:
            pass

    if not frames:
        raise NoDataError(f"No daily data found for {symbol}.")

    df = pd.concat(frames, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="mixed", utc=True,
                                     errors="coerce")
    df = df.dropna(subset=["timestamp", "close"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # source dedup
    if source:
        df = df[df["source"] == source]
    else:
        prio = {s: i for i, s in enumerate(SOURCE_PRIORITY)}
        df["_p"] = df["source"].map(lambda s: prio.get(s, 99))
        df = df.sort_values("_p").drop_duplicates(subset=["timestamp"], keep="first")
        df = df.drop(columns=["_p"])

    df = df.sort_values("timestamp").reset_index(drop=True)

    if date_from:
        df = df[df["timestamp"] >= pd.Timestamp(date_from, tz="UTC")]
    if date_to:
        df = df[df["timestamp"] <= pd.Timestamp(date_to, tz="UTC")]

    if df.empty:
        raise NoDataError(f"No data for {symbol} in the requested range.")

    return df.reset_index(drop=True)


# ─────────────────────────────────────────────
#  INDICATORS
# ─────────────────────────────────────────────

def _rsi(series: pd.Series, window: int) -> pd.Series:
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).mean()


def _bbands(series: pd.Series, window: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid   = series.rolling(window).mean()
    std   = series.rolling(window).std()
    return mid - 2 * std, mid, mid + 2 * std


def _macd(series: pd.Series, fast: int = 12, slow: int = 26,
          signal_window: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Standard MACD: EMA(fast) − EMA(slow), with a signal EMA on top.
    Returns (macd_line, signal_line, histogram)."""
    ema_fast = series.ewm(span=fast,  adjust=False).mean()
    ema_slow = series.ewm(span=slow,  adjust=False).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal_window, adjust=False).mean()
    hist        = macd_line - signal_line
    return macd_line, signal_line, hist


# ─────────────────────────────────────────────
#  SIGNAL GENERATORS
#  Each returns a pd.Series of int: +1=BUY, -1=SELL, 0=HOLD
#  Signals are shift(1) before use so there is NO lookahead.
# ─────────────────────────────────────────────

def signals_rsi(df: pd.DataFrame, window: int,
                buy_at: float, sell_at: float) -> pd.Series:
    """
    Buy when RSI crosses below buy_at (oversold).
    Sell when RSI crosses above sell_at (overbought).
    """
    rsi  = _rsi(df["close"], window)
    prev = rsi.shift(1)
    sig  = pd.Series(0, index=df.index)
    sig[((prev >= buy_at)  & (rsi < buy_at))]  =  1   # crossing down into oversold
    sig[((prev <= sell_at) & (rsi > sell_at))] = -1   # crossing up into overbought
    return sig


def signals_sma_cross(df: pd.DataFrame, fast: int, slow: int) -> pd.Series:
    """
    Buy on golden cross (fast SMA crosses above slow SMA).
    Sell on death cross (fast SMA crosses below slow SMA).
    """
    fast_ma  = _sma(df["close"], fast)
    slow_ma  = _sma(df["close"], slow)
    prev_f   = fast_ma.shift(1)
    prev_s   = slow_ma.shift(1)
    sig      = pd.Series(0, index=df.index)
    sig[(prev_f <= prev_s) & (fast_ma > slow_ma)] =  1   # golden cross
    sig[(prev_f >= prev_s) & (fast_ma < slow_ma)] = -1   # death cross
    return sig


def signals_bbands(df: pd.DataFrame, window: int) -> pd.Series:
    """
    Buy when close crosses below lower band (enter at oversold extreme).
    Sell when close crosses above middle band (exit at mean).
    """
    lower, mid, _ = _bbands(df["close"], window)
    prev_close    = df["close"].shift(1)
    prev_lower    = lower.shift(1)
    prev_mid      = mid.shift(1)
    sig           = pd.Series(0, index=df.index)
    sig[(prev_close >= prev_lower) & (df["close"] < lower)] =  1
    sig[(prev_close <= prev_mid)   & (df["close"] > mid)]   = -1
    return sig


def signals_breakout(df: pd.DataFrame, window: int) -> pd.Series:
    """
    Buy when close breaks above the highest close of the past `window` bars.
    Sell when close breaks below the lowest close of the past `window` bars.
    """
    roll_high = df["close"].shift(1).rolling(window).max()
    roll_low  = df["close"].shift(1).rolling(window).min()
    sig       = pd.Series(0, index=df.index)
    sig[df["close"] > roll_high] =  1
    sig[df["close"] < roll_low]  = -1
    return sig


def signals_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26,
                 signal_window: int = 9) -> pd.Series:
    """Classic MACD-cross trend-following strategy.

    Buy when the MACD line crosses above the signal line (bullish cross).
    Sell when it crosses back below (bearish cross). Slow on noisy
    sideways markets, decent on persistent trends.
    """
    macd_line, signal_line, _ = _macd(df["close"], fast, slow, signal_window)
    prev_m = macd_line.shift(1)
    prev_s = signal_line.shift(1)
    sig    = pd.Series(0, index=df.index)
    sig[(prev_m <= prev_s) & (macd_line > signal_line)] =  1   # bullish cross
    sig[(prev_m >= prev_s) & (macd_line < signal_line)] = -1   # bearish cross
    return sig


STRATEGIES = {
    "rsi":       signals_rsi,
    "sma_cross": signals_sma_cross,
    "bbands":    signals_bbands,
    "breakout":  signals_breakout,
    "macd":      signals_macd,
}


# ─────────────────────────────────────────────
#  BACKTESTER
# ─────────────────────────────────────────────

class Backtester:
    """
    Simple long-only backtester.

    Execution model:
      - Signal generated at bar close t.
      - Trade fills at bar close t × (1 ± slippage).
      - Commission deducted as a percentage of trade value.
      - Only one position at a time (no pyramiding).
      - Fractional shares allowed.
    """

    def __init__(self, capital: float = 10_000.0,
                 commission: float = 0.001,
                 slippage:   float = 0.001):
        self.capital    = capital
        self.commission = commission   # fraction of trade value, e.g. 0.001 = 0.1%
        self.slippage   = slippage     # fraction of price, e.g. 0.001 = 0.1%

    def run(self, df: pd.DataFrame, signals: pd.Series) -> dict:
        """
        Run the backtest.  Returns a dict with equity curve, trade log,
        and performance metrics.
        """
        close   = df["close"].values
        dates   = df["timestamp"].values
        sigs    = signals.values

        cash     = self.capital
        shares   = 0.0
        equity   = np.zeros(len(close))
        in_trade = False
        trades   = []   # list of (entry_date, exit_date, entry_price, exit_price, pnl_pct)

        entry_price = 0.0
        entry_date  = None

        for i in range(len(close)):
            price = close[i]
            if np.isnan(price):
                equity[i] = cash + shares * (close[i-1] if i > 0 else 0)
                continue

            sig = sigs[i]

            # ── BUY ────────────────────────────────────────────────────────
            if sig == 1 and not in_trade and cash > 0:
                fill       = price * (1 + self.slippage)
                cost       = cash * self.commission
                shares     = (cash - cost) / fill
                cash       = 0.0
                in_trade   = True
                entry_price = fill
                entry_date  = dates[i]

            # ── SELL ───────────────────────────────────────────────────────
            elif sig == -1 and in_trade and shares > 0:
                fill       = price * (1 - self.slippage)
                proceeds   = shares * fill
                cost       = proceeds * self.commission
                pnl_pct    = (fill / entry_price - 1) * 100
                trades.append({
                    "entry_date":  pd.Timestamp(entry_date),
                    "exit_date":   pd.Timestamp(dates[i]),
                    "entry_price": round(entry_price, 4),
                    "exit_price":  round(fill, 4),
                    "pnl_pct":     round(pnl_pct, 3),
                    "pnl_abs":     round(proceeds - cost - shares * entry_price, 2),
                })
                cash     = proceeds - cost
                shares   = 0.0
                in_trade = False

            equity[i] = cash + shares * price

        # close any open position at last price
        if in_trade and shares > 0:
            fill     = close[-1] * (1 - self.slippage)
            proceeds = shares * fill
            pnl_pct  = (fill / entry_price - 1) * 100
            trades.append({
                "entry_date":  pd.Timestamp(entry_date),
                "exit_date":   pd.Timestamp(dates[-1]),
                "entry_price": round(entry_price, 4),
                "exit_price":  round(fill, 4),
                "pnl_pct":     round(pnl_pct, 3),
                "pnl_abs":     round(proceeds - shares * entry_price, 2),
            })
            equity[-1] = proceeds

        return {
            "equity":     equity,
            "dates":      dates,
            "trades":     trades,
            "metrics":    self._metrics(equity, trades, dates),
        }

    def _metrics(self, equity: np.ndarray, trades: list,
                 dates: np.ndarray) -> dict:
        if len(equity) < 2:
            return {}

        # remove leading zeros (before first trade)
        eq = equity.copy()
        first_nz = np.argmax(eq > 0)
        eq[:first_nz] = eq[first_nz]

        rets    = np.diff(eq) / eq[:-1]
        rets    = rets[~np.isnan(rets)]
        n_years = max((pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])).days / 365.25,
                      1e-6)

        total_ret = (eq[-1] / self.capital - 1) * 100
        cagr      = ((eq[-1] / self.capital) ** (1 / n_years) - 1) * 100

        ann  = np.sqrt(252)
        sharpe  = (rets.mean() / rets.std() * ann) if rets.std() > 0 else 0.0
        neg     = rets[rets < 0]
        sortino = (rets.mean() / neg.std() * ann) if (len(neg) > 0 and neg.std() > 0) else 0.0

        # max drawdown
        roll_max = np.maximum.accumulate(eq)
        dd       = (eq - roll_max) / roll_max * 100
        max_dd   = dd.min()
        calmar   = cagr / abs(max_dd) if max_dd != 0 else 0.0

        # trade stats
        if trades:
            pnls      = [t["pnl_pct"] for t in trades]
            winners   = [p for p in pnls if p > 0]
            losers    = [p for p in pnls if p <= 0]
            win_rate  = len(winners) / len(pnls) * 100
            avg_win   = np.mean(winners) if winners else 0.0
            avg_loss  = np.mean(losers)  if losers  else 0.0
            gross_p   = sum(winners)
            gross_l   = abs(sum(losers))
            pf        = gross_p / gross_l if gross_l > 0 else float("inf")
        else:
            win_rate = avg_win = avg_loss = pf = 0.0

        return {
            "total_return_pct": round(total_ret, 2),
            "cagr_pct":         round(cagr, 2),
            "sharpe":           round(sharpe, 3),
            "sortino":          round(sortino, 3),
            "max_dd_pct":       round(max_dd, 2),
            "calmar":           round(calmar, 3),
            "n_trades":         len(trades),
            "win_rate_pct":     round(win_rate, 1),
            "avg_win_pct":      round(avg_win, 2),
            "avg_loss_pct":     round(avg_loss, 2),
            "profit_factor":    round(pf, 3),
            "final_value":      round(eq[-1], 2),
        }


# ─────────────────────────────────────────────
#  OUTPUT HELPERS
# ─────────────────────────────────────────────

def _sep(title: str = "", width: int = 64):
    if title:
        pad = max(0, width - len(title) - 4)
        print(f"\n── {title} {'─' * pad}")
    else:
        print("─" * width)


def _tbl(rows: list[tuple], headers: list[str]):
    col_w = [len(h) for h in headers]
    for row in rows:
        for i, v in enumerate(row):
            col_w[i] = max(col_w[i], len(str(v)))
    fmt = "  ".join(f"{{:<{w}}}" for w in col_w)
    print(fmt.format(*headers))
    print("  ".join("─" * w for w in col_w))
    for row in rows:
        print(fmt.format(*[str(v) for v in row]))


def print_metrics(sym: str, strategy_label: str, m: dict, bh: dict):
    """Print strategy vs buy-and-hold comparison table."""
    _sep(f"{sym}  —  {strategy_label}")
    rows = [
        ("Total return",   f"{m['total_return_pct']:+.2f}%",   f"{bh['total_return_pct']:+.2f}%"),
        ("CAGR",           f"{m['cagr_pct']:+.2f}%",           f"{bh['cagr_pct']:+.2f}%"),
        ("Sharpe",         f"{m['sharpe']:.3f}",                f"{bh['sharpe']:.3f}"),
        ("Sortino",        f"{m['sortino']:.3f}",               f"{bh['sortino']:.3f}"),
        ("Max drawdown",   f"{m['max_dd_pct']:.2f}%",          f"{bh['max_dd_pct']:.2f}%"),
        ("Calmar",         f"{m['calmar']:.3f}",                f"{bh['calmar']:.3f}"),
        ("Final value",    f"${m['final_value']:,.2f}",         f"${bh['final_value']:,.2f}"),
        ("Trades",         str(m["n_trades"]),                  "1"),
        ("Win rate",       f"{m['win_rate_pct']:.1f}%",         "—"),
        ("Avg win",        f"{m['avg_win_pct']:+.2f}%",         "—"),
        ("Avg loss",       f"{m['avg_loss_pct']:+.2f}%",        "—"),
        ("Profit factor",  f"{m['profit_factor']:.3f}",         "—"),
    ]
    _tbl(rows, ["Metric", "Strategy", "Buy & Hold"])


def print_trades(trades: list[dict], max_rows: int = 20):
    if not trades:
        print("  No trades executed.")
        return
    _sep("Trade log")
    rows = [
        (
            t["entry_date"].strftime("%Y-%m-%d"),
            t["exit_date"].strftime("%Y-%m-%d"),
            f"{t['entry_price']:.2f}",
            f"{t['exit_price']:.2f}",
            f"{t['pnl_pct']:+.2f}%",
            f"${t['pnl_abs']:+.2f}",
        )
        for t in trades[:max_rows]
    ]
    _tbl(rows, ["Entry", "Exit", "Buy @", "Sell @", "P&L %", "P&L $"])
    if len(trades) > max_rows:
        print(f"  … {len(trades) - max_rows} more trades not shown.")


# ─────────────────────────────────────────────
#  PLOTTING
# ─────────────────────────────────────────────

def plot_results(sym: str, df: pd.DataFrame, result: dict, bh_result: dict,
                 strategy_label: str, indicator_fn=None):
    try:
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        print("[plot] matplotlib not installed — run: pip install matplotlib")
        return

    dates  = pd.to_datetime(result["dates"])
    equity = result["equity"]
    bh_eq  = bh_result["equity"]
    trades = result["trades"]

    n_panels = 3 if indicator_fn is not None else 2
    fig, axes = plt.subplots(n_panels, 1, figsize=(14, 4 * n_panels),
                              gridspec_kw={"height_ratios": [2, 1.5] + ([1] * (n_panels - 2))})

    # ── panel 1: price + trade markers ────────────────────────────────────────
    ax1 = axes[0]
    ax1.plot(dates, df["close"].values, color="#1f77b4", linewidth=1.2, label="Price")

    for t in trades:
        ax1.axvline(t["entry_date"], color="green", alpha=0.25, linewidth=0.8)
        ax1.axvline(t["exit_date"],  color="red",   alpha=0.25, linewidth=0.8)

    # entry/exit markers on price
    for t in trades:
        ep = df.loc[df["timestamp"] == pd.Timestamp(t["entry_date"], tz="UTC"), "close"]
        xp = df.loc[df["timestamp"] == pd.Timestamp(t["exit_date"],  tz="UTC"), "close"]
        if not ep.empty:
            ax1.scatter(t["entry_date"], ep.values[0], marker="^", color="green",
                        s=60, zorder=5)
        if not xp.empty:
            ax1.scatter(t["exit_date"],  xp.values[0], marker="v", color="red",
                        s=60, zorder=5)

    ax1.set_title(f"{sym} — {strategy_label}")
    ax1.set_ylabel("Price")
    ax1.legend(fontsize=9)
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax1.xaxis.set_major_locator(mdates.AutoDateLocator())

    # ── panel 2: equity curve ─────────────────────────────────────────────────
    ax2 = axes[1]
    ax2.plot(dates, equity, color="#2ca02c", linewidth=1.5, label="Strategy")
    ax2.plot(dates, bh_eq,  color="#7f7f7f", linewidth=1.0,
             linestyle="--", alpha=0.7, label="Buy & hold")
    ax2.set_ylabel("Portfolio value ($)")
    ax2.legend(fontsize=9)
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax2.xaxis.set_major_locator(mdates.AutoDateLocator())

    # ── panel 3: indicator (optional) ─────────────────────────────────────────
    if indicator_fn is not None and n_panels == 3:
        ax3 = axes[2]
        indicator_fn(ax3, df, dates)
        ax3.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
        ax3.xaxis.set_major_locator(mdates.AutoDateLocator())

    fig.autofmt_xdate(rotation=35)
    plt.tight_layout()
    plt.show()


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def _main():
    parser = argparse.ArgumentParser(
        description="Backtesting engine for stock_collector.py data",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""
strategies:
  rsi        Buy when RSI crosses below --buy-at, sell when RSI crosses above --sell-at
  sma_cross  Buy on golden cross (fast SMA > slow SMA), sell on death cross
  bbands     Buy when price crosses below lower Bollinger Band, sell at middle band
  breakout   Buy on N-bar high breakout, sell on N-bar low breakdown
  macd       Buy on MACD-over-signal bullish cross, sell on bearish cross

examples:
  python3 stock_backtest.py -s AAPL --strategy rsi --window 14 --plot
  python3 stock_backtest.py -s AAPL --strategy sma_cross --fast 20 --slow 50 --plot
  python3 stock_backtest.py -s AAPL --strategy bbands --window 20 --plot
  python3 stock_backtest.py -s AAPL --strategy breakout --window 20 --plot
  python3 stock_backtest.py -s AAPL MSFT --strategy rsi --window 14 --plot
  python3 stock_backtest.py -s AAPL --strategy rsi --from 2018-01-01 --test-from 2023-01-01
        """
    )

    parser.add_argument("-s", "--symbols", nargs="+", metavar="TICKER", required=True)
    parser.add_argument("--strategy", required=True, choices=list(STRATEGIES),
                        help="Strategy to backtest")
    parser.add_argument("--from",     dest="date_from", metavar="YYYY-MM-DD")
    parser.add_argument("--to",       dest="date_to",   metavar="YYYY-MM-DD")
    parser.add_argument("--test-from", dest="test_from", metavar="YYYY-MM-DD",
                        help="Walk-forward split: train up to this date, test from here")
    parser.add_argument("--source",   metavar="NAME",
                        help="Use only this data source")

    # strategy parameters
    parser.add_argument("--window", type=int, default=14,
                        help="RSI / Bollinger / breakout window (default: 14)")
    parser.add_argument("--fast",   type=int, default=20,
                        help="Fast SMA window for sma_cross (default: 20)")
    parser.add_argument("--slow",   type=int, default=50,
                        help="Slow SMA window for sma_cross (default: 50)")
    parser.add_argument("--buy-at",  dest="buy_at",  type=float, default=30.0,
                        help="RSI level to buy at (default: 30)")
    parser.add_argument("--sell-at", dest="sell_at", type=float, default=70.0,
                        help="RSI level to sell at (default: 70)")
    parser.add_argument("--macd-fast",   dest="macd_fast",   type=int, default=12,
                        help="MACD fast EMA span (default: 12)")
    parser.add_argument("--macd-slow",   dest="macd_slow",   type=int, default=26,
                        help="MACD slow EMA span (default: 26)")
    parser.add_argument("--macd-signal", dest="macd_signal", type=int, default=9,
                        help="MACD signal EMA span (default: 9)")

    # execution parameters
    parser.add_argument("--capital",    type=float, default=10_000.0,
                        help="Starting capital in $ (default: 10000)")
    parser.add_argument("--commission", type=float, default=0.001,
                        help="Commission as fraction of trade value (default: 0.001 = 0.1%%)")
    parser.add_argument("--slippage",   type=float, default=0.001,
                        help="Slippage as fraction of price (default: 0.001 = 0.1%%)")

    parser.add_argument("--plot",        action="store_true")
    parser.add_argument("--show-trades", action="store_true",
                        help="Print every trade in the log")

    args = parser.parse_args()

    bt = Backtester(
        capital    = args.capital,
        commission = args.commission,
        slippage   = args.slippage,
    )

    for sym in args.symbols:
        df = load_prices(sym, args.date_from, args.date_to, args.source)

        # ── walk-forward split ────────────────────────────────────────────────
        if args.test_from:
            split_ts = pd.Timestamp(args.test_from, tz="UTC")
            train_df = df[df["timestamp"] <  split_ts].reset_index(drop=True)
            test_df  = df[df["timestamp"] >= split_ts].reset_index(drop=True)
            print(f"\n{sym}: walk-forward split at {args.test_from}")
            print(f"  Train: {len(train_df)} bars  "
                  f"({train_df['timestamp'].iloc[0].date()} → "
                  f"{train_df['timestamp'].iloc[-1].date()})")
            print(f"  Test:  {len(test_df)} bars  "
                  f"({test_df['timestamp'].iloc[0].date()} → "
                  f"{test_df['timestamp'].iloc[-1].date()})")
            eval_df = test_df
        else:
            eval_df = df

        # ── generate signals ──────────────────────────────────────────────────
        strategy_fn = STRATEGIES[args.strategy]

        if args.strategy == "rsi":
            signals = strategy_fn(eval_df, args.window, args.buy_at, args.sell_at)
            label   = f"RSI({args.window}) buy<{args.buy_at} sell>{args.sell_at}"
            def ind_fn(ax, df, dates):
                rsi = _rsi(df["close"], args.window)
                ax.plot(dates, rsi, color="#1f77b4", linewidth=1.0)
                ax.axhline(args.buy_at,  color="green", linewidth=0.8, linestyle="--")
                ax.axhline(args.sell_at, color="red",   linewidth=0.8, linestyle="--")
                ax.set_ylabel(f"RSI({args.window})")
                ax.set_ylim(0, 100)
                ax.grid(True, alpha=0.3)
        elif args.strategy == "sma_cross":
            signals = strategy_fn(eval_df, args.fast, args.slow)
            label   = f"SMA cross {args.fast}/{args.slow}"
            def ind_fn(ax, df, dates):
                ax.plot(dates, _sma(df["close"], args.fast), linewidth=1.0,
                        color="#ff7f0e", label=f"SMA{args.fast}")
                ax.plot(dates, _sma(df["close"], args.slow), linewidth=1.0,
                        color="#d62728", label=f"SMA{args.slow}")
                ax.set_ylabel("SMA")
                ax.legend(fontsize=8)
                ax.grid(True, alpha=0.3)
        elif args.strategy == "bbands":
            signals = strategy_fn(eval_df, args.window)
            label   = f"Bollinger Bands({args.window})"
            def ind_fn(ax, df, dates):
                lo, mid, hi = _bbands(df["close"], args.window)
                ax.plot(dates, lo,  color="#1f77b4", linewidth=0.8, linestyle=":")
                ax.plot(dates, mid, color="#1f77b4", linewidth=0.8, linestyle="--")
                ax.plot(dates, hi,  color="#1f77b4", linewidth=0.8, linestyle=":")
                ax.fill_between(dates, lo, hi, alpha=0.1, color="#1f77b4")
                ax.set_ylabel("BBands")
                ax.grid(True, alpha=0.3)
        elif args.strategy == "macd":
            signals = strategy_fn(eval_df, args.macd_fast, args.macd_slow,
                                  args.macd_signal)
            label   = (f"MACD({args.macd_fast},{args.macd_slow},"
                       f"{args.macd_signal})")
            def ind_fn(ax, df, dates):
                m, s, _h = _macd(
                    df["close"], args.macd_fast, args.macd_slow,
                    args.macd_signal,
                )
                ax.plot(dates, m, color="#1f77b4", linewidth=1.0,
                        label="MACD")
                ax.plot(dates, s, color="#ff7f0e", linewidth=1.0,
                        label="Signal")
                ax.axhline(0, color="#888", linewidth=0.6, linestyle="--")
                ax.set_ylabel("MACD")
                ax.legend(fontsize=8)
                ax.grid(True, alpha=0.3)
        else:  # breakout
            signals = strategy_fn(eval_df, args.window)
            label   = f"Breakout({args.window})"
            ind_fn  = None

        # ── run strategy ──────────────────────────────────────────────────────
        result = bt.run(eval_df, signals)

        # ── buy and hold benchmark ────────────────────────────────────────────
        bh_signals = pd.Series(0, index=eval_df.index)
        bh_signals.iloc[0] = 1   # buy on day one, never sell
        bh_result = bt.run(eval_df, bh_signals)

        # ── print results ─────────────────────────────────────────────────────
        print_metrics(sym, label, result["metrics"], bh_result["metrics"])

        if args.show_trades or result["metrics"]["n_trades"] <= 30:
            print_trades(result["trades"])

        if args.plot:
            plot_results(sym, eval_df, result, bh_result, label,
                         indicator_fn=ind_fn)

    print()


def main():
    try:
        _main()
    except NoDataError as e:
        print(f"[error] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
