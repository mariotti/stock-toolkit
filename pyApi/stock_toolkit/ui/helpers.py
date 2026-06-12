"""Shared data helpers and formatting for the dashboard."""


import pandas as pd
import streamlit as st

from stock_toolkit import score as ss
from stock_toolkit.common import CONFIG_PATH, load_config

_cfg = load_config(CONFIG_PATH)

# ─────────────────────────────────────────────
#  SHARED STATE & HELPERS
# ─────────────────────────────────────────────

@st.cache_data(ttl=300)
def get_all_symbols():
    try:
        return ss.list_all_symbols()
    except Exception:
        return []


@st.cache_data(ttl=300)
def get_prices(symbol, date_from, date_to):
    try:
        return ss.load_prices(symbol, date_from or None, date_to or None)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def get_fundamentals(symbols: tuple) -> dict:
    """Per-symbol valuation snapshot via yfinance (no API key needed).

    Returns {symbol: {trailing_pe, forward_pe, revenue_growth,
    earnings_growth}} — growth values are fractions (0.166 = +16.6% YoY).
    Missing fields are None; symbols with no data at all are omitted.
    """
    try:
        import yfinance as yf
    except ImportError:
        return {}
    out = {}
    for sym in symbols:
        try:
            info = yf.Ticker(sym).info or {}
        except Exception:
            continue
        row = {
            "trailing_pe":     info.get("trailingPE"),
            "forward_pe":      info.get("forwardPE"),
            "revenue_growth":  info.get("revenueGrowth"),
            "earnings_growth": info.get("earningsGrowth"),
        }
        if any(v is not None for v in row.values()):
            out[sym] = row
    return out


def score_color(score):
    if score >= 60:   return "#4ade80"
    if score >= 40:   return "#fbbf24"
    return "#f87171"


def fmt_pct(v, decimals=1):
    if v is None: return "—"
    return f"{v:+.{decimals}f}%"


def fmt_val(v, decimals=2):
    if v is None: return "—"
    return f"{v:.{decimals}f}"


