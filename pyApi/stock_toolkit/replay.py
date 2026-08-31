"""
Replay game engine — rewind the clock and bet on the next bar.

Pure logic, no Streamlit: the ⏪ Replay page keeps a game dict in
session state and calls these helpers. Nothing touches disk beyond
reading the price DBs — a replay session is deliberately ephemeral
(close the tab, the game is gone).

Anti-leak guarantee: everything that describes "today" is computed
from bars at integer position <= i in the closes panel; the future is
read only inside next_return / resolve_* / equal_weight_return, after
the bet is already placed.
"""

from __future__ import annotations

import pandas as pd

from stock_toolkit.score import _pct_b, _rsi, load_prices

__all__ = [
    "MIN_HISTORY", "LOOKBACK",
    "load_panel", "playable_positions", "window", "indicators",
    "next_return", "resolve_single", "resolve_portfolio",
    "equal_weight_return", "single_summary",
]

MIN_HISTORY = 60     # bars needed behind a start date (indicator warm-up)
LOOKBACK    = 60     # bars shown in the price window


def load_panel(symbols, date_to: str | None = None) -> pd.DataFrame:
    """Daily closes matrix: index = union of trading dates (ascending),
    one column per symbol, forward-filled so a symbol missing a bar
    carries its last close (its next-day move then scores as 0%).
    Leading NaNs remain for symbols that start trading later."""
    cols = {}
    for sym in symbols:
        try:
            df = load_prices(sym, None, date_to)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        s = pd.Series(df["close"].values,
                      index=pd.to_datetime(df["timestamp"]).dt.date)
        cols[sym] = s[~s.index.duplicated(keep="last")]
    if not cols:
        return pd.DataFrame()
    return pd.DataFrame(cols).sort_index().ffill()


def playable_positions(panel: pd.DataFrame) -> tuple[int, int]:
    """(first, last) integer positions a game may sit on: enough
    warm-up behind, at least one bar ahead. last < first when the
    panel is too short to play."""
    return MIN_HISTORY, len(panel) - 2


def window(panel: pd.DataFrame, sym: str, i: int,
           lookback: int = LOOKBACK) -> pd.Series:
    """sym's closes up to and including position i, newest last."""
    return panel[sym].iloc[:i + 1].dropna().tail(lookback)


def indicators(closes: pd.Series) -> dict:
    """Indicator snapshot of one symbol's series as of its last bar."""
    s = closes.dropna()
    out = {"price": float(s.iloc[-1]) if len(s) else None,
           "chg_pct": None, "rsi14": None, "pct_b": None,
           "sma20": None, "sma50": None}
    if len(s) >= 2:
        out["chg_pct"] = float((s.iloc[-1] / s.iloc[-2] - 1) * 100)
    if len(s) >= 15:
        out["rsi14"] = _rsi(s)
    if len(s) >= 20:
        out["pct_b"] = _pct_b(s)
        out["sma20"] = float(s.rolling(20).mean().iloc[-1])
    if len(s) >= 50:
        out["sma50"] = float(s.rolling(50).mean().iloc[-1])
    return out


def next_return(panel: pd.DataFrame, sym: str, i: int) -> float | None:
    """Percent move of sym from position i to i+1, or None when either
    bar is missing (symbol not yet trading, or i is the last bar)."""
    if i + 1 >= len(panel):
        return None
    today, nxt = panel[sym].iloc[i], panel[sym].iloc[i + 1]
    if pd.isna(today) or pd.isna(nxt) or today == 0:
        return None
    return float((nxt / today - 1) * 100)


def resolve_single(panel: pd.DataFrame, sym: str, i: int,
                   direction: str, stake: float) -> dict | None:
    """Score a Higher/Lower call on sym's next bar. direction is 'up'
    or 'down'. Returns None when there is no next bar to bet on."""
    ret = next_return(panel, sym, i)
    if ret is None:
        return None
    signed = ret if direction == "up" else -ret
    return {
        "date": panel.index[i], "next_date": panel.index[i + 1],
        "symbol": sym, "direction": direction, "ret_pct": ret,
        "gain": stake * signed / 100.0,
        "outcome": ("push" if ret == 0
                    else "hit" if signed > 0 else "miss"),
    }


def resolve_portfolio(panel: pd.DataFrame, weights: dict[str, float],
                      i: int) -> dict | None:
    """Apply next-day returns to an allocation. weights are percents
    (0-100) of the pot; the un-allocated remainder sits in cash at 0%.
    Returns None when the panel has no next bar."""
    if i + 1 >= len(panel):
        return None
    per, total = {}, 0.0
    for sym, w in weights.items():
        if w <= 0:
            continue
        ret = next_return(panel, sym, i) or 0.0
        per[sym] = ret
        total += (w / 100.0) * ret
    return {
        "date": panel.index[i], "next_date": panel.index[i + 1],
        "weights": {s: w for s, w in weights.items() if w > 0},
        "per_symbol": per, "ret_pct": total,
    }


def equal_weight_return(panel: pd.DataFrame, i: int) -> float:
    """Benchmark move: next-day return of an equal-weight basket of
    every symbol tradable at position i (fully invested, no cash)."""
    rets = [r for r in (next_return(panel, sym, i)
                        for sym in panel.columns) if r is not None]
    return sum(rets) / len(rets) if rets else 0.0


def single_summary(rounds: list[dict]) -> dict:
    """Aggregate a single-symbol session: hit/miss/push counts, hit
    rate over decided rounds, and total stake gain."""
    hits = sum(1 for r in rounds if r["outcome"] == "hit")
    misses = sum(1 for r in rounds if r["outcome"] == "miss")
    pushes = sum(1 for r in rounds if r["outcome"] == "push")
    decided = hits + misses
    return {
        "rounds": len(rounds), "hits": hits, "misses": misses,
        "pushes": pushes,
        "hit_rate": hits / decided if decided else 0.0,
        "total_gain": sum(r["gain"] for r in rounds),
    }
