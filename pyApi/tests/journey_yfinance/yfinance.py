"""
Test fixture: deterministic fake yfinance for subprocess journey tests.

Injected via PYTHONPATH so that `stock-bootstrap` running as a real
subprocess imports this module instead of the real yfinance — no network,
no Yahoo scrape, deterministic output. Mirrors the real-yfinance API
surface used by stock_toolkit.collector.sources.yfinance.
"""

import datetime

import numpy as np
import pandas as pd


def _series(symbol: str, n: int = 252 * 5) -> pd.DataFrame:
    """Synthetic OHLCV: deterministic per-symbol geometric drift."""
    end = datetime.date.today()
    dates = pd.bdate_range(end=end, periods=n, tz="UTC")
    rng = np.random.default_rng(sum(ord(c) for c in symbol))
    rets = np.cumsum(rng.normal(0.0003, 0.015, len(dates)))
    close = 100 * np.exp(rets)
    return pd.DataFrame(
        {
            "Open":   close * 0.995,
            "High":   close * 1.010,
            "Low":    close * 0.990,
            "Close":  close,
            "Volume": np.full(len(dates), 1_000_000),
        },
        index=dates,
    )


class Ticker:
    """Drop-in for yfinance.Ticker for the subset our collector uses.

    Magic symbol names recognised:
      "NODATA*"  → history() returns an empty DataFrame, so the
                   collector records a per-source failure into
                   stock_failures.db. Used by the collector-failures
                   journey test.
    """

    def __init__(self, symbol):
        self.symbol = symbol
        if symbol.startswith("NODATA"):
            self._history = pd.DataFrame()
        else:
            self._history = _series(symbol)

    def history(self, start=None, end=None, period=None, interval="1d"):
        df = self._history
        if df.empty:
            return df
        if start:
            df = df[df.index >= pd.Timestamp(start, tz="UTC")]
        if end:
            df = df[df.index <= pd.Timestamp(end, tz="UTC")]
        return df

    @property
    def info(self):
        # The fundamentals path isn't exercised by stock-bootstrap; return
        # an empty dict so any accidental access doesn't crash.
        return {}
