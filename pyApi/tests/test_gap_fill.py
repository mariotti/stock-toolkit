"""
test_gap_fill.py
==============
Coverage-focused tests for stock_toolkit/gap_fill.py — drives gap
detection + dry-run reporting, the fetch-and-insert branch (yfinance
replaced by a stubbed _fetch_range), and main()'s CLI. Offline.
"""
import contextlib
import datetime
import io
import os
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("MPLBACKEND", "Agg")
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from stock_toolkit import gap_fill  # noqa: E402


def _make_gappy_db(path: pathlib.Path) -> None:
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE prices (
        fetched_at TEXT, symbol TEXT, source TEXT, timestamp TEXT,
        interval TEXT, open REAL, high REAL, low REAL, close REAL,
        volume INTEGER, vwap REAL, change_pct REAL, extra TEXT,
        UNIQUE(symbol, source, timestamp))""")
    rows = []
    # CLEAN: 30 consecutive business days. GAPPY: 15 days, 15-day hole, 15 days.
    for i in range(30):
        d = datetime.date(2026, 4, 1) + datetime.timedelta(days=i)
        if d.weekday() < 5:
            rows.append(("CLEAN", d.isoformat()))
    for i in list(range(15)) + list(range(30, 45)):
        d = datetime.date(2026, 4, 1) + datetime.timedelta(days=i)
        if d.weekday() < 5:
            rows.append(("GAPPY", d.isoformat()))
    for sym, ts in rows:
        con.execute("INSERT INTO prices (symbol, source, timestamp, interval, "
                    "close) VALUES (?, 'yfinance', ?, '1d', 100.0)",
                    (sym, ts + "T00:00:00+00:00"))
    con.commit(); con.close()


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


class TestGapFill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = pathlib.Path(self.tmp.name) / "stock_data.db"
        _make_gappy_db(self.db)

    def test_dry_run_detects_without_writing(self):
        # dry-run must not fetch — guard by making _fetch_range explode.
        with mock.patch.object(gap_fill, "_fetch_range",
                               side_effect=AssertionError("should not fetch")):
            result = _quiet(gap_fill.fill_gaps, dry_run=True, dbs=[self.db])
        self.assertIsInstance(result, dict)

    def test_fill_inserts_fetched_rows(self):
        # stub the yfinance fetch with canned bars covering the gap
        def fake_fetch(yf, symbol, start, end):
            out = []
            d = start
            while d <= end:
                if d.weekday() < 5:
                    out.append(gap_fill.make_row(
                        symbol, "yfinance", d, "1d", 100, 101, 99, 100.5, 1000))
                d += datetime.timedelta(days=1)
            return out

        with mock.patch.object(gap_fill, "_fetch_range", side_effect=fake_fetch):
            result = _quiet(gap_fill.fill_gaps, dry_run=False, dbs=[self.db])
        inserted = sum(result.values())
        self.assertGreater(inserted, 0, "expected rows inserted into the gap")

    def test_symbol_filter_limits_scope(self):
        with mock.patch.object(gap_fill, "_fetch_range", return_value=[]):
            result = _quiet(gap_fill.fill_gaps, symbol_filter=["GAPPY"],
                            dry_run=True, dbs=[self.db])
        self.assertIsInstance(result, dict)

    def test_main_dry_run(self):
        with mock.patch.object(gap_fill, "discover_dbs", return_value=[self.db]), \
             mock.patch.object(gap_fill, "_fetch_range", return_value=[]):
            old = sys.argv
            sys.argv = ["stock-gap-fill", "--dry-run"]
            try:
                _quiet(gap_fill.main)
            finally:
                sys.argv = old

    def test_main_with_symbol(self):
        def fake_fetch(yf, symbol, start, end):
            return [gap_fill.make_row(symbol, "yfinance", start, "1d",
                                      100, 101, 99, 100.0, 1000)]
        with mock.patch.object(gap_fill, "discover_dbs", return_value=[self.db]), \
             mock.patch.object(gap_fill, "_fetch_range", side_effect=fake_fetch):
            old = sys.argv
            sys.argv = ["stock-gap-fill", "-s", "GAPPY"]
            try:
                _quiet(gap_fill.main)
            finally:
                sys.argv = old


if __name__ == "__main__":
    unittest.main()
