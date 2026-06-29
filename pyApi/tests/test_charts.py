"""
test_charts.py
=============
Coverage-focused tests for stock_toolkit/ui/charts.py — calls every
Plotly/figure builder with realistic fixture-derived DataFrames and
asserts a figure (or table) comes back. No Streamlit, no browser.
"""
import os
import pathlib
import sys
import unittest

os.environ.setdefault("MPLBACKEND", "Agg")

SCRIPT_DIR = pathlib.Path(__file__).parent
PKG_ROOT   = SCRIPT_DIR.parent
sys.path.insert(0, str(PKG_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from test_toolkit import FixtureTestCase, _load_module  # noqa: E402


class TestCharts(FixtureTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sb = _load_module("backtest", cls.db, cls.tmp_dir)
        cls.df_aapl = sb.load_prices("AAPL", "2022-01-01", None, source=None)
        cls.df_msft = sb.load_prices("MSFT", "2022-01-01", None, source=None)
        cls.dfs = {"AAPL": cls.df_aapl, "MSFT": cls.df_msft}
        from stock_toolkit.ui import charts
        cls.charts = charts
        import plotly.graph_objects as go
        cls.go = go

    def _assert_fig(self, fig):
        self.assertIsInstance(fig, self.go.Figure)

    def test_price_chart(self):
        self._assert_fig(self.charts.price_chart(self.df_aapl, "AAPL"))

    def test_score_bar_chart(self):
        results = [{"symbol": "AAPL", "score": 82.0},
                   {"symbol": "MSFT", "score": 41.5},
                   {"symbol": "ENEL.MI", "score": 12.0}]
        self._assert_fig(self.charts.score_bar_chart(results))

    def test_equity_chart(self):
        import numpy as np
        dates  = self.df_aapl["timestamp"].values
        equity = np.linspace(10_000, 13_000, len(dates))
        bh     = np.linspace(10_000, 12_000, len(dates))
        self._assert_fig(self.charts.equity_chart(dates, equity, bh))

    def test_drawdown_chart(self):
        self._assert_fig(self.charts.drawdown_chart(self.df_aapl))

    def test_rsi_chart(self):
        self._assert_fig(self.charts.rsi_chart(self.df_aapl, 14))

    def test_bbands_chart(self):
        self._assert_fig(self.charts.bbands_chart(self.df_aapl, 20))

    def test_mc_chart(self):
        self._assert_fig(self.charts.mc_chart(self.df_aapl, n_paths=200,
                                              horizon=42))

    def test_price_compare_chart(self):
        self._assert_fig(self.charts.price_compare_chart(self.dfs))

    def test_drawdown_compare_chart(self):
        self._assert_fig(self.charts.drawdown_compare_chart(self.dfs))

    def test_correlation_heatmap(self):
        self._assert_fig(self.charts.correlation_heatmap(self.dfs))

    def test_summary_table(self):
        import pandas as pd
        tbl = self.charts.summary_table(self.dfs)
        self.assertIsInstance(tbl, pd.DataFrame)
        self.assertFalse(tbl.empty)


if __name__ == "__main__":
    unittest.main()
