"""
Self-validation for the investment score (stock_toolkit.score).

The score ranks symbols 0–100, but a ranking is only worth acting on if a
*higher score actually precedes a higher forward return*. This module
answers that empirically with a point-in-time walk-forward test:

  for each rebalance date T, for each symbol:
     - score it using ONLY data <= T  (the real score pipeline)
     - measure the actual forward return over the horizon (T → T+H)
  then report:
     - Information Coefficient (per-date Spearman rank corr, averaged)
     - mean forward return by score tercile (cross-sectionally demeaned)
     - a plain-English verdict

There is no look-ahead: scoring uses `score.load_prices(sym, from, to=T)`,
which filters to `timestamp <= T`; forward returns come from prices strictly
after T. Because the fixture data is a random walk, the IC there is ~0 by
construction — this module tests the *machinery*; the *verdict* only means
something on real data.

Public API is in ``__all__``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from stock_toolkit import score as ss

__all__ = ["FORWARD_BARS", "run_score_backtest", "verdict_for_ic"]

# Trading-day forward window per horizon (how far ahead the score claims to see).
FORWARD_BARS = {
    "week": 5, "month": 21, "quarter": 63, "year": 252, "life": 252,
}


def verdict_for_ic(mean_ic: float, tstat: float, n_dates: int) -> str:
    """Plain-English, statistically honest reading of the IC.

    A big-looking mean IC is meaningless if it isn't distinguishable from
    zero, so significance (|t| > 2 across rebalance dates) gates the claim
    of a signal — not the raw mean. This is the whole point of the tool:
    it must not overclaim.
    """
    if n_dates < 8:
        return ("Too few rebalance dates to judge reliably — widen the date "
                "span or add symbols.")
    if abs(tstat) < 2.0:
        return (f"**No statistically reliable signal.** The Information "
                f"Coefficient ({mean_ic:+.3f}) is not distinguishable from "
                f"zero (t={tstat:+.2f}) — the score did not rank future "
                f"returns better than chance over this test.")
    a = abs(mean_ic)
    grade = "Weak" if a < 0.05 else "Modest" if a < 0.10 else "Strong"
    wrong = (" — and it points the **wrong way**: higher scores preceded "
             "*lower* returns" if mean_ic < 0 else "")
    return (f"**{grade}, statistically significant signal** "
            f"(IC {mean_ic:+.3f}, t={tstat:+.2f}){wrong}.")


def run_score_backtest(
    symbols: list[str],
    horizon: str = "quarter",
    *,
    start: str | None = None,
    end: str | None = None,
    rebalance_months: int = 6,
    lookback_years: int = 5,
    mc_paths: int = 200,
    forward_bars: int | None = None,
) -> dict:
    """Walk-forward predictive-value test of the score.

    Returns a dict with IC stats, tercile forward returns, the raw
    observations, and a verdict. ``n_obs == 0`` means not enough data.
    """
    if horizon not in ss.HORIZON_PROFILES:
        raise ValueError(f"unknown horizon {horizon!r}")
    prof = ss.HORIZON_PROFILES[horizon]
    gran, weights, min_bars = prof["gran"], prof["weights"], prof["min_bars"]
    fwd_bars = forward_bars or FORWARD_BARS.get(horizon, 63)

    symbols = [s.upper() for s in symbols]

    # Full daily close per symbol → forward returns. discover_dbs() gives the
    # same DBs the score itself reads (live + historical).
    full: dict[str, pd.Series] = {}
    for s in symbols:
        d = ss.load_prices(s, None, None)
        if not d.empty:
            full[s] = d.set_index("timestamp")["close"].sort_index()
    if not full:
        return _empty_result(horizon, fwd_bars)

    # Rebalance grid, bounded to where the data actually is.
    data_lo = min(ser.index.min() for ser in full.values())
    data_hi = max(ser.index.max() for ser in full.values())
    first = pd.Timestamp(start, tz="UTC") if start else \
        data_lo + pd.DateOffset(years=lookback_years)
    last = pd.Timestamp(end, tz="UTC") if end else data_hi
    dates = []
    t = max(first, data_lo + pd.DateOffset(years=lookback_years))
    while t <= last:
        dates.append(t)
        t += pd.DateOffset(months=rebalance_months)

    rows = []
    for T in dates:
        frm = (T - pd.DateOffset(years=lookback_years)).strftime("%Y-%m-%d")
        to = T.strftime("%Y-%m-%d")
        for s in symbols:
            ser = full.get(s)
            if ser is None:
                continue
            df = ss.load_prices(s, frm, to)
            if df.empty or len(df) < 10:
                continue
            df_r = (df.set_index("timestamp").resample(gran)
                    .agg({"open": "first", "high": "max", "low": "min",
                          "close": "last", "volume": "sum"})
                    .dropna(subset=["close"]).reset_index())
            if len(df_r) < 10:
                continue
            raw = {
                "summary":    ss.step_summary(df_r, ann_factor=prof["ann_factor"]),
                "regression": ss.step_regression(df_r),
                "drawdown":   ss.step_drawdown(df_r),
                "entry":      ss.step_entry_timing(df_r),
                "montecarlo": ss.step_montecarlo(df_r, mc_paths, prof["mc_bars"]),
            }
            score, _ = ss.score_symbol(raw, weights=weights, min_bars=min_bars)

            past = ser[ser.index <= T]
            fut = ser[ser.index > T]
            if past.empty or len(fut) < fwd_bars:
                continue
            fwd = float(fut.iloc[fwd_bars - 1] / past.iloc[-1] - 1.0)
            rows.append({"date": T, "symbol": s, "score": float(score),
                         "forward_return": fwd})

    obs = pd.DataFrame(rows)
    if obs.empty:
        return _empty_result(horizon, fwd_bars)

    # Information Coefficient: per-date Spearman(score, forward), averaged.
    ics = []
    for _, g in obs.groupby("date"):
        if len(g) >= 3 and g["score"].nunique() > 1:
            ic = g["score"].corr(g["forward_return"], method="spearman")
            if pd.notna(ic):
                ics.append(ic)
    ics = np.array(ics, dtype=float)
    mean_ic = float(ics.mean()) if len(ics) else 0.0
    tstat = (float(mean_ic / (ics.std(ddof=1) / np.sqrt(len(ics))))
             if len(ics) > 1 and ics.std(ddof=1) > 0 else 0.0)

    # Terciles: pooled, on returns demeaned per date (cross-sectional).
    obs["fwd_rel"] = obs.groupby("date")["forward_return"].transform(
        lambda x: x - x.mean())
    terc = {"low": None, "mid": None, "high": None}
    if obs["score"].nunique() >= 3 and len(obs) >= 6:
        try:
            labels = pd.qcut(obs["score"].rank(method="first"), 3,
                             labels=["low", "mid", "high"])
            for name in terc:
                sub = obs.loc[labels == name, "fwd_rel"]
                if len(sub):
                    terc[name] = float(sub.mean())
        except ValueError:
            pass
    hml = (terc["high"] - terc["low"]
           if terc["high"] is not None and terc["low"] is not None else None)

    return {
        "horizon":        horizon,
        "forward_bars":   fwd_bars,
        "n_obs":          int(len(obs)),
        "n_dates":        int(obs["date"].nunique()),
        "n_symbols":      int(obs["symbol"].nunique()),
        "n_ic_dates":     int(len(ics)),
        "mean_ic":        mean_ic,
        "median_ic":      float(np.median(ics)) if len(ics) else 0.0,
        "pct_positive_ic": float((ics > 0).mean() * 100) if len(ics) else 0.0,
        "ic_tstat":       tstat,
        "tercile_returns": terc,
        "high_minus_low": hml,
        "observations":   obs,
        "verdict":        verdict_for_ic(mean_ic, tstat, len(ics)),
    }


def _empty_result(horizon: str, fwd_bars: int) -> dict:
    return {
        "horizon": horizon, "forward_bars": fwd_bars,
        "n_obs": 0, "n_dates": 0, "n_symbols": 0, "n_ic_dates": 0,
        "mean_ic": 0.0, "median_ic": 0.0, "pct_positive_ic": 0.0,
        "ic_tstat": 0.0,
        "tercile_returns": {"low": None, "mid": None, "high": None},
        "high_minus_low": None, "observations": pd.DataFrame(),
        "verdict": "Not enough history to test the score over this window.",
    }
