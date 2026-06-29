"""
test_analysis_cli.py
====================
Coverage-focused tests for stock_toolkit/analysis.py — exercises every
analysis tool both directly (plot on/off) and through the argparse CLI
(`main()` with patched argv), plus the pure helpers (hurst, ann_factor,
granularity). Runs fully offline against the shared synthetic fixture DB.

The analysis_* functions print tables and optionally draw matplotlib
figures; we redirect stdout to keep output quiet and force the Agg
backend so plot=True paths build figures without a display.
"""
import contextlib
import io
import os
import pathlib
import sys
import unittest
import warnings

os.environ.setdefault("MPLBACKEND", "Agg")
# plot=True paths call plt.show() under the Agg backend → harmless noise.
warnings.filterwarnings("ignore", message="FigureCanvasAgg is non-interactive")

SCRIPT_DIR = pathlib.Path(__file__).parent
PKG_ROOT   = SCRIPT_DIR.parent
sys.path.insert(0, str(PKG_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from test_toolkit import FixtureTestCase, _load_module  # noqa: E402


def _quiet(fn, *a, **k):
    """Call fn swallowing stdout + the headless-plot warning; close figures."""
    import matplotlib.pyplot as plt
    with contextlib.redirect_stdout(io.StringIO()), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        out = fn(*a, **k)
    plt.close("all")
    return out


class AnalysisBase(FixtureTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sa     = _load_module("analysis", cls.db, cls.tmp_dir)
        cls.df_raw = cls.sa.load_raw([cls.db])
        cls.df     = cls.sa.apply_granularity(
            cls.sa.resolve_source(cls.df_raw, None), "1d")


class TestAnalysisTools(AnalysisBase):
    """Every analysis_* tool, with plot False and True."""

    def _run_both(self, fn, *a):
        # plot=False then plot=True — covers the figure branch too.
        _quiet(fn, *a, False)
        _quiet(fn, *a, True)

    def test_summary(self):
        _quiet(self.sa.analysis_summary, self.df, "close", "1d")

    def test_regression(self):
        self._run_both(self.sa.analysis_regression, self.df, "close")

    def test_returns(self):
        _quiet(self.sa.analysis_returns, self.df, "close", False, "1d")
        _quiet(self.sa.analysis_returns, self.df, "close", True, "1d")

    def test_volatility(self):
        _quiet(self.sa.analysis_volatility, self.df, "close", 20, False, "1d")
        _quiet(self.sa.analysis_volatility, self.df, "close", 20, True, "1d")

    def test_correlation(self):
        self._run_both(self.sa.analysis_correlation, self.df, "close")

    def test_sma(self):
        _quiet(self.sa.analysis_sma, self.df, "close", [20, 50, 200], False)
        _quiet(self.sa.analysis_sma, self.df, "close", [20, 50, 200], True)

    def test_drawdown(self):
        self._run_both(self.sa.analysis_drawdown, self.df, "close")

    def test_rsi(self):
        _quiet(self.sa.analysis_rsi, self.df, "close", 14, False)
        _quiet(self.sa.analysis_rsi, self.df, "close", 14, True)

    def test_bbands(self):
        _quiet(self.sa.analysis_bbands, self.df, "close", 20, False)
        _quiet(self.sa.analysis_bbands, self.df, "close", 20, True)

    def test_montecarlo(self):
        _quiet(self.sa.analysis_montecarlo, self.df, "close", 500, 63, False)
        _quiet(self.sa.analysis_montecarlo, self.df, "close", 500, 63, True)

    def test_hurst(self):
        self._run_both(self.sa.analysis_hurst, self.df, "close")


class TestAnalysisHelpers(AnalysisBase):
    """Pure helpers — return values, not just no-crash."""

    def test_ann_factor_known_values(self):
        self.assertAlmostEqual(self.sa.ann_factor("1d"), 252.0)
        # weekly/monthly/quarterly are smaller; all positive
        for gran in ("1w", "1M", "1Q"):
            self.assertGreater(self.sa.ann_factor(gran), 0)

    def test_hurst_exponent_shape_and_range(self):
        import numpy as np
        rng = np.random.default_rng(0)
        ts  = np.cumsum(rng.normal(size=400)) + 100
        h, lags, rs = self.sa.hurst_exponent(ts)
        self.assertTrue(0.0 <= h <= 1.5)         # random walk ~0.5
        self.assertEqual(len(lags), len(rs))

    def test_auto_granularity_daily(self):
        gran = self.sa.auto_granularity(self.df, intraday=False)
        self.assertIn(gran, ("1d", "1w", "1M", "1Q"))

    def test_apply_granularity_resamples(self):
        weekly = self.sa.apply_granularity(self.df, "1w")
        self.assertFalse(weekly.empty)
        # weekly has fewer rows than daily for the same symbol
        s = "AAPL"
        self.assertLess((weekly["symbol"] == s).sum(),
                        (self.df["symbol"] == s).sum())


class TestAnalysisCli(AnalysisBase):
    """Drive main() / _main() through argparse with patched argv."""

    def _cli(self, *args):
        argv = ["stock-analyse", *args]
        with contextlib.redirect_stdout(io.StringIO()), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            old = sys.argv
            sys.argv = argv
            try:
                self.sa.main()
            finally:
                sys.argv = old
        import matplotlib.pyplot as plt
        plt.close("all")

    def test_list_symbols(self):
        self._cli("--list-symbols")

    def test_single_tool(self):
        self._cli("-s", "AAPL", "--analysis", "summary")

    def test_all_tools_with_plot(self):
        self._cli("-s", "AAPL", "MSFT", "--analysis",
                  "regression", "returns", "volatility", "correlation",
                  "sma", "drawdown", "rsi", "bbands", "hurst", "--plot")

    def test_montecarlo_cli(self):
        self._cli("-s", "AAPL", "--analysis", "montecarlo",
                  "--mc-paths", "300", "--mc-horizon", "42")

    def test_date_range_and_granularity(self):
        self._cli("-s", "AAPL", "--from", "2022-06-01", "--to", "2023-06-01",
                  "--granularity", "1w", "--analysis", "summary")

    def test_save_dataset(self):
        out = self.tmp_dir / "saved.csv"
        self._cli("-s", "AAPL", "--analysis", "summary", "--save", str(out))
        self.assertTrue(out.exists())

    def test_no_data_for_unknown_symbol_exits(self):
        with self.assertRaises(SystemExit):
            self._cli("-s", "NOPE_NOT_A_SYMBOL", "--analysis", "summary")


if __name__ == "__main__":
    unittest.main()
