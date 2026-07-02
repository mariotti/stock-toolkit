"""
test_score_validation.py
======================
Tests for stock_toolkit.score_validation — the self-validating score
backtest. The fixture is a random walk, so the IC there is ~0 by
construction; these tests cover the *machinery* (shape, no-crash,
empty-data path) and, most importantly, the *statistical honesty* of
the verdict (a big mean IC must NOT be called a signal unless it's
significant).
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

from test_toolkit import FixtureTestCase, SYMBOLS  # noqa: E402
from stock_toolkit import score_validation as sv   # noqa: E402


class TestVerdictHonesty(unittest.TestCase):
    """The verdict must gate 'signal' on statistical significance, not on
    the raw mean IC — otherwise the tool would overclaim, defeating its
    entire purpose."""

    def test_too_few_dates(self):
        self.assertIn("Too few", sv.verdict_for_ic(0.30, 5.0, n_dates=4))

    def test_big_mean_but_insignificant_is_not_a_signal(self):
        # mean IC looks large (+0.053) but t=1.2 → must read as no signal
        v = sv.verdict_for_ic(0.053, 1.2, n_dates=83)
        self.assertIn("No statistically reliable signal", v)
        self.assertNotIn("Strong", v)

    def test_significant_positive_is_a_signal(self):
        v = sv.verdict_for_ic(0.08, 3.5, n_dates=40)
        self.assertIn("significant signal", v.lower())
        self.assertIn("Modest", v)

    def test_significant_negative_flags_wrong_way(self):
        v = sv.verdict_for_ic(-0.09, -3.0, n_dates=40)
        self.assertIn("wrong way", v.lower())


class TestScoreBacktestMechanics(FixtureTestCase):
    """Run the walk-forward against the synthetic fixture DB."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from stock_toolkit import score
        cls._score = score
        cls._orig  = (score.LIVE_DB, score.HIST_DIR)
        score.LIVE_DB  = cls.db
        score.HIST_DIR = cls.tmp_dir / "no_hist"
        cls.result = sv.run_score_backtest(
            SYMBOLS, "quarter", lookback_years=1,
            rebalance_months=3, mc_paths=50)

    @classmethod
    def tearDownClass(cls):
        cls._score.LIVE_DB, cls._score.HIST_DIR = cls._orig
        super().tearDownClass()

    def test_result_shape(self):
        r = self.result
        for key in ("mean_ic", "median_ic", "ic_tstat", "n_obs", "n_dates",
                    "tercile_returns", "high_minus_low", "observations",
                    "verdict"):
            self.assertIn(key, r)

    def test_produced_observations(self):
        r = self.result
        self.assertGreater(r["n_obs"], 0)
        self.assertEqual(r["n_symbols"], len(SYMBOLS))
        self.assertLessEqual(abs(r["mean_ic"]), 1.0)   # valid correlation

    def test_observations_have_score_and_forward(self):
        obs = self.result["observations"]
        self.assertFalse(obs.empty)
        for col in ("date", "symbol", "score", "forward_return"):
            self.assertIn(col, obs.columns)

    def test_tercile_keys(self):
        self.assertEqual(set(self.result["tercile_returns"]),
                         {"low", "mid", "high"})

    def test_unknown_symbol_returns_empty_not_crash(self):
        e = sv.run_score_backtest(["NOPE_XYZ"], "quarter")
        self.assertEqual(e["n_obs"], 0)
        self.assertIn("Not enough history", e["verdict"])

    def test_bad_horizon_raises(self):
        with self.assertRaises(ValueError):
            sv.run_score_backtest(SYMBOLS, "decade")


if __name__ == "__main__":
    unittest.main()
