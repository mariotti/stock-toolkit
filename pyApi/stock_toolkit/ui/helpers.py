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


