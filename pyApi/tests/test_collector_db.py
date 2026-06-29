"""
test_collector_db.py
===================
Coverage-focused tests for stock_toolkit/collector/db.py — the row
builders, dedup, insert, CSV append, and the freshness/staleness
helpers. Runs against a fresh temp DB plus the shared fixture DB.
"""
import datetime
import os
import pathlib
import sqlite3
import sys
import unittest

os.environ.setdefault("MPLBACKEND", "Agg")

SCRIPT_DIR = pathlib.Path(__file__).parent
PKG_ROOT   = SCRIPT_DIR.parent
sys.path.insert(0, str(PKG_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from test_toolkit import FixtureTestCase, _load_module  # noqa: E402


class TestCollectorDb(FixtureTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sc  = _load_module("collector", cls.db, cls.tmp_dir)
        from stock_toolkit.collector import db as dbmod
        from stock_toolkit.collector import config as cfg
        cls.db_mod = dbmod
        cls.cfg = cfg
        # Insert/freshness helpers default to cfg.DB_PATH — point at a
        # writable scratch DB seeded with the prices schema.
        cls.scratch = cls.tmp_dir / "scratch.db"
        con = sqlite3.connect(cls.scratch)
        con.execute("""CREATE TABLE prices (
            fetched_at TEXT, symbol TEXT, source TEXT, timestamp TEXT,
            interval TEXT, open REAL, high REAL, low REAL, close REAL,
            volume INTEGER, vwap REAL, change_pct REAL, extra TEXT,
            UNIQUE(symbol, source, timestamp))""")
        con.commit(); con.close()
        cfg.DB_PATH  = cls.scratch
        cfg.CSV_PATH = cls.tmp_dir / "out.csv"

    # ── make_row / dedup ──────────────────────────────────────────────────

    def test_make_row_normalises(self):
        r = self.db_mod.make_row("aapl", "yfinance", "2024-01-02", "1d",
                                  10, 11, 9, 10.5, 1000, vwap=10.2,
                                  change_pct=1.5, extra={"k": "v"})
        self.assertEqual(r["symbol"], "AAPL")
        self.assertEqual(r["close"], 10.5)
        self.assertTrue(r["timestamp"].startswith("2024-01-02"))
        self.assertIn("k", r["extra"])

    def test_make_row_infers_interval_when_none(self):
        r = self.db_mod.make_row("AAPL", "yf", "2024-01-02", None,
                                  1, 1, 1, 1, 1)
        self.assertIn(r["interval"], ("1d", "1h", "1m"))

    def test_make_row_handles_empty_values(self):
        r = self.db_mod.make_row("AAPL", "yf", "2024-01-02", "1d",
                                  None, None, None, None, None)
        self.assertEqual(r["close"], "")

    def test_dedup_key_stable_and_distinct(self):
        a = self.db_mod.make_row("AAPL", "yf", "2024-01-02", "1d", 1, 1, 1, 1, 1)
        b = self.db_mod.make_row("AAPL", "yf", "2024-01-03", "1d", 1, 1, 1, 1, 1)
        self.assertEqual(self.db_mod.dedup_key(a), self.db_mod.dedup_key(a))
        self.assertNotEqual(self.db_mod.dedup_key(a), self.db_mod.dedup_key(b))

    # ── insert / csv ──────────────────────────────────────────────────────

    def test_db_insert_rows_and_dedup(self):
        rows = [self.db_mod.make_row("TST", "yf", "2024-02-01", "1d",
                                     1, 2, 0.5, 1.5, 100)]
        n1 = self.db_mod.db_insert_rows(rows, db_path=self.scratch)
        self.assertEqual(n1, 1)
        # same row again → UNIQUE conflict → 0 inserted
        n2 = self.db_mod.db_insert_rows(rows, db_path=self.scratch)
        self.assertEqual(n2, 0)

    def test_db_insert_rows_empty(self):
        self.assertEqual(self.db_mod.db_insert_rows([], db_path=self.scratch), 0)

    def test_csv_append_rows(self):
        seen = set()
        rows = [self.db_mod.make_row("CSV", "yf", "2024-03-01", "1d",
                                     1, 1, 1, 1, 1)]
        n = self.db_mod.csv_append_rows(rows, seen)
        self.assertEqual(n, 1)
        self.assertTrue(self.cfg.CSV_PATH.exists())
        # second call with same key → skipped
        self.assertEqual(self.db_mod.csv_append_rows(rows, seen), 0)

    # ── freshness / staleness against the fixture (old dates → not fresh) ─

    def test_live_has_today_false_for_old_fixture(self):
        # fixture data ends in the past → no row dated today
        self.cfg.DB_PATH = self.db
        self.assertFalse(self.db_mod._live_has_today("AAPL", "yfinance"))
        self.cfg.DB_PATH = self.scratch

    def test_quote_is_fresh_false_for_old(self):
        self.cfg.DB_PATH = self.db
        self.assertFalse(self.db_mod._quote_is_fresh("AAPL", "yfinance", 25))
        self.cfg.DB_PATH = self.scratch

    def test_hist_has_data_true(self):
        d0, d1 = datetime.date(2022, 1, 1), datetime.date(2025, 1, 1)
        self.assertTrue(
            self.db_mod._hist_has_data(self.db, "AAPL", "yfinance", d0, d1))

    def test_hist_has_data_false_unknown(self):
        d0, d1 = datetime.date(2022, 1, 1), datetime.date(2025, 1, 1)
        self.assertFalse(
            self.db_mod._hist_has_data(self.db, "ZZZZ", "yfinance", d0, d1))

    def test_sort_by_staleness_returns_all(self):
        self.cfg.DB_PATH = self.db
        out = self.db_mod._sort_by_staleness(["AAPL", "MSFT"])
        self.assertEqual(set(out), {"AAPL", "MSFT"})
        self.cfg.DB_PATH = self.scratch


if __name__ == "__main__":
    unittest.main()
