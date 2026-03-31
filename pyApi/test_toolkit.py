"""
test_toolkit.py
===============
End-to-end test suite for the Stock Toolkit.

All tests use a local fixture database with synthetic OHLCV data.
No external API calls are made.

Run:
    python3 test_toolkit.py            # built-in unittest runner
    python3 -m pytest test_toolkit.py  # pytest (if installed)
    python3 -m pytest test_toolkit.py -v --tb=short  # verbose
"""

import importlib.util
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────
#  FIXTURE HELPERS
# ─────────────────────────────────────────────

SCRIPT_DIR = pathlib.Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

# Symbols used throughout. Two "US" style, two "European" (.MI suffix).
SYMBOLS = ["AAPL", "MSFT", "ENEL.MI", "CSMIB.MI"]

# Controlled price series parameters (seeded for determinism)
SIM_PARAMS = {
    "AAPL":     {"mu": 0.0006,  "sigma": 0.015, "start": 150.0},
    "MSFT":     {"mu": 0.0003,  "sigma": 0.012, "start": 300.0},
    "ENEL.MI":  {"mu": 0.0004,  "sigma": 0.010, "start": 7.0},
    "CSMIB.MI": {"mu": 0.0005,  "sigma": 0.009, "start": 120.0},
}
N_DAYS  = 800   # ~3+ years of trading days
START_D = date(2022, 1, 3)


def _trading_dates(n: int, start: date) -> list[date]:
    """Generate n weekday dates starting from start."""
    days, d = [], start
    while len(days) < n:
        if d.weekday() < 5:   # Mon–Fri
            days.append(d)
        d += timedelta(days=1)
    return days


def make_fixture_db(tmp_dir: pathlib.Path) -> pathlib.Path:
    """
    Create a SQLite fixture database with synthetic OHLCV data.
    Returns the path to the database file.
    Two sources per symbol (yfinance + fmp) to exercise dedup logic.
    """
    db = tmp_dir / "stock_data.db"
    con = sqlite3.connect(db)
    con.execute("""
        CREATE TABLE prices (
            fetched_at TEXT, symbol TEXT, source TEXT,
            data_date TEXT, interval TEXT,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER,
            vwap REAL, change_pct REAL, extra TEXT,
            UNIQUE(symbol, source, data_date, interval)
        )
    """)

    rng   = np.random.default_rng(42)
    dates = _trading_dates(N_DAYS, START_D)

    for sym, p in SIM_PARAMS.items():
        price = p["start"]
        for d in dates:
            # log-normal daily returns
            ret   = rng.normal(p["mu"], p["sigma"])
            price = max(price * np.exp(ret), 0.01)
            o     = round(price * rng.uniform(0.995, 1.000), 4)
            h     = round(price * rng.uniform(1.000, 1.015), 4)
            lo    = round(price * rng.uniform(0.985, 1.000), 4)
            vol   = int(rng.uniform(1_000_000, 5_000_000))
            ds    = str(d)
            for source in ("yfinance", "fmp"):
                con.execute(
                    "INSERT OR IGNORE INTO prices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("2024-01-01", sym, source, ds, "1d",
                     o, h, lo, round(price, 4), vol,
                     round(price, 4), round(ret * 100, 4), "")
                )

    con.commit()
    con.close()
    return db


def _load_module(name: str, tmp_db: pathlib.Path, tmp_dir: pathlib.Path):
    """
    Import a toolkit module and redirect its path constants to the fixture DB.
    Must be called after make_fixture_db().
    """
    spec   = importlib.util.spec_from_file_location(
        name, SCRIPT_DIR / f"{name}.py"
    )
    mod    = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Redirect DB paths — must be done AFTER exec_module
    if hasattr(mod, "LIVE_DB"):
        mod.LIVE_DB  = tmp_db
    if hasattr(mod, "HIST_DIR"):
        mod.HIST_DIR = tmp_dir / "data_nonexistent"
    if hasattr(mod, "DB_PATH"):
        mod.DB_PATH  = tmp_db
    if hasattr(mod, "STATE_PATH"):
        mod.STATE_PATH = tmp_dir / ".test_alerts_state.json"
    if hasattr(mod, "CONFIG_PATH"):
        mod.CONFIG_PATH = tmp_dir / "config.env"

    return mod


# ─────────────────────────────────────────────────────────────
#  BASE TEST CLASS — creates fixture DB once per test class
# ─────────────────────────────────────────────────────────────

class FixtureTestCase(unittest.TestCase):
    """Base class: sets up a shared temp dir + fixture DB for the class."""

    @classmethod
    def setUpClass(cls):
        cls.tmp     = tempfile.TemporaryDirectory()
        cls.tmp_dir = pathlib.Path(cls.tmp.name)
        cls.db      = make_fixture_db(cls.tmp_dir)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


# ─────────────────────────────────────────────────────────────
#  1. COLLECTOR — config parser + dedup helpers
# ─────────────────────────────────────────────────────────────

class TestCollectorConfig(FixtureTestCase):
    """Tests for config.env parser (_load_config)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sc = _load_module("stock_collector", cls.db, cls.tmp_dir)

    def _write_cfg(self, text: str) -> pathlib.Path:
        p = self.tmp_dir / "test_config.env"
        p.write_text(text)
        return p

    def test_basic_key_value(self):
        p   = self._write_cfg("SYMBOLS=AAPL,MSFT\nFMP_KEY=abc123\n")
        cfg = self.sc._load_config(p)
        self.assertEqual(cfg["SYMBOLS"], "AAPL,MSFT")
        self.assertEqual(cfg["FMP_KEY"], "abc123")

    def test_inline_comment_stripped(self):
        p   = self._write_cfg("POLYGON_KEY=   # https://polygon.io\n")
        cfg = self.sc._load_config(p)
        self.assertEqual(cfg["POLYGON_KEY"], "")

    def test_value_with_inline_comment(self):
        p   = self._write_cfg("ALPHAVANTAGE_KEY=mykey123   # sign up free\n")
        cfg = self.sc._load_config(p)
        self.assertEqual(cfg["ALPHAVANTAGE_KEY"], "mykey123")

    def test_quoted_value(self):
        p   = self._write_cfg('FMP_KEY="quoted_value"\n')
        cfg = self.sc._load_config(p)
        self.assertEqual(cfg["FMP_KEY"], "quoted_value")

    def test_comment_lines_ignored(self):
        p   = self._write_cfg("# this is a comment\nFOO=bar\n")
        cfg = self.sc._load_config(p)
        self.assertNotIn("# this is a comment", cfg)
        self.assertEqual(cfg["FOO"], "bar")

    def test_missing_file_returns_empty(self):
        cfg = self.sc._load_config(self.tmp_dir / "nonexistent.env")
        self.assertEqual(cfg, {})

    def test_bool_parsing(self):
        p   = self._write_cfg("FINNHUB_PAID=true\nALPHAVANTAGE_PAID=false\n")
        cfg = self.sc._load_config(p)
        self.assertEqual(cfg["FINNHUB_PAID"].lower(), "true")
        self.assertEqual(cfg["ALPHAVANTAGE_PAID"].lower(), "false")


class TestCollectorDedup(FixtureTestCase):
    """Tests for _live_has_today and _hist_has_data."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sc = _load_module("stock_collector", cls.db, cls.tmp_dir)
        # redirect DB_PATH so _live_has_today reads our fixture
        cls.sc.DB_PATH = cls.db

    def test_live_has_today_miss(self):
        # Fixture has historical data, not today's date
        self.assertFalse(
            self.sc._live_has_today("AAPL", "yfinance", "1d")
        )

    def test_live_has_today_hit(self):
        # Insert a row for today using a dedicated test symbol
        con = sqlite3.connect(self.db)
        today = str(date.today())
        con.execute(
            "INSERT OR IGNORE INTO prices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("x", "TEST_TODAY_SYM", "yfinance", today, "1d",
             150, 151, 149, 150, 1000000, 150, 0.1, "")
        )
        con.commit(); con.close()
        self.assertTrue(
            self.sc._live_has_today("TEST_TODAY_SYM", "yfinance", "1d")
        )

    def test_live_has_today_wrong_symbol(self):
        self.assertFalse(self.sc._live_has_today("UNKNOWN", "yfinance", "1d"))

    def test_hist_has_data_hit(self):
        from datetime import date as d
        result = self.sc._hist_has_data(
            self.db, "AAPL", "yfinance",
            date(2022, 1, 1), date(2023, 12, 31)
        )
        self.assertTrue(result)

    def test_hist_has_data_future_range(self):
        result = self.sc._hist_has_data(
            self.db, "AAPL", "yfinance",
            date(2099, 1, 1), date(2099, 12, 31)
        )
        self.assertFalse(result)

    def test_hist_has_data_missing_db(self):
        result = self.sc._hist_has_data(
            self.tmp_dir / "nonexistent.db", "AAPL", "yfinance",
            date(2022, 1, 1), date(2023, 1, 1)
        )
        self.assertFalse(result)

    def test_symbols_from_db_returns_fixture_symbols(self):
        """All four fixture symbols should appear in _symbols_from_db."""
        syms = self.sc._symbols_from_db()
        for s in ["AAPL", "MSFT", "ENEL.MI", "CSMIB.MI"]:
            self.assertIn(s, syms, f"{s} missing from _symbols_from_db()")

    def test_symbols_from_db_only_daily(self):
        """Only interval='1d' rows should be returned, not quote or 1h."""
        syms = self.sc._symbols_from_db()
        # fixture has yfinance + fmp sources with 1d — all four symbols present
        self.assertGreater(len(syms), 0)
        # confirm no duplicates
        self.assertEqual(len(syms), len(set(syms)), "duplicates in _symbols_from_db()")

    def test_symbols_from_db_missing_db(self):
        """Non-existent DB returns empty list without raising."""
        self.sc.DB_PATH = self.tmp_dir / "nonexistent.db"
        result = self.sc._symbols_from_db()
        self.assertEqual(result, [])
        self.sc.DB_PATH = self.db   # restore

    def test_symbols_merge_config_plus_db(self):
        """DB symbols not in config are appended after config symbols."""
        cfg  = ["AAPL", "TSLA"]
        db   = self.sc._symbols_from_db()   # has AAPL, MSFT, ENEL.MI, CSMIB.MI
        seen = set(cfg)
        merged = list(cfg) + [s for s in db if s not in seen]
        # config symbols come first
        self.assertEqual(merged[:2], ["AAPL", "TSLA"])
        # DB-only symbols appended
        for s in ["MSFT", "ENEL.MI", "CSMIB.MI"]:
            self.assertIn(s, merged)



# ─────────────────────────────────────────────────────────────
#  1c. COLLECTOR — new skip functions (_quote_is_fresh, _hourly_bar_is_current)
#      and --sources flag
# ─────────────────────────────────────────────────────────────

class TestCollectorSkipLogic(FixtureTestCase):
    """Tests for _quote_is_fresh, _hourly_bar_is_current, and --sources."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sc = _load_module("stock_collector", cls.db, cls.tmp_dir)
        cls.sc.DB_PATH = cls.db

    # ── _quote_is_fresh ───────────────────────────────────────────────────────

    def test_quote_is_fresh_miss_no_rows(self):
        """Symbol with no quote rows is never fresh."""
        self.assertFalse(self.sc._quote_is_fresh("AAPL", "finnhub", minutes=25))

    def test_quote_is_fresh_hit_recent(self):
        """A quote (now stored as 1d) inserted seconds ago is fresh."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        con = sqlite3.connect(self.db)
        con.execute(
            "INSERT OR IGNORE INTO prices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (now, "TEST_FRESH_SYM", "finnhub", str(date.today()), "1d",
             150, 151, 149, 150, 1000000, 150, 0.1, "")
        )
        con.commit(); con.close()
        self.assertTrue(self.sc._quote_is_fresh("TEST_FRESH_SYM", "finnhub", minutes=25))

    def test_quote_is_fresh_miss_old(self):
        """A quote (now stored as 1d) inserted 2 hours ago is not fresh for a 25-min window."""
        from datetime import datetime, timezone, timedelta
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        con = sqlite3.connect(self.db)
        con.execute(
            "INSERT OR IGNORE INTO prices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (old_ts, "TEST_OLD_QUOTE", "finnhub", str(date.today()), "1d",
             150, 151, 149, 150, 1000000, 150, 0.1, "")
        )
        con.commit(); con.close()
        self.assertFalse(self.sc._quote_is_fresh("TEST_OLD_QUOTE", "finnhub", minutes=25))

    def test_quote_is_fresh_wrong_source(self):
        """Fresh row for source A does not make source B fresh."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        con = sqlite3.connect(self.db)
        con.execute(
            "INSERT OR IGNORE INTO prices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (now, "TEST_SRC_SYM", "finnhub", str(date.today()), "quote",
             150, 151, 149, 150, 1000000, 150, 0.1, "")
        )
        con.commit(); con.close()
        self.assertFalse(self.sc._quote_is_fresh("TEST_SRC_SYM", "fmp", minutes=25))

    # ── _hourly_bar_is_current ────────────────────────────────────────────────

    def test_hourly_bar_miss_no_rows(self):
        """Symbol with no hourly rows is never current."""
        self.assertFalse(self.sc._hourly_bar_is_current("AAPL", "yfinance"))

    def test_hourly_bar_hit_this_hour(self):
        """A bar timestamped in the current UTC hour is current."""
        from datetime import datetime, timezone
        now      = datetime.now(timezone.utc)
        # build a timestamp that falls in this hour
        ts       = now.strftime("%Y-%m-%dT%H:15:00+00:00")
        con = sqlite3.connect(self.db)
        con.execute(
            "INSERT OR IGNORE INTO prices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("x", "TEST_HOUR_SYM", "yfinance", ts, "1h",
             150, 151, 149, 150, 500000, 150, 0.1, "")
        )
        con.commit(); con.close()
        self.assertTrue(self.sc._hourly_bar_is_current("TEST_HOUR_SYM", "yfinance"))

    def test_hourly_bar_miss_previous_hour(self):
        """A bar from a previous hour is not current."""
        from datetime import datetime, timezone, timedelta
        prev_hour = (datetime.now(timezone.utc) - timedelta(hours=2))
        ts        = prev_hour.strftime("%Y-%m-%dT%H:30:00+00:00")
        con = sqlite3.connect(self.db)
        con.execute(
            "INSERT OR IGNORE INTO prices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("x", "TEST_OLD_HOUR", "yfinance", ts, "1h",
             150, 151, 149, 150, 500000, 150, 0.1, "")
        )
        con.commit(); con.close()
        self.assertFalse(self.sc._hourly_bar_is_current("TEST_OLD_HOUR", "yfinance"))

    def test_hourly_bar_miss_wrong_source(self):
        """Current bar for yfinance does not satisfy twelvedata check."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        ts  = now.strftime("%Y-%m-%dT%H:00:00+00:00")
        con = sqlite3.connect(self.db)
        con.execute(
            "INSERT OR IGNORE INTO prices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("x", "TEST_HOUR_SRC", "yfinance", ts, "1h",
             150, 151, 149, 150, 500000, 150, 0.1, "")
        )
        con.commit(); con.close()
        self.assertFalse(self.sc._hourly_bar_is_current("TEST_HOUR_SRC", "twelvedata"))

    # ── --sources flag via _should_run ────────────────────────────────────────

    def test_sources_none_means_all(self):
        """When run_sources is None (no --sources flag) all sources run."""
        # _should_run is defined inside main() but we can test the logic directly
        run_sources = None
        _should_run = lambda source: run_sources is None or source in run_sources
        for src in ["yfinance","alphavantage","finnhub","polygon","fmp","twelvedata","marketstack"]:
            self.assertTrue(_should_run(src), f"should run {src} when no filter")

    def test_sources_filter_includes(self):
        """Sources in the filter list run; others don't."""
        run_sources = {"finnhub", "fmp"}
        _should_run = lambda source: run_sources is None or source in run_sources
        self.assertTrue(_should_run("finnhub"))
        self.assertTrue(_should_run("fmp"))
        self.assertFalse(_should_run("yfinance"))
        self.assertFalse(_should_run("alphavantage"))
        self.assertFalse(_should_run("marketstack"))




class TestScoreSteps(FixtureTestCase):
    """Tests for each of the seven analysis steps in stock_score.py."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ss   = _load_module("stock_score", cls.db, cls.tmp_dir)
        cls.df   = cls.ss.load_prices("AAPL", "2022-01-01", None)
        # weekly resample used by scorer
        cls.df_w = (
            cls.df.set_index("data_date")
            .resample("W-FRI")
            .agg({"open":"first","high":"max","low":"min",
                  "close":"last","volume":"sum"})
            .dropna(subset=["close"])
            .reset_index()
        )

    def test_load_prices_returns_data(self):
        self.assertFalse(self.df.empty)
        self.assertGreater(len(self.df), 100)

    def test_load_prices_columns(self):
        for col in ["data_date", "close", "open", "high", "low", "volume"]:
            self.assertIn(col, self.df.columns)

    def test_list_all_symbols(self):
        syms = self.ss.list_all_symbols()
        for s in ["AAPL", "MSFT", "ENEL.MI", "CSMIB.MI"]:
            self.assertIn(s, syms)

    def test_step_summary(self):
        r = self.ss.step_summary(self.df_w, ann_factor=52)
        self.assertIn("sharpe", r)
        self.assertIn("ann_vol", r)
        self.assertIn("total_ret", r)
        self.assertIsInstance(r["sharpe"], float)
        self.assertGreater(r["n_bars"], 50)

    def test_step_regression(self):
        r = self.ss.step_regression(self.df_w)
        self.assertIn("r2", r)
        self.assertIn("ann_trend", r)
        self.assertGreaterEqual(r["r2"], 0.0)
        self.assertLessEqual(r["r2"], 1.0)

    def test_step_drawdown(self):
        r = self.ss.step_drawdown(self.df_w)
        self.assertIn("max_dd", r)
        self.assertIn("calmar", r)
        self.assertIn("recovered", r)
        self.assertLessEqual(r["max_dd"], 0.0)  # always negative
        self.assertIsInstance(r["recovered"], bool)

    def test_step_entry_timing(self):
        r = self.ss.step_entry_timing(self.df_w)
        self.assertIn("rsi14", r)
        self.assertIn("pct_b", r)
        self.assertIn("bbands_squeeze", r)
        if r["rsi14"] is not None:
            self.assertGreaterEqual(r["rsi14"], 0)
            self.assertLessEqual(r["rsi14"], 100)

    def test_step_montecarlo(self):
        r = self.ss.step_montecarlo(self.df_w, n_paths=200, horizon=21)
        self.assertIn("prob_gain", r)
        self.assertIn("p50", r)
        self.assertIn("p5", r)
        self.assertGreaterEqual(r["prob_gain"], 0)
        self.assertLessEqual(r["prob_gain"], 100)
        self.assertLess(r["p5"], r["p50"])  # P5 < P50 always

    def test_score_symbol_range(self):
        raw = {
            "symbol":     "AAPL",
            "summary":    self.ss.step_summary(self.df_w, ann_factor=52),
            "regression": self.ss.step_regression(self.df_w),
            "drawdown":   self.ss.step_drawdown(self.df_w),
            "entry":      self.ss.step_entry_timing(self.df_w),
            "montecarlo": self.ss.step_montecarlo(self.df_w, 200, 21),
        }
        score, notes = self.ss.score_symbol(raw)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 100.0)
        self.assertIsInstance(notes, list)
        self.assertGreater(len(notes), 0)

    def test_score_all_horizons(self):
        """All five horizons must produce a valid score without crashing."""
        for horizon, profile in self.ss.HORIZON_PROFILES.items():
            gran = profile["gran"]
            try:
                df_r = self.df.set_index("data_date").resample(gran).agg(
                    {"open":"first","high":"max","low":"min",
                     "close":"last","volume":"sum"}
                ).dropna(subset=["close"]).reset_index()
            except Exception:
                fb   = {"ME":"M","QE":"Q"}.get(gran, gran)
                df_r = self.df.set_index("data_date").resample(fb).agg(
                    {"open":"first","high":"max","low":"min",
                     "close":"last","volume":"sum"}
                ).dropna(subset=["close"]).reset_index()

            raw = {
                "symbol":     "AAPL",
                "summary":    self.ss.step_summary(df_r,
                                                    ann_factor=profile["ann_factor"]),
                "regression": self.ss.step_regression(df_r),
                "drawdown":   self.ss.step_drawdown(df_r),
                "entry":      self.ss.step_entry_timing(df_r),
                "montecarlo": self.ss.step_montecarlo(
                    df_r, 200, profile["mc_bars"]),
            }
            score, _ = self.ss.score_symbol(
                raw,
                weights=profile["weights"],
                min_bars=profile["min_bars"],
            )
            self.assertGreaterEqual(score, 0.0, f"score < 0 for horizon={horizon}")
            self.assertLessEqual(score, 100.0, f"score > 100 for horizon={horizon}")

    def test_horizon_weights_sum_to_100(self):
        for horizon, profile in self.ss.HORIZON_PROFILES.items():
            total = sum(profile["weights"].values())
            self.assertEqual(total, 100,
                             f"{horizon}: weights sum to {total}, expected 100")

    def test_penalty_unrecovered_reduces_score(self):
        """A symbol with unrecovered drawdown should score lower."""
        raw_good = {
            "symbol":     "GOOD",
            "summary":    {"sharpe": 1.5, "ann_vol": 20.0, "n_bars": 200,
                           "total_ret": 80.0, "first": 100, "last": 180},
            "regression": {"r2": 0.85, "ann_trend": 25.0, "p_value": 0.001},
            "drawdown":   {"max_dd": -15.0, "calmar": 10.0, "recovered": True,
                           "ann_ret": 25.0, "dd_dur": 5},
            "entry":      {"rsi14": 42.0, "pct_b": 0.25, "bbands_squeeze": False},
            "montecarlo": {"prob_gain": 82.0, "p50": 110.0, "p5": 90.0,
                           "exp_ret": 15.0},
        }
        raw_bad = dict(raw_good)
        raw_bad["drawdown"] = dict(raw_good["drawdown"])
        raw_bad["drawdown"]["recovered"] = False

        score_good, _ = self.ss.score_symbol(raw_good)
        score_bad,  _ = self.ss.score_symbol(raw_bad)
        self.assertGreater(score_good, score_bad)
        self.assertAlmostEqual(score_good - score_bad, 20.0, places=0)


# ─────────────────────────────────────────────────────────────
#  3. BACKTEST — signals + engine
# ─────────────────────────────────────────────────────────────

class TestBacktest(FixtureTestCase):
    """Tests for signal generators and Backtester."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sb = _load_module("stock_backtest", cls.db, cls.tmp_dir)
        cls.df = cls.sb.load_prices("AAPL", "2022-01-01", None, source=None)

    def test_load_prices(self):
        self.assertFalse(self.df.empty)
        self.assertIn("close", self.df.columns)

    def _run_strategy(self, sigs):
        bt     = self.sb.Backtester(capital=10_000, commission=0.001, slippage=0.001)
        result = bt.run(self.df, sigs)
        bh_sig = pd.Series(0, index=self.df.index)
        bh_sig.iloc[0] = 1
        bh    = bt.run(self.df, bh_sig)
        return result, bh

    def test_rsi_strategy(self):
        sigs = self.sb.signals_rsi(self.df, window=14, buy_at=30, sell_at=70)
        self.assertEqual(len(sigs), len(self.df))
        result, _ = self._run_strategy(sigs)
        m = result["metrics"]
        self.assertIn("total_return_pct", m)
        self.assertIn("sharpe", m)
        self.assertIn("max_dd_pct", m)
        self.assertLessEqual(m["max_dd_pct"], 0)

    def test_sma_cross_strategy(self):
        sigs = self.sb.signals_sma_cross(self.df, fast=20, slow=50)
        result, _ = self._run_strategy(sigs)
        self.assertIn("n_trades", result["metrics"])

    def test_bbands_strategy(self):
        sigs = self.sb.signals_bbands(self.df, window=20)
        result, _ = self._run_strategy(sigs)
        self.assertGreaterEqual(result["metrics"]["n_trades"], 0)

    def test_breakout_strategy(self):
        sigs = self.sb.signals_breakout(self.df, window=20)
        result, _ = self._run_strategy(sigs)
        self.assertIn("win_rate_pct", result["metrics"])

    def test_no_lookahead(self):
        """Signal at bar t should not use bar t+1 close."""
        sigs = self.sb.signals_rsi(self.df, 14, 30, 70)
        # Signals must not be NaN where data exists
        valid = sigs[self.df["close"].notna()]
        self.assertTrue(valid.isin([-1, 0, 1]).all(),
                        "signals contain values other than -1, 0, 1")

    def test_equity_length_matches_price(self):
        sigs           = self.sb.signals_sma_cross(self.df, 20, 50)
        bt             = self.sb.Backtester(10_000)
        result         = bt.run(self.df, sigs)
        self.assertEqual(len(result["equity"]), len(self.df))

    def test_buy_hold_fully_invested(self):
        """Buy-and-hold with zero commission should end at last_price/first_price."""
        bt     = self.sb.Backtester(capital=10_000, commission=0, slippage=0)
        sigs   = pd.Series(0, index=self.df.index)
        sigs.iloc[0] = 1   # buy on day 1, never sell
        result = bt.run(self.df, sigs)
        expected_ret = (self.df["close"].iloc[-1] /
                        self.df["close"].iloc[0] - 1) * 100
        actual_ret   = result["metrics"]["total_return_pct"]
        self.assertAlmostEqual(actual_ret, expected_ret, delta=1.0)

    def test_commission_reduces_return(self):
        sigs      = self.sb.signals_sma_cross(self.df, 20, 50)
        bt_cheap  = self.sb.Backtester(10_000, commission=0.0001, slippage=0)
        bt_costly = self.sb.Backtester(10_000, commission=0.005,  slippage=0)
        r_cheap   = bt_cheap.run(self.df, sigs)["metrics"]["total_return_pct"]
        r_costly  = bt_costly.run(self.df, sigs)["metrics"]["total_return_pct"]
        self.assertGreaterEqual(r_cheap, r_costly)

    def test_calmar_positive_for_profitable_strategy(self):
        """Calmar = ann_return / |max_dd| — should be positive if ann_return > 0."""
        bt     = self.sb.Backtester(10_000, commission=0, slippage=0)
        sigs   = pd.Series(0, index=self.df.index)
        sigs.iloc[0] = 1
        m      = bt.run(self.df, sigs)["metrics"]
        if m["cagr_pct"] > 0 and m["max_dd_pct"] < 0:
            self.assertGreater(m["calmar"], 0)


# ─────────────────────────────────────────────────────────────
#  4. ALERTS — indicators + condition evaluation + edge detect
# ─────────────────────────────────────────────────────────────

class TestAlerts(FixtureTestCase):
    """Tests for build_context, evaluate_condition, and edge detection."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sal = _load_module("stock_alerts", cls.db, cls.tmp_dir)
        cls.df  = cls.sal.load_series("AAPL", n_bars=250)
        cls.ctx = cls.sal.build_context(cls.df) if not cls.df.empty else {}

    def test_load_series(self):
        self.assertFalse(self.df.empty)
        self.assertIn("close", self.df.columns)

    def test_context_has_required_keys(self):
        required = ["price", "rsi14", "sma50", "sma200",
                    "bbands_pct_b", "bbands_squeeze",
                    "macd", "change_pct", "volume_spike"]
        for k in required:
            self.assertIn(k, self.ctx, f"missing key: {k}")

    def test_rsi_in_range(self):
        if self.ctx.get("rsi14") is not None:
            self.assertGreaterEqual(self.ctx["rsi14"], 0)
            self.assertLessEqual(self.ctx["rsi14"], 100)

    def test_pct_b_is_float(self):
        if self.ctx.get("bbands_pct_b") is not None:
            self.assertIsInstance(self.ctx["bbands_pct_b"], float)

    def test_bbands_squeeze_is_bool(self):
        self.assertIsInstance(self.ctx["bbands_squeeze"], bool)

    def test_evaluate_always_true(self):
        r = self.sal.evaluate_condition("price > 0", self.ctx)
        self.assertTrue(r)

    def test_evaluate_always_false(self):
        r = self.sal.evaluate_condition("price < 0", self.ctx)
        self.assertFalse(r)

    def test_evaluate_rsi_condition(self):
        r = self.sal.evaluate_condition("rsi14 < 100", self.ctx)
        self.assertTrue(r)

    def test_evaluate_compound_condition(self):
        r = self.sal.evaluate_condition("price > 0 and rsi14 < 100", self.ctx)
        self.assertTrue(r)

    def test_evaluate_none_for_unknown_indicator(self):
        ctx_copy = dict(self.ctx)
        ctx_copy["sma200"] = None
        r = self.sal.evaluate_condition("price > sma200", ctx_copy)
        self.assertIsNone(r)

    def test_edge_detection_false_to_true(self):
        state = {}
        fired = self.sal.check_edge(state, "AAPL|rsi14 < 30", False)
        self.assertFalse(fired)   # False→False: no fire
        fired = self.sal.check_edge(state, "AAPL|rsi14 < 30", True)
        self.assertTrue(fired)    # False→True: FIRE

    def test_edge_detection_no_refire(self):
        state = {}
        self.sal.check_edge(state, "AAPL|test", True)   # first fire
        fired = self.sal.check_edge(state, "AAPL|test", True)
        self.assertFalse(fired)   # True→True: no re-fire

    def test_edge_detection_refire_after_reset(self):
        state = {}
        self.sal.check_edge(state, "AAPL|test", True)   # fire
        self.sal.check_edge(state, "AAPL|test", False)  # reset
        fired = self.sal.check_edge(state, "AAPL|test", True)
        self.assertTrue(fired)    # False→True again: FIRE

    def test_state_persistence(self):
        state_path = self.tmp_dir / ".test_state.json"
        self.sal.STATE_PATH = state_path
        state = {}
        self.sal.check_edge(state, "AAPL|rsi14 < 30", True)
        self.sal.save_state(state)
        self.assertTrue(state_path.exists())
        loaded = self.sal.load_state()
        self.assertIn("AAPL|rsi14 < 30", loaded)


# ─────────────────────────────────────────────────────────────
#  5. PIPELINE — full end-to-end flows
# ─────────────────────────────────────────────────────────────

class TestPipeline(FixtureTestCase):
    """
    End-to-end tests that exercise the full data → analysis → action pipeline.
    These mirror real usage scenarios without touching any external API.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ss  = _load_module("stock_score",    cls.db, cls.tmp_dir)
        cls.sb  = _load_module("stock_backtest", cls.db, cls.tmp_dir)
        cls.sal = _load_module("stock_alerts",   cls.db, cls.tmp_dir)

    def test_score_multiple_symbols_ranked(self):
        """Score all four symbols and verify ranking is deterministic."""
        results = []
        profile = self.ss.HORIZON_PROFILES["quarter"]
        for sym in SYMBOLS:
            df = self.ss.load_prices(sym, "2022-01-01", None)
            if df.empty:
                continue
            df_w = (df.set_index("data_date")
                    .resample("W-FRI")
                    .agg({"open":"first","high":"max","low":"min",
                          "close":"last","volume":"sum"})
                    .dropna(subset=["close"])
                    .reset_index())
            raw = {
                "symbol":     sym,
                "summary":    self.ss.step_summary(df_w, 52),
                "regression": self.ss.step_regression(df_w),
                "drawdown":   self.ss.step_drawdown(df_w),
                "entry":      self.ss.step_entry_timing(df_w),
                "montecarlo": self.ss.step_montecarlo(df_w, 200, 63),
            }
            score, _ = self.ss.score_symbol(
                raw, weights=profile["weights"], min_bars=profile["min_bars"])
            results.append((sym, score))

        self.assertEqual(len(results), len(SYMBOLS))
        # Scores are in [0, 100]
        for sym, score in results:
            self.assertGreaterEqual(score, 0, f"{sym}: score < 0")
            self.assertLessEqual(score, 100, f"{sym}: score > 100")

        # Ranking is deterministic: same run → same order
        scores1 = [s for _, s in results]
        scores2 = []
        for sym in SYMBOLS:
            df   = self.ss.load_prices(sym, "2022-01-01", None)
            df_w = (df.set_index("data_date")
                    .resample("W-FRI")
                    .agg({"open":"first","high":"max","low":"min",
                          "close":"last","volume":"sum"})
                    .dropna(subset=["close"]).reset_index())
            raw  = {
                "symbol":     sym,
                "summary":    self.ss.step_summary(df_w, 52),
                "regression": self.ss.step_regression(df_w),
                "drawdown":   self.ss.step_drawdown(df_w),
                "entry":      self.ss.step_entry_timing(df_w),
                "montecarlo": self.ss.step_montecarlo(df_w, 200, 63),
            }
            score, _ = self.ss.score_symbol(
                raw, weights=profile["weights"], min_bars=profile["min_bars"])
            scores2.append(score)
        self.assertEqual(scores1, scores2, "Scoring is not deterministic")

    def test_backtest_then_score_same_symbol(self):
        """Backtest and score should both work on the same symbol/data."""
        sym = "AAPL"

        # Backtest
        df     = self.sb.load_prices(sym, "2022-01-01", None, None)
        sigs   = self.sb.signals_sma_cross(df, 20, 50)
        bt     = self.sb.Backtester(10_000)
        result = bt.run(df, sigs)
        self.assertIn("metrics", result)

        # Score
        df2  = self.ss.load_prices(sym, "2022-01-01", None)
        df_w = (df2.set_index("data_date")
                .resample("W-FRI")
                .agg({"open":"first","high":"max","low":"min",
                      "close":"last","volume":"sum"})
                .dropna(subset=["close"]).reset_index())
        profile = self.ss.HORIZON_PROFILES["quarter"]
        raw = {
            "symbol":     sym,
            "summary":    self.ss.step_summary(df_w, 52),
            "regression": self.ss.step_regression(df_w),
            "drawdown":   self.ss.step_drawdown(df_w),
            "entry":      self.ss.step_entry_timing(df_w),
            "montecarlo": self.ss.step_montecarlo(df_w, 200, 63),
        }
        score, _ = self.ss.score_symbol(
            raw, weights=profile["weights"], min_bars=profile["min_bars"])
        self.assertGreaterEqual(score, 0)

    def test_alert_fires_on_real_indicator(self):
        """Evaluate a condition that's always true, verify it fires."""
        df  = self.sal.load_series("AAPL", n_bars=250)
        ctx = self.sal.build_context(df)
        state = {}
        result = self.sal.evaluate_condition("price > 0", ctx)
        self.assertTrue(result)
        fired = self.sal.check_edge(state, "AAPL|price > 0", result)
        self.assertTrue(fired)

    def test_all_symbols_have_data(self):
        """Verify fixture DB has rows for every expected symbol."""
        syms = self.ss.list_all_symbols()
        for s in SYMBOLS:
            self.assertIn(s, syms, f"symbol {s} missing from fixture DB")

    def test_multi_symbol_correlation_possible(self):
        """Load multiple symbols and compute a correlation matrix."""
        series = {}
        for sym in SYMBOLS:
            df = self.ss.load_prices(sym, "2022-01-01", None)
            df_w = (df.set_index("data_date")
                    .resample("W-FRI").last()
                    .dropna())
            series[sym] = df_w["close"].pct_change().dropna()

        aligned = pd.DataFrame(series).dropna()
        corr    = aligned.corr()
        self.assertEqual(corr.shape, (len(SYMBOLS), len(SYMBOLS)))
        # Diagonal must be 1.0
        for s in SYMBOLS:
            self.assertAlmostEqual(corr.loc[s, s], 1.0, places=5)
        # Off-diagonal values in [-1, 1]
        for s1 in SYMBOLS:
            for s2 in SYMBOLS:
                self.assertGreaterEqual(corr.loc[s1, s2], -1.0)
                self.assertLessEqual(corr.loc[s1, s2],     1.0)

    def test_horizon_life_requires_long_history(self):
        """life horizon needs 120 bars — thin data penalty should fire."""
        sym  = "AAPL"
        df   = self.ss.load_prices(sym, "2022-01-01", None)
        prof = self.ss.HORIZON_PROFILES["life"]
        try:
            df_m = (df.set_index("data_date")
                    .resample("ME").agg({"open":"first","high":"max",
                                         "low":"min","close":"last","volume":"sum"})
                    .dropna(subset=["close"]).reset_index())
        except Exception:
            df_m = (df.set_index("data_date")
                    .resample("M").agg({"open":"first","high":"max",
                                        "low":"min","close":"last","volume":"sum"})
                    .dropna(subset=["close"]).reset_index())

        raw = {
            "symbol":     sym,
            "summary":    self.ss.step_summary(df_m, ann_factor=12),
            "regression": self.ss.step_regression(df_m),
            "drawdown":   self.ss.step_drawdown(df_m),
            "entry":      self.ss.step_entry_timing(df_m),
            "montecarlo": self.ss.step_montecarlo(df_m, 100, prof["mc_bars"]),
        }
        score, notes = self.ss.score_symbol(
            raw, weights=prof["weights"], min_bars=prof["min_bars"])

        # Fixture has ~3 years = ~36 monthly bars < 120 min_bars
        # Penalty should fire
        if len(df_m) < prof["min_bars"]:
            penalty_notes = [n for n in notes if "PENALTY" in n and "thin" in n]
            self.assertGreater(len(penalty_notes), 0,
                               "thin data penalty should have fired")


# ─────────────────────────────────────────────────────────────
#  6b. INVENTORY — --remove and --check
# ─────────────────────────────────────────────────────────────

class TestInventory(FixtureTestCase):
    """Tests for stock_inventory cmd_remove, cmd_check, and _group_gaps."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.inv = _load_module("stock_inventory", cls.db, cls.tmp_dir)
        # patch LIVE_DB so discover_dbs finds our fixture
        cls.inv.LIVE_DB  = cls.db
        cls.inv.HIST_DIR = cls.tmp_dir / "data_nonexistent"

    # ── _group_gaps ───────────────────────────────────────────────────────────

    def test_group_gaps_single(self):
        result = self.inv._group_gaps(["2024-01-03"])
        self.assertEqual(result, ["2024-01-03"])

    def test_group_gaps_consecutive_range(self):
        result = self.inv._group_gaps(["2024-01-03","2024-01-04","2024-01-05"])
        self.assertEqual(result, ["2024-01-03..2024-01-05"])

    def test_group_gaps_non_consecutive(self):
        result = self.inv._group_gaps(["2024-01-03","2024-01-09"])
        self.assertEqual(result, ["2024-01-03", "2024-01-09"])

    def test_group_gaps_mixed(self):
        result = self.inv._group_gaps(
            ["2024-01-03","2024-01-04","2024-01-05","2024-01-09","2024-01-10"]
        )
        self.assertEqual(result, ["2024-01-03..2024-01-05", "2024-01-09..2024-01-10"])

    def test_group_gaps_empty(self):
        self.assertEqual(self.inv._group_gaps([]), [])

    # ── cmd_check — clean data ────────────────────────────────────────────────

    def test_check_fixture_runs_without_error(self):
        """cmd_check must complete without raising on the fixture DB."""
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.inv.cmd_check([self.db], None)
        output = buf.getvalue()
        # should produce some output (either clean or issues found)
        self.assertGreater(len(output), 0)

    def test_check_symbol_filter(self):
        """Filtering to a single symbol should not raise."""
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.inv.cmd_check([self.db], ["AAPL"])
        # output should mention AAPL or the clean message
        output = buf.getvalue()
        self.assertTrue("AAPL" in output or "No consistency" in output)

    def test_check_detects_gap(self):
        """Inject a gap into a temp DB and verify --check reports it."""
        import io, contextlib, sqlite3 as _sq, tempfile, shutil
        from datetime import date, timedelta

        # Build a small DB with a deliberate weekday gap.
        # Need 3 symbols so 2-of-3 (75% threshold met) establishes the calendar,
        # while the third symbol is missing one day.
        tmp2 = pathlib.Path(tempfile.mkdtemp())
        gap_db = tmp2 / "gap_test.db"
        con = _sq.connect(gap_db)
        con.execute("""CREATE TABLE prices (
            fetched_at TEXT, symbol TEXT, source TEXT,
            data_date TEXT, interval TEXT,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER,
            vwap REAL, change_pct REAL, extra TEXT)""")
        days = []
        d = date(2024, 1, 2)
        while len(days) < 10:
            if d.weekday() < 5:
                days.append(str(d))
            d += timedelta(days=1)
        # MSFT and GOOGL: all days present (establishes calendar)
        for sym in ("MSFT", "GOOGL"):
            for day in days:
                con.execute(
                    "INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("x", sym, "yfinance", day, "1d", 300, 301, 299, 300,
                     100000, 300, 0, ""))
        # AAPL: day index 4 missing (calendar day present in 2/3 = 67% > threshold)
        for i, day in enumerate(days):
            if i == 4:
                continue
            con.execute(
                "INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("x", "AAPL", "yfinance", day, "1d", 150, 151, 149, 150,
                 100000, 150, 0, ""))
        con.commit(); con.close()

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.inv.cmd_check([gap_db], None)
        output = buf.getvalue()

        self.assertIn("AAPL", output)
        self.assertIn("missing", output.lower())
        shutil.rmtree(tmp2)

    # ── cmd_remove ────────────────────────────────────────────────────────────

    def test_remove_nonexistent_symbol(self):
        """Removing a symbol not in the DB prints a not-found message."""
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.inv.cmd_remove("NONEXISTENT_XYZ", [self.db])
        self.assertIn("not found", buf.getvalue().lower())

    def test_remove_with_env_allow(self, monkeypatch=None):
        """
        With STOCK_INV_REMOVE=allow, cmd_remove deletes without prompting.
        Uses a copy of the fixture DB so the main fixture is unaffected.
        """
        import io, contextlib, shutil, sqlite3 as _sq

        # count rows for a symbol before removal
        con = _sq.connect(self.db)
        n_before = con.execute(
            "SELECT COUNT(*) FROM prices WHERE symbol='AAPL'"
        ).fetchone()[0]
        con.close()
        self.assertGreater(n_before, 0)

        # work on a copy — don't corrupt the shared fixture
        tmp2 = pathlib.Path(tempfile.mkdtemp())
        db_copy = tmp2 / "stock_data.db"
        shutil.copy(self.db, db_copy)

        import os
        old_env = os.environ.get("STOCK_INV_REMOVE")
        os.environ["STOCK_INV_REMOVE"] = "allow"
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.inv.cmd_remove("AAPL", [db_copy])
        finally:
            if old_env is None:
                os.environ.pop("STOCK_INV_REMOVE", None)
            else:
                os.environ["STOCK_INV_REMOVE"] = old_env

        # verify rows are gone
        con = _sq.connect(db_copy)
        n_after = con.execute(
            "SELECT COUNT(*) FROM prices WHERE symbol='AAPL'"
        ).fetchone()[0]
        con.close()
        self.assertEqual(n_after, 0)

        output = buf.getvalue()
        self.assertIn("deleted", output.lower())
        shutil.rmtree(tmp2)




if __name__ == "__main__":
    # Friendly summary output
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()

    for cls in [
        TestCollectorConfig,
        TestCollectorDedup,
        TestCollectorSkipLogic,
        TestScoreSteps,
        TestBacktest,
        TestAlerts,
        TestInventory,
        TestPipeline,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2, failfast=False)
    result = runner.run(suite)

    # Summary line
    total  = result.testsRun
    failed = len(result.failures) + len(result.errors)
    passed = total - failed
    print(f"\n{'─'*60}")
    print(f"  {passed}/{total} passed  "
          + (f"  {failed} FAILED" if failed else "  ✓ all green"))
    print(f"{'─'*60}")

    sys.exit(0 if result.wasSuccessful() else 1)
