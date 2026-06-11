"""Per-source fetchers. Each module provides fetch_<name>() and, where the
API supports it, _hist_<name>() for historical backfills."""

from .yfinance import fetch_yfinance, _hist_yfinance
from .alphavantage import fetch_alphavantage, _hist_alphavantage
from .finnhub import fetch_finnhub, _hist_finnhub
from .polygon import fetch_polygon, _hist_polygon
from .fmp import fetch_fmp, _hist_fmp
from .twelvedata import fetch_twelvedata, _hist_twelvedata
from .marketstack import fetch_marketstack

LIVE_FETCHERS = {
    "yfinance":     fetch_yfinance,
    "alphavantage": fetch_alphavantage,
    "finnhub":      fetch_finnhub,
    "polygon":      fetch_polygon,
    "fmp":          fetch_fmp,
    "twelvedata":   fetch_twelvedata,
    "marketstack":  fetch_marketstack,
}

HIST_FETCHERS = {
    "yfinance":     _hist_yfinance,
    "alphavantage": _hist_alphavantage,
    "finnhub":      _hist_finnhub,
    "polygon":      _hist_polygon,
    "fmp":          _hist_fmp,
    "twelvedata":   _hist_twelvedata,
}
