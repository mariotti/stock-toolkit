"""
test_backtest_cli.py
====================
Coverage-focused tests for stock_toolkit/backtest.py — drives the CLI
main() across every strategy (with --plot, --show-trades, walk-forward
--test-from, multi-symbol) and exercises the print/plot helpers
directly. Offline against the shared synthetic fixture DB.
"""
import contextlib
import io
import os
import pathlib
import sys
import unittest
import warnings

os.environ.setdefault("MPLBACKEND", "Agg")

SCRIPT_DIR = pathlib.Path(__file__).parent
PKG_ROOT   = SCRIPT_DIR.parent
sys.path.insert(0, str(PKG_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from test_toolkit import FixtureTestCase, _load_module  # noqa: E402


class BacktestBase(FixtureTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sb = _load_module("backtest", cls.db, cls.tmp_dir)
        cls.df = cls.sb.load_prices("AAPL", "2022-01-01", None, source=None)

    def _cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            old = sys.argv
            sys.argv = ["stock-backtest", *args]
            try:
                self.sb.main()
            finally:
                sys.argv = old
        import matplotlib.pyplot as plt
        plt.close("all")


class TestBacktestCli(BacktestBase):

    def test_each_strategy(self):
        for strat in ("rsi", "sma_cross", "bbands", "breakout", "macd"):
            with self.subTest(strategy=strat):
                self._cli("-s", "AAPL", "--strategy", strat)

    def test_plot_and_show_trades(self):
        self._cli("-s", "AAPL", "--strategy", "rsi", "--window", "14",
                  "--plot", "--show-trades")

    def test_multi_symbol(self):
        self._cli("-s", "AAPL", "MSFT", "--strategy", "sma_cross",
                  "--fast", "10", "--slow", "30")

    def test_walk_forward_split(self):
        self._cli("-s", "AAPL", "--strategy", "rsi",
                  "--from", "2022-01-01", "--test-from", "2023-06-01")

    def test_macd_custom_params(self):
        self._cli("-s", "AAPL", "--strategy", "macd",
                  "--macd-fast", "8", "--macd-slow", "21", "--macd-signal", "5")

    def test_custom_capital_commission(self):
        self._cli("-s", "AAPL", "--strategy", "bbands",
                  "--capital", "50000", "--commission", "0.002",
                  "--slippage", "0.0005")


class TestBacktestHelpers(BacktestBase):

    def setUp(self):
        bt = self.sb.Backtester(capital=10_000, commission=0.001, slippage=0.001)
        self.bt = bt
        self.sigs = self.sb.signals_rsi(self.df, 14, buy_at=30, sell_at=70)
        self.result = bt.run(self.df, self.sigs)
        import pandas as pd
        bh = pd.Series(0, index=self.df.index)
        bh.iloc[0] = 1
        self.bh = bt.run(self.df, bh)

    def _quiet(self, fn, *a, **k):
        with contextlib.redirect_stdout(io.StringIO()):
            return fn(*a, **k)

    def test_print_metrics(self):
        self._quiet(self.sb.print_metrics, "AAPL", "RSI(14)",
                    self.result["metrics"], self.bh["metrics"])

    def test_print_trades(self):
        self._quiet(self.sb.print_trades, self.result["trades"])

    def test_print_trades_empty(self):
        self._quiet(self.sb.print_trades, [])

    def test_plot_results(self):
        import matplotlib.pyplot as plt
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._quiet(self.sb.plot_results, "AAPL", self.df,
                        self.result, self.bh, "RSI(14)")
        plt.close("all")

    def test_signals_all_return_series(self):
        import pandas as pd
        for name, fn, args in [
            ("rsi",       self.sb.signals_rsi,       (self.df, 14, 30, 70)),
            ("sma_cross", self.sb.signals_sma_cross, (self.df, 10, 30)),
            ("bbands",    self.sb.signals_bbands,    (self.df, 20)),
            ("breakout",  self.sb.signals_breakout,  (self.df, 20)),
            ("macd",      self.sb.signals_macd,      (self.df,)),
        ]:
            with self.subTest(strategy=name):
                s = fn(*args)
                self.assertIsInstance(s, pd.Series)
                self.assertEqual(len(s), len(self.df))


if __name__ == "__main__":
    unittest.main()
