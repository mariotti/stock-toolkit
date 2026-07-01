"""Shared data helpers and formatting for the dashboard."""


import pandas as pd
import streamlit as st

from stock_toolkit import score as ss
from stock_toolkit.common import CONFIG_PATH, load_config

_cfg = load_config(CONFIG_PATH)


def reload_config() -> None:
    """Re-read config.env into the module-level ``_cfg`` dict in place.

    Other modules import _cfg as a dict object, so we mutate it
    rather than rebinding — that way the new values reach every
    consumer (briefing.py, etc.) without a Streamlit restart.
    """
    _cfg.clear()
    _cfg.update(load_config(CONFIG_PATH))

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


@st.cache_data(ttl=300)
def get_data_date_range():
    """(min_date, max_date) of daily bars across every price DB.

    Used to bound the sidebar calendar so a user can't pick an empty
    period. Returns (None, None) when there's no data on disk.
    """
    import sqlite3

    lo = hi = None
    try:
        dbs = ss.discover_dbs()
    except Exception:
        return (None, None)
    for db in dbs:
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            row = con.execute(
                "SELECT MIN(timestamp), MAX(timestamp) FROM prices"
            ).fetchone()
            con.close()
        except Exception:
            continue
        if row and row[0] and row[1]:
            d0 = pd.to_datetime(row[0], utc=True, errors="coerce")
            d1 = pd.to_datetime(row[1], utc=True, errors="coerce")
            if pd.isna(d0) or pd.isna(d1):
                continue
            d0, d1 = d0.date(), d1.date()
            lo = d0 if lo is None else min(lo, d0)
            hi = d1 if hi is None else max(hi, d1)
    return (lo, hi)


@st.cache_data(ttl=3600, show_spinner=False)
def get_fundamentals(symbols: tuple) -> dict:
    """Cached wrapper around stock_toolkit.fundamentals.fetch_fundamentals."""
    from stock_toolkit.fundamentals import fetch_fundamentals
    return fetch_fundamentals(symbols)


@st.cache_data(ttl=3600, show_spinner=False)
def get_news_sentiment(symbols: tuple, api_key: str) -> dict:
    """Cached wrapper around stock_toolkit.news.fetch_news_sentiment.

    1-hour TTL matches get_fundamentals — protects the shared Alpha
    Vantage 25-calls/day budget when a user clicks Generate-briefing
    multiple times in quick succession. The api_key is part of the
    cache key so swapping it in Admin → API Keys invalidates correctly.
    """
    if not (api_key or "").strip() or not symbols:
        return {}
    from stock_toolkit.news import fetch_news_sentiment
    return fetch_news_sentiment(symbols, api_key)


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


