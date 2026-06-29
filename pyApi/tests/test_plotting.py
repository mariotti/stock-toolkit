"""
test_plotting.py
===============
Coverage-focused tests for stock_toolkit/collector/plotting.py — drives
the matplotlib and gnuplot renderers (gnuplot just writes .dat/.gp
files, no binary needed) plus the CSV/empty data-loading branches,
against the synthetic fixture DB. Output dirs are redirected to the
test temp dir.
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


class TestPlotting(FixtureTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _load_module("collector", cls.db, cls.tmp_dir)
        from stock_toolkit.collector import config as cfg
        from stock_toolkit.collector import plotting
        cls.cfg = cfg
        cls.plotting = plotting
        cfg.DB_PATH       = cls.db
        cfg.GNUPLOT_DIR   = cls.tmp_dir / "gnuplot"
        cfg.MATPLOTLIB_DIR = cls.tmp_dir / "mpl"
        cfg.OUTPUT_DIR    = cls.tmp_dir
        cfg.CSV_PATH      = cls.tmp_dir / "stock_data.csv"

    def _quiet(self, fn, *a, **k):
        import matplotlib.pyplot as plt
        with contextlib.redirect_stdout(io.StringIO()), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = fn(*a, **k)
        plt.close("all")
        return out

    def test_load_plot_data_from_db(self):
        df = self.plotting._load_plot_data(["AAPL", "MSFT"], False, "close",
                                           db_path=self.db)
        self.assertFalse(df.empty)
        self.assertIn("close", df.columns)

    def test_load_plot_data_unknown_symbol_empty(self):
        df = self.plotting._load_plot_data(["ZZZZ"], False, "close",
                                           db_path=self.db)
        self.assertTrue(df.empty)

    def test_load_plot_data_csv_missing_empty(self):
        self.cfg.CSV_PATH = self.tmp_dir / "does_not_exist.csv"
        df = self._quiet(self.plotting._load_plot_data, ["AAPL"], True, "close")
        self.assertTrue(df.empty)

    def test_matplotlib_renders_png(self):
        self._quiet(self.plotting.plot_matplotlib, ["AAPL", "MSFT"],
                    False, "close", db_path=self.db)
        pngs = list((self.tmp_dir / "mpl").glob("*.png"))
        self.assertTrue(pngs, "expected a matplotlib PNG to be written")

    def test_matplotlib_empty_data_noops(self):
        # unknown symbol → empty → early return, no crash
        self._quiet(self.plotting.plot_matplotlib, ["ZZZZ"], False, "close",
                    db_path=self.db)

    def test_gnuplot_writes_scripts(self):
        self._quiet(self.plotting.plot_gnuplot, ["AAPL", "MSFT"],
                    False, "close", db_path=self.db)
        gp = self.tmp_dir / "gnuplot" / "stock_plot.gp"
        dats = list((self.tmp_dir / "gnuplot").glob("*.dat"))
        self.assertTrue(gp.exists(), "expected stock_plot.gp")
        self.assertTrue(dats, "expected per-symbol .dat files")

    def test_gnuplot_empty_data_noops(self):
        self._quiet(self.plotting.plot_gnuplot, ["ZZZZ"], False, "close",
                    db_path=self.db)


if __name__ == "__main__":
    unittest.main()
