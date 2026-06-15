"""
stock_score.py
==============
Runs all seven analysis steps against every symbol in the database and
produces a ranked investment score.  Designed to answer the question:
"Right now, which of these symbols looks best to buy?"

The score is NOT a buy recommendation.  It is a data-driven summary of
risk-adjusted return, trend quality, and entry timing — nothing more.

Usage:
    python3 stock_score.py
    python3 stock_score.py -s AAPL MSFT GOOGL
    python3 stock_score.py --from 2023-01-01
    python3 stock_score.py --horizon week
    python3 stock_score.py --horizon month
    python3 stock_score.py --horizon quarter   (default)
    python3 stock_score.py --horizon year
    python3 stock_score.py --horizon life
    python3 stock_score.py --top 3
    python3 stock_score.py --json

Install dependencies:
    pip install pandas numpy scipy matplotlib
"""

import argparse
import json
import sqlite3
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

from stock_toolkit.common import LIVE_DB, HIST_DIR, NoDataError

SOURCE_PRIORITY = [
    "alphavantage", "fmp", "yfinance",
    "finnhub", "twelvedata", "polygon", "marketstack",
]

# ─────────────────────────────────────────────
#  HORIZON PROFILES
# ─────────────────────────────────────────────

HORIZON_PROFILES = {
    "week": {
        "label":      "Next week (~5 trading days)",
        "gran":       "D",
        "mc_bars":    5,
        "min_bars":   30,
        "ann_factor": 252,
        "weights": {"sharpe":4,"calmar":4,"r2":4,"ann_trend":4,
                    "rsi_entry":30,"pct_b_entry":30,"prob_gain":8,
                    "momentum":8,"hurst":8},
    },
    "month": {
        "label":      "Next month (~21 trading days)",
        "gran":       "D",
        "mc_bars":    21,
        "min_bars":   60,
        "ann_factor": 252,
        "weights": {"sharpe":8,"calmar":8,"r2":4,"ann_trend":8,
                    "rsi_entry":20,"pct_b_entry":20,"prob_gain":12,
                    "momentum":12,"hurst":8},
    },
    "quarter": {
        "label":      "Next quarter (~63 trading days)",
        "gran":       "W-FRI",
        "mc_bars":    63,
        "min_bars":   60,
        "ann_factor": 52,
        "weights": {"sharpe":16,"calmar":16,"r2":8,"ann_trend":8,
                    "rsi_entry":12,"pct_b_entry":12,"prob_gain":8,
                    "momentum":14,"hurst":6},
    },
    "year": {
        "label":      "Next year (~252 trading days)",
        "gran":       "W-FRI",
        "mc_bars":    252,
        "min_bars":   100,
        "ann_factor": 52,
        "weights": {"sharpe":20,"calmar":20,"r2":16,"ann_trend":12,
                    "rsi_entry":4,"pct_b_entry":4,"prob_gain":4,
                    "momentum":14,"hurst":6},
    },
    "life": {
        "label":      "Long-term / buy and hold (5+ years)",
        "gran":       "ME",
        "mc_bars":    60,
        "min_bars":   120,
        "ann_factor": 12,
        "weights": {"sharpe":26,"calmar":22,"r2":22,"ann_trend":12,
                    "rsi_entry":2,"pct_b_entry":2,"prob_gain":0,
                    "momentum":8,"hurst":6},
    },
}

WEIGHTS   = HORIZON_PROFILES["quarter"]["weights"]   # default
MAX_SCORE = sum(WEIGHTS.values())   # always 100

PENALTY = {
    "unrecovered_dd":  -20,   # drawdown not yet recovered
    "high_vol":        -10,   # annualised vol > 50%
    "deep_dd":         -10,   # max drawdown > 40%
    "thin_data":       -30,   # fewer than 60 bars
}

# ─────────────────────────────────────────────
#  DATA LOADING
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


def list_all_symbols() -> list[str]:
    dbs  = discover_dbs()
    syms = set()
    for db in dbs:
        try:
            con = sqlite3.connect(db)
            rows = con.execute(
                "SELECT DISTINCT symbol FROM prices WHERE interval='1d'"
            ).fetchall()
            con.close()
            syms.update(r[0] for r in rows)
        except Exception:
            pass
    return sorted(syms)


def load_prices(symbol: str, date_from: str | None,
                date_to: str | None) -> pd.DataFrame:
    """Load daily bars for a single symbol, deduplicated by source priority."""
    dbs    = discover_dbs()
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
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="mixed",
                                      utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp", "close"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    prio = {s: i for i, s in enumerate(SOURCE_PRIORITY)}
    df["_p"] = df["source"].map(lambda s: prio.get(s, 99))
    df = df.sort_values("_p").drop_duplicates(subset=["timestamp"], keep="first")
    df = df.drop(columns=["_p"]).sort_values("timestamp").reset_index(drop=True)

    if date_from:
        df = df[df["timestamp"] >= pd.Timestamp(date_from, tz="UTC")]
    if date_to:
        df = df[df["timestamp"] <= pd.Timestamp(date_to, tz="UTC")]

    return df.reset_index(drop=True)


# ─────────────────────────────────────────────
#  INDICATOR HELPERS
# ─────────────────────────────────────────────

def _sma(s: pd.Series, w: int) -> pd.Series:
    return s.rolling(w).mean()

def _rsi(s: pd.Series, w: int = 14) -> float:
    delta = s.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1/w, adjust=False).mean()
    loss  = (-delta).clip(lower=0).ewm(alpha=1/w, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    val   = rsi.dropna()
    return float(val.iloc[-1]) if not val.empty else float("nan")

def _pct_b(s: pd.Series, w: int = 20) -> float:
    mid   = s.rolling(w).mean()
    std   = s.rolling(w).std()
    upper = mid + 2 * std
    lower = mid - 2 * std
    pb    = (s - lower) / (upper - lower)
    val   = pb.dropna()
    return float(val.iloc[-1]) if not val.empty else float("nan")

def _bbands_squeeze(s: pd.Series, w: int = 20) -> bool:
    mid = s.rolling(w).mean()
    std = s.rolling(w).std()
    bw  = ((mid + 2*std - (mid - 2*std)) / mid * 100).dropna()
    if len(bw) < w:
        return False
    return bool(bw.iloc[-1] < bw.quantile(0.2))


# ─────────────────────────────────────────────
#  SEVEN STEPS — all computed per symbol
# ─────────────────────────────────────────────

def step_summary(df: pd.DataFrame, ann_factor: int = 52) -> dict:
    """Step 2 — descriptive statistics."""
    s      = df["close"].dropna()
    rets   = s.pct_change().dropna()
    af     = ann_factor
    n_bars = len(s)
    if n_bars < 2 or rets.std() == 0:
        return {}

    total_ret  = (s.iloc[-1] / s.iloc[0] - 1) * 100
    ann_vol    = rets.std() * np.sqrt(af) * 100
    sharpe     = rets.mean() / rets.std() * np.sqrt(af)
    return {
        "n_bars":    n_bars,
        "total_ret": round(total_ret, 2),
        "ann_vol":   round(ann_vol, 2),
        "sharpe":    round(sharpe, 3),
        "first":     round(float(s.iloc[0]), 2),
        "last":      round(float(s.iloc[-1]), 2),
    }


def step_regression(df: pd.DataFrame) -> dict:
    """Step 3 — regression + Hurst (simplified: R² only)."""
    try:
        from scipy import stats as sp
    except ImportError:
        return {}

    s = df["close"].dropna().values.astype(float)
    if len(s) < 10:
        return {}

    x = np.arange(len(s), dtype=float)
    slope, intercept, r, p, _ = sp.linregress(x, s)

    # annualised trend as % of starting price
    base       = intercept if intercept > 0 else s[0]
    ann_trend  = (slope * 52 / base) * 100   # weekly bars

    return {
        "r2":        round(r**2, 4),
        "ann_trend": round(ann_trend, 1),
        "p_value":   round(p, 6),
        "slope":     round(slope, 4),
    }


def step_drawdown(df: pd.DataFrame) -> dict:
    """Step 4 — volatility + drawdown."""
    s    = df["close"].dropna().values.astype(float)
    if len(s) < 5:
        return {}

    rets = np.diff(s) / s[:-1]

    # rolling 20-bar vol (weekly = 20-week window)
    if len(rets) >= 20:
        latest_vol = np.std(rets[-20:]) * np.sqrt(52) * 100
    else:
        latest_vol = np.std(rets) * np.sqrt(52) * 100

    # drawdown
    hwm       = np.maximum.accumulate(s)
    dd        = (s - hwm) / hwm * 100
    max_dd    = float(dd.min())
    trough_i  = int(dd.argmin())
    peak_i    = int(np.argmax(s[:trough_i + 1]))
    dd_dur    = trough_i - peak_i

    # recovery
    peak_price  = s[peak_i]
    after       = s[trough_i:]
    recovered   = np.where(after >= peak_price)[0]
    rec_bars    = int(recovered[0]) if len(recovered) > 0 else None

    # annualised return + Calmar
    n_years    = max(len(s) / 52, 1e-6)
    total_ret  = (s[-1] / s[0] - 1)
    ann_ret    = (1 + total_ret) ** (1 / n_years) - 1
    calmar     = ann_ret / abs(max_dd / 100) if max_dd != 0 else 0.0

    return {
        "latest_vol": round(latest_vol, 1),
        "max_dd":     round(max_dd, 2),
        "dd_dur":     dd_dur,
        "recovered":  rec_bars is not None,
        "calmar":     round(calmar, 3),
        "ann_ret":    round(ann_ret * 100, 1),
    }


def step_entry_timing(df: pd.DataFrame) -> dict:
    """Step 5 — RSI and Bollinger Band entry signals."""
    close = df["close"].dropna()
    if len(close) < 21:
        return {}

    rsi  = _rsi(close, 14)
    pb   = _pct_b(close, 20)
    sq   = _bbands_squeeze(close, 20)

    return {
        "rsi14":          round(rsi, 1) if not np.isnan(rsi) else None,
        "pct_b":          round(pb, 3)  if not np.isnan(pb)  else None,
        "bbands_squeeze": sq,
    }


def step_montecarlo(df: pd.DataFrame, n_paths: int,
                    horizon: int) -> dict:
    """Step 7 — GBM Monte Carlo."""
    price = df["close"].dropna().values.astype(float)
    if len(price) < 10:
        return {}

    rets   = np.diff(np.log(price))
    mu     = rets.mean()
    sigma  = rets.std()
    s0     = price[-1]

    rng    = np.random.default_rng(seed=42)
    eps    = rng.standard_normal((horizon, n_paths))
    daily  = np.exp((mu - 0.5 * sigma**2) + sigma * eps)
    paths  = s0 * np.cumprod(daily, axis=0)
    final  = paths[-1]

    pcts   = np.percentile(final, [5, 25, 50, 75, 95])
    prob   = (final > s0).mean() * 100

    return {
        "p5":        round(float(pcts[0]), 2),
        "p50":       round(float(pcts[2]), 2),
        "p95":       round(float(pcts[4]), 2),
        "prob_gain": round(prob, 1),
        "exp_ret":   round((final.mean() / s0 - 1) * 100, 1),
    }


def step_momentum(df: pd.DataFrame) -> dict:
    """Step 8 — price momentum on DAILY bars (not the resampled frame).

    mom_12_1: return over the last ~12 months excluding the most recent
    month (the classic cross-sectional momentum signal). Falls back to
    available-history-minus-last-month when fewer than ~13 months of
    bars exist (flagged with mom_12_1_partial).
    mom_3m: simple 3-month (63 trading day) return.
    """
    close = df["close"].dropna().reset_index(drop=True)
    n = len(close)
    out = {}
    if n >= 64:
        out["mom_3m"] = round((close.iloc[-1] / close.iloc[-64] - 1) * 100, 1)
    if n >= 274:
        out["mom_12_1"] = round((close.iloc[-22] / close.iloc[-274] - 1) * 100, 1)
    elif n >= 120:
        out["mom_12_1"] = round((close.iloc[-22] / close.iloc[0] - 1) * 100, 1)
        out["mom_12_1_partial"] = True
    return out


def step_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26,
              signal_window: int = 9) -> dict:
    """Step — MACD snapshot for the latest bar.

    Returns the latest MACD value, signal value, histogram, and a
    coarse regime label ('bullish' if MACD > signal and histogram
    growing, 'bearish' if the reverse, 'neutral' otherwise). Used by
    the Briefing prompt and surfaced in the score table for
    context — does not enter the weighted score itself.
    """
    close = df["close"].dropna()
    if len(close) < slow + signal_window:
        return {}
    ema_fast = close.ewm(span=fast,  adjust=False).mean()
    ema_slow = close.ewm(span=slow,  adjust=False).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal_window, adjust=False).mean()
    hist        = macd_line - signal_line

    m_now, s_now, h_now = (float(macd_line.iloc[-1]),
                           float(signal_line.iloc[-1]),
                           float(hist.iloc[-1]))
    h_prev = float(hist.iloc[-2]) if len(hist) >= 2 else h_now
    if m_now > s_now and h_now > h_prev:
        regime = "bullish"
    elif m_now < s_now and h_now < h_prev:
        regime = "bearish"
    else:
        regime = "neutral"
    return {"macd":   round(m_now, 4),
            "signal": round(s_now, 4),
            "hist":   round(h_now, 4),
            "regime": regime}


def step_hurst(df: pd.DataFrame) -> dict:
    """Step 9 — Hurst exponent of DAILY log returns (trend persistence).

    Computed on returns, not prices: R/S on a drifting price series
    saturates near H=1 for anything uptrending and discriminates nothing.
    On returns, H≈0.5 is a random walk, H>0.5 persistent (trends carry
    on), H<0.5 anti-persistent (mean-reverting).
    """
    from stock_toolkit.analysis import hurst_exponent

    price = df["close"].dropna().values.astype(float)
    if len(price) < 100:
        return {}
    rets = np.diff(np.log(price))
    h, _, _ = hurst_exponent(rets)
    if np.isnan(h):
        return {}
    regime = ("trending" if h > 0.55
              else "mean-reverting" if h < 0.45 else "random walk")
    return {"hurst": round(float(h), 3), "regime": regime}


# ─────────────────────────────────────────────
#  SCORING ENGINE
# ─────────────────────────────────────────────

def score_symbol(sym: dict, weights: dict | None = None,
                 min_bars: int = 60) -> tuple[float, list[str]]:
    """
    Convert raw metrics into a 0–100 score.
    weights overrides global WEIGHTS (set by --horizon).
    Returns (score, [explanation strings]).
    """
    w       = weights or WEIGHTS
    s       = sym.get("summary",      {})
    reg     = sym.get("regression",   {})
    dd      = sym.get("drawdown",     {})
    entry   = sym.get("entry",        {})
    mc      = sym.get("montecarlo",   {})

    total  = 0.0
    notes  = []

    # ── 1. Sharpe (0–20) ──────────────────────────────────────────────────────
    sharpe = s.get("sharpe")
    if sharpe is not None:
        pts = min(float(sharpe) / 2.0, 1.0) * w["sharpe"]
        pts = max(pts, 0)
        total += pts
        notes.append(f"sharpe={sharpe:.2f} → {pts:.1f}/{WEIGHTS['sharpe']}")

    # ── 2. Calmar (0–20) ─────────────────────────────────────────────────────
    calmar = dd.get("calmar")
    if calmar is not None:
        pts = min(float(calmar) / 20.0, 1.0) * w["calmar"]
        pts = max(pts, 0)
        total += pts
        notes.append(f"calmar={calmar:.2f} → {pts:.1f}/{WEIGHTS['calmar']}")

    # ── 3. R² (0–10) ─────────────────────────────────────────────────────────
    r2 = reg.get("r2")
    if r2 is not None:
        pts = float(r2) * w["r2"]
        total += pts
        notes.append(f"R²={r2:.3f} → {pts:.1f}/{WEIGHTS['r2']}")

    # ── 4. Annualised trend (0–10) ────────────────────────────────────────────
    ann_trend = reg.get("ann_trend")
    if ann_trend is not None:
        pts = min(max(float(ann_trend), 0) / 50.0, 1.0) * w["ann_trend"]
        total += pts
        notes.append(f"trend={ann_trend:.1f}%/yr → {pts:.1f}/{WEIGHTS['ann_trend']}")

    # ── 5. RSI entry (0–15) ───────────────────────────────────────────────────
    # Best score near RSI=40 (mildly oversold, not extreme)
    # Falls off toward 0 (extreme oversold) and 70+ (overbought)
    rsi = entry.get("rsi14")
    if rsi is not None:
        if rsi <= 30:
            # Oversold but could keep falling — good but uncertain
            rsi_score = 0.75
        elif rsi <= 50:
            # Sweet spot: recovering from oversold
            rsi_score = 1.0 - abs(rsi - 40) / 40
            rsi_score = max(rsi_score, 0.5)
        elif rsi <= 60:
            # Neutral — fine
            rsi_score = 0.5
        elif rsi <= 70:
            # Getting extended
            rsi_score = 0.25
        else:
            # Overbought — poor entry
            rsi_score = 0.0
        pts = rsi_score * w["rsi_entry"]
        total += pts
        notes.append(f"RSI={rsi:.1f} → {pts:.1f}/{WEIGHTS['rsi_entry']}")

    # ── 6. %B entry (0–15) ───────────────────────────────────────────────────
    # Best score near 0 (lower band) or below 0 (below band = rare entry)
    # Worst near 1.0+ (upper band)
    pb = entry.get("pct_b")
    if pb is not None:
        if pb < 0:
            # Below lower band — rare, high-quality entry for trending asset
            pb_score = 1.0
        elif pb <= 0.2:
            pb_score = 0.9
        elif pb <= 0.4:
            pb_score = 0.7
        elif pb <= 0.6:
            pb_score = 0.4
        elif pb <= 0.8:
            pb_score = 0.2
        else:
            pb_score = 0.0
        pts = pb_score * w["pct_b_entry"]
        total += pts
        notes.append(f"%B={pb:.2f} → {pts:.1f}/{WEIGHTS['pct_b_entry']}")

    # ── 7. Monte Carlo prob > S0 (0–10) ──────────────────────────────────────
    prob = mc.get("prob_gain")
    if prob is not None:
        pts = (float(prob) / 100.0) * w["prob_gain"]
        total += pts
        notes.append(f"prob_gain={prob:.1f}% → {pts:.1f}/{WEIGHTS['prob_gain']}")

    # ── 8. Momentum (12-1 blended with 3m) ────────────────────────────────────
    mom = sym.get("momentum", {})
    m12 = mom.get("mom_12_1")
    m3  = mom.get("mom_3m")
    if (m12 is not None or m3 is not None) and w.get("momentum"):
        def _norm(v, lo, hi):
            return min(max((float(v) - lo) / (hi - lo), 0.0), 1.0)
        parts = []
        if m12 is not None:
            parts.append((0.6, _norm(m12, -30.0, 60.0)))
        if m3 is not None:
            parts.append((0.4, _norm(m3, -15.0, 30.0)))
        blend = sum(wt * v for wt, v in parts) / sum(wt for wt, _ in parts)
        pts = blend * w["momentum"]
        total += pts
        label = " ".join(
            ([f"12-1={m12:+.1f}%"] if m12 is not None else [])
            + ([f"3m={m3:+.1f}%"] if m3 is not None else []))
        notes.append(f"momentum {label} → {pts:.1f}/{w['momentum']}")

    # ── 9. Hurst persistence (trend trustworthiness) ─────────────────────────
    hurst = sym.get("hurst", {}).get("hurst")
    if hurst is not None and w.get("hurst"):
        # H 0.40 → 0 pts, 0.65 → full points; random walk lands mid-scale
        pts = min(max((float(hurst) - 0.40) / 0.25, 0.0), 1.0) * w["hurst"]
        total += pts
        notes.append(f"hurst={hurst:.3f} ({sym['hurst'].get('regime','?')}) "
                     f"→ {pts:.1f}/{w['hurst']}")

    # ── PENALTIES ─────────────────────────────────────────────────────────────

    n_bars = s.get("n_bars", 999)
    if n_bars < min_bars:
        total += PENALTY["thin_data"]
        notes.append(f"PENALTY: thin data ({n_bars} bars, need {min_bars}) → {PENALTY['thin_data']}")

    ann_vol = s.get("ann_vol")
    if ann_vol is not None and ann_vol > 50:
        total += PENALTY["high_vol"]
        notes.append(f"PENALTY: high vol ({ann_vol:.1f}%) → {PENALTY['high_vol']}")

    max_dd = dd.get("max_dd")
    if max_dd is not None and max_dd < -40:
        total += PENALTY["deep_dd"]
        notes.append(f"PENALTY: deep drawdown ({max_dd:.1f}%) → {PENALTY['deep_dd']}")

    recovered = dd.get("recovered")
    if recovered is False:
        total += PENALTY["unrecovered_dd"]
        notes.append(f"PENALTY: unrecovered drawdown → {PENALTY['unrecovered_dd']}")

    total = max(total, 0.0)
    return round(total, 1), notes


# ─────────────────────────────────────────────
#  OUTPUT HELPERS
# ─────────────────────────────────────────────

def _bar(score: float, width: int = 20) -> str:
    filled = int(round(score / MAX_SCORE * width))
    return "█" * filled + "░" * (width - filled)

def _tbl(rows: list[list], headers: list[str]):
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
#  MAIN
# ─────────────────────────────────────────────

def _main():
    parser = argparse.ArgumentParser(
        description="Rank symbols by investment score (all 7 analysis steps)",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""
examples:
  python3 stock_score.py
  python3 stock_score.py --horizon week
  python3 stock_score.py --horizon month
  python3 stock_score.py --horizon year
  python3 stock_score.py --horizon life
  python3 stock_score.py -s AAPL MSFT GOOGL CSMIB.MI TSLA --horizon quarter
  python3 stock_score.py --from 2023-01-01 --top 3 --detail
  python3 stock_score.py --horizon life --json
        """
    )
    parser.add_argument("--horizon", default="quarter",
                        choices=list(HORIZON_PROFILES),
                        help=(
                            "Investment horizon — reshapes weights, granularity, and MC horizon:\n"
                            "  week     next 5 trading days   (entry timing 70%%)\n"
                            "  month    next 21 trading days  (entry timing 50%%)\n"
                            "  quarter  next 63 days          (balanced, default)\n"
                            "  year     next 252 trading days (trend quality 60%%)\n"
                            "  life     5+ years buy-and-hold (trend + risk 95%%)"
                        ))

    parser.add_argument("-s", "--symbols", nargs="+", metavar="TICKER",
                        help="Symbols to score (default: all available)")
    parser.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD",
                        help="Start of date range for analysis")
    parser.add_argument("--to",   dest="date_to",   metavar="YYYY-MM-DD",
                        help="End of date range for analysis")
    parser.add_argument("--top",  type=int, default=None, metavar="N",
                        help="Show only the top N symbols")
    parser.add_argument("--mc-paths", type=int, default=500,
                        help="Monte Carlo paths (default: 500). "
                             "MC horizon is set automatically by --horizon.")
    parser.add_argument("--detail", action="store_true",
                        help="Show per-metric breakdown for each symbol")
    parser.add_argument("--fundamentals", action="store_true",
                        help="Fetch P/E and revenue growth via yfinance "
                             "(network call per symbol) and apply a ±5 point "
                             "valuation adjustment to the score")
    parser.add_argument("--json", action="store_true",
                        help="Output full results as JSON")
    args = parser.parse_args()

    profile  = HORIZON_PROFILES[args.horizon]
    h_w      = profile["weights"]
    h_gran   = profile["gran"]
    h_mc     = profile["mc_bars"]
    h_minb   = profile["min_bars"]
    h_af     = profile["ann_factor"]

    symbols = args.symbols if args.symbols else list_all_symbols()
    if not symbols:
        print("[error] No symbols found. Run stock_collector.py first.")
        sys.exit(1)

    print(f"Horizon:  {args.horizon.upper()}  —  {profile['label']}")
    print(f"Weights:  entry {h_w['rsi_entry']+h_w['pct_b_entry']}pts  "
          f"trend {h_w['r2']+h_w['ann_trend']}pts  "
          f"risk-adj {h_w['sharpe']+h_w['calmar']}pts  "
          f"MC {h_w['prob_gain']}pts  "
          f"momentum {h_w['momentum']}pts  hurst {h_w['hurst']}pts")
    print(f"Scoring:  {len(symbols)} symbol(s)  "
          f"[{args.date_from or 'all'} → {args.date_to or 'now'}, "
          f"MC {args.mc_paths}p × {h_mc}b, gran={h_gran}]")
    print()

    results = []

    for sym in symbols:
        sys.stdout.write(f"  {sym:<12} computing... \r")
        sys.stdout.flush()

        df = load_prices(sym, args.date_from, args.date_to)
        if df.empty or len(df) < 10:
            continue

        # resample to horizon granularity
        try:
            df_r = df.set_index("timestamp").resample(h_gran).agg({
                "open":"first","high":"max","low":"min",
                "close":"last","volume":"sum",
            }).dropna(subset=["close"]).reset_index()
        except Exception:
            fb = {"ME":"M","QE":"Q","YE":"Y"}.get(h_gran, h_gran)
            df_r = df.set_index("timestamp").resample(fb).agg({
                "open":"first","high":"max","low":"min",
                "close":"last","volume":"sum",
            }).dropna(subset=["close"]).reset_index()

        if len(df_r) < 10:
            continue

        raw = {
            "symbol":     sym,
            "horizon":    args.horizon,
            "summary":    step_summary(df_r, ann_factor=h_af),
            "regression": step_regression(df_r),
            "drawdown":   step_drawdown(df_r),
            "entry":      step_entry_timing(df_r),
            "montecarlo": step_montecarlo(df_r, args.mc_paths, h_mc),
            "momentum":   step_momentum(df),   # daily bars, not resampled
            "hurst":      step_hurst(df),      # daily bars, not resampled
        }

        score, notes = score_symbol(raw, weights=h_w, min_bars=h_minb)
        raw["score"]   = score
        raw["notes"]   = notes
        results.append(raw)

    # clear progress line
    sys.stdout.write(" " * 40 + "\r")

    # ── optional valuation adjustment (network: yfinance) ─────────────────────
    if args.fundamentals and results:
        from stock_toolkit.fundamentals import (
            fetch_fundamentals, valuation_adjustment,
        )
        print("Fetching fundamentals via yfinance (P/E, revenue growth)…")
        funda = fetch_fundamentals([r["symbol"] for r in results])
        for r in results:
            row = funda.get(r["symbol"])
            if not row:
                continue
            delta, why = valuation_adjustment(row)
            r["valuation"] = {**row, "adjustment": delta}
            r["score"] = round(max(0.0, min(100.0, r["score"] + delta)), 1)
            r["notes"].append(f"VALUATION: {why} → {delta:+.1f}")

    results.sort(key=lambda x: x["score"], reverse=True)

    if args.top:
        results = results[:args.top]

    # ── JSON output ───────────────────────────────────────────────────────────
    if args.json:
        print(json.dumps(results, indent=2, default=str))
        return

    # ── ranked table ──────────────────────────────────────────────────────────
    rows  = []
    for rank, r in enumerate(results, 1):
        s  = r.get("summary",    {})
        dd = r.get("drawdown",   {})
        en = r.get("entry",      {})
        mc = r.get("montecarlo", {})

        rsi_val = en.get("rsi14")
        pb_val  = en.get("pct_b")
        squeeze = "⚡" if en.get("bbands_squeeze") else ""

        rows.append([
            str(rank),
            r["symbol"],
            f"{r['score']:.1f}  {_bar(r['score'])}",
            f"{s.get('sharpe',   '—'):.2f}"  if s.get("sharpe")  is not None else "—",
            f"{dd.get('calmar',  '—'):.2f}"  if dd.get("calmar") is not None else "—",
            f"{s.get('ann_vol',  '—'):.1f}%" if s.get("ann_vol") is not None else "—",
            f"{dd.get('max_dd',  '—'):.1f}%" if dd.get("max_dd") is not None else "—",
            f"{rsi_val:.0f}"  if rsi_val is not None else "—",
            f"{pb_val:.2f}{squeeze}" if pb_val is not None else "—",
            f"{mc.get('prob_gain','—'):.0f}%" if mc.get("prob_gain") is not None else "—",
        ])

    _tbl(rows, ["#", "Symbol", f"Score /100  {'bar':^20}",
                "Sharpe", "Calmar", "Vol", "Max DD",
                "RSI", "%B", "P(gain)"])

    # ── detail breakdown ──────────────────────────────────────────────────────
    if args.detail:
        for r in results:
            print(f"\n  {r['symbol']}  ({r['score']:.1f}/100)")
            for note in r["notes"]:
                flag = "  ✓" if "PENALTY" not in note else "  ✗"
                print(f"    {flag}  {note}")

    # ── top pick summary ──────────────────────────────────────────────────────
    if results:
        top   = results[0]
        print(f"\n  Top pick:  {top['symbol']}  ({top['score']:.1f}/100)")

        mc   = top.get("montecarlo", {})
        dd   = top.get("drawdown",   {})
        en   = top.get("entry",      {})
        s    = top.get("summary",    {})

        if mc.get("prob_gain"):
            print(f"  MC:        {mc['prob_gain']:.0f}% prob of gain  |  "
                  f"P50={mc['p50']:.2f}  P5={mc['p5']:.2f}")
        if en.get("rsi14"):
            pb_str = f"  %B={en['pct_b']:.2f}" if en.get("pct_b") is not None else ""
            print(f"  Entry:     RSI={en['rsi14']:.0f}{pb_str}"
                  + ("  ⚡ squeeze" if en.get("bbands_squeeze") else ""))
        if dd.get("max_dd"):
            rec = "recovered" if dd.get("recovered") else "NOT YET RECOVERED"
            print(f"  Risk:      max DD={dd['max_dd']:.1f}%  Calmar={dd['calmar']:.2f}  ({rec})")

        # best pair: top pick + lowest-correlation partner
        if len(results) >= 2:
            # heuristic: pick the highest-scoring symbol from a different
            # "family" — US tech vs European, based on exchange suffix
            top_sym    = top["symbol"]
            top_eu     = "." in top_sym
            candidates = [r for r in results[1:]
                          if ("." in r["symbol"]) != top_eu]
            if candidates:
                pair = candidates[0]
                print(f"\n  Best pair: {top_sym} + {pair['symbol']}  "
                      f"(scores: {top['score']:.1f} + {pair['score']:.1f})")
                print("  Rationale: different market families → likely low correlation")

    print()
    print("  ⚠  Score is a data summary, not investment advice.")
    print("     Always run the full 7-step analysis before investing.\n")


def main():
    try:
        _main()
    except NoDataError as e:
        print(f"[error] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
