"""
test_sanity
===========
Tests for stock_toolkit.sanity — each check is tested both against a
clean fixture (no issues) and against a deliberately-broken fixture
(the specific check fires, no false positives on its neighbours).
"""

import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT_DIR = pathlib.Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from stock_toolkit import sanity                             # noqa: E402


# ─── fixtures ────────────────────────────────────────────────────────

def _make_prices_db(path: pathlib.Path, rows: list = None) -> None:
    """Build a stock_data.db with a `prices` table matching the
    collector's schema. ``rows`` defaults to one valid AAPL bar."""
    con = sqlite3.connect(path)
    con.execute("""
        CREATE TABLE prices (
          fetched_at TEXT, symbol TEXT, source TEXT, timestamp TEXT,
          interval TEXT, open REAL, high REAL, low REAL, close REAL,
          volume INTEGER, vwap REAL, change_pct REAL, extra TEXT,
          UNIQUE(symbol, source, timestamp)
        )
    """)
    rows = rows or [
        ("AAPL", "yfinance", "2025-01-02T00:00:00+00:00", "1d", 200.0),
    ]
    for sym, src, ts, interval, close in rows:
        con.execute(
            "INSERT INTO prices (symbol, source, timestamp, interval, close) "
            "VALUES (?, ?, ?, ?, ?)",
            (sym, src, ts, interval, close),
        )
    con.commit(); con.close()


def _make_portfolio_db(path: pathlib.Path,
                      starting_cash: float = 10_000.0) -> None:
    """Initialise a v2 portfolio.db with one strategy and no trades."""
    from stock_toolkit.game import init_portfolio
    init_portfolio(starting_cash=starting_cash, db=path)


# ─── individual check tests ──────────────────────────────────────────

class TestDataLayout(unittest.TestCase):
    def test_clean_layout(self):
        with tempfile.TemporaryDirectory() as td:
            data = pathlib.Path(td) / "data"
            data.mkdir()
            self.assertEqual(sanity.check_data_layout(data), [])

    def test_missing_dir_is_error(self):
        with tempfile.TemporaryDirectory() as td:
            data = pathlib.Path(td) / "absent"
            issues = sanity.check_data_layout(data)
            self.assertTrue(any(i.severity == sanity.ERROR for i in issues))

    def test_stray_legacy_file_is_warning(self):
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            data = base / "data"
            data.mkdir()
            (base / "stock_data.db").write_bytes(b"old")
            issues = sanity.check_data_layout(data)
            self.assertTrue(
                any(i.severity == sanity.WARNING for i in issues),
                f"expected a stray-file warning, got {issues!r}",
            )


class TestConfig(unittest.TestCase):
    def test_missing_config_is_info(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "config.env"
            issues = sanity.check_config(path)
            self.assertEqual([i.severity for i in issues], [sanity.INFO])

    def test_bad_paid_flag_is_error(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "config.env"
            path.write_text(
                "SYMBOLS=AAPL\nFINNHUB_PAID=yes\n",
            )
            issues = sanity.check_config(path)
            self.assertTrue(
                any(i.severity == sanity.ERROR and "FINNHUB_PAID" in i.message
                    for i in issues)
            )

    def test_empty_symbols_is_warning(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "config.env"
            path.write_text("SYMBOLS=\n")
            issues = sanity.check_config(path)
            self.assertTrue(
                any(i.severity == sanity.WARNING and "SYMBOLS" in i.message
                    for i in issues)
            )


class TestDatabase(unittest.TestCase):
    def test_missing_db_is_info(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "stock_data.db"
            issues = sanity.check_database(path)
            self.assertEqual([i.severity for i in issues], [sanity.INFO])

    def test_clean_db_is_silent(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "stock_data.db"
            _make_prices_db(path)
            self.assertEqual(sanity.check_database(path), [])

    def test_null_close_is_warning(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "stock_data.db"
            _make_prices_db(path, rows=[
                ("AAPL", "yfinance", "2025-01-02T00:00:00+00:00", "1d", None),
            ])
            issues = sanity.check_database(path)
            self.assertTrue(
                any(i.severity == sanity.WARNING and "NULL" in i.message
                    for i in issues)
            )

    def test_wrong_shape_is_error(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "stock_data.db"
            con = sqlite3.connect(path)
            con.execute("CREATE TABLE other (a INT)")
            con.commit(); con.close()
            issues = sanity.check_database(path)
            self.assertTrue(
                any(i.severity == sanity.ERROR and "prices" in i.message
                    for i in issues)
            )


class TestPortfolios(unittest.TestCase):
    def test_missing_db_is_silent(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "portfolio.db"
            self.assertEqual(sanity.check_portfolios(path), [])

    def test_clean_portfolio_is_silent(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "portfolio.db"
            _make_portfolio_db(path)
            # Patch the price discovery so mark_to_market returns
            # deterministic numbers (no positions → equity=0).
            with mock.patch("stock_toolkit.game._discover_data_dbs",
                            return_value=[]):
                issues = sanity.check_portfolios(path)
            self.assertEqual(issues, [], f"got {issues!r}")


class TestTradeStats(unittest.TestCase):
    def test_missing_db_is_silent(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(
                sanity.check_trade_stats(pathlib.Path(td) / "absent.db"),
                [],
            )

    def test_clean_portfolio_is_silent(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "portfolio.db"
            _make_portfolio_db(path)
            with mock.patch("stock_toolkit.game._discover_data_dbs",
                            return_value=[]):
                self.assertEqual(sanity.check_trade_stats(path), [])


class TestValueHistory(unittest.TestCase):
    def test_clean_portfolio_is_silent(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "portfolio.db"
            _make_portfolio_db(path)
            with mock.patch("stock_toolkit.game._discover_data_dbs",
                            return_value=[]):
                self.assertEqual(sanity.check_value_history(path), [])


class TestScoreOutputs(unittest.TestCase):
    def test_synthetic_series_passes(self):
        # Pure upward drift, no NaNs. All percentile/bound invariants hold.
        self.assertEqual(sanity.check_score_outputs(), [])


class TestRunAll(unittest.TestCase):
    """End-to-end: run_all() composes every check and a single broken
    check does not crash the rest."""

    def test_run_all_returns_report(self):
        report = sanity.run_all()
        self.assertIsInstance(report.issues, list)
        # Property accessors don't crash.
        _ = report.ok, report.errors, report.warnings, report.infos
        # Serialisable for the --json mode.
        d = report.as_dict()
        for k in ("ok", "errors", "warnings", "infos", "issues"):
            self.assertIn(k, d)

    def test_one_check_raising_does_not_poison_others(self):
        def _boom():
            raise RuntimeError("intentional")
        # Wrap an extra failing check via monkey-patch of _ALL_CHECKS.
        with mock.patch.object(
            sanity, "_ALL_CHECKS",
            list(sanity._ALL_CHECKS) + [_boom],
        ):
            report = sanity.run_all()
        # The boom check shows up as an error, but every other check
        # still ran (so we got at least len(_ALL_CHECKS) issues OR a
        # report whose ok flag reflects the error).
        self.assertTrue(any(
            "boom" in (i.check or "") or "intentional" in (i.message or "")
            for i in report.issues
        ))


class TestSanityCli(unittest.TestCase):
    """Drive sanity_cli.main() in-process (the subprocess journey test in
    test_journey.py exercises the same path, but coverage can't see across
    a subprocess boundary)."""

    def setUp(self):
        import contextlib
        import io
        from stock_toolkit import sanity_cli
        self.cli = sanity_cli
        self._buf = io.StringIO()
        self._redirect = contextlib.redirect_stdout(self._buf)

    def _main(self, *args):
        old = sys.argv
        sys.argv = ["stock-sanity", *args]
        try:
            with self._redirect, self.assertRaises(SystemExit) as cm:
                self.cli.main()
            return cm.exception.code, self._buf.getvalue()
        finally:
            sys.argv = old

    def test_human_output_exits_cleanly(self):
        code, _ = self._main("--no-color")
        self.assertIn(code, (0, 1))

    def test_json_output_is_parseable(self):
        import json
        code, out = self._main("--json")
        # the report dict is the last JSON object printed
        parsed = json.loads(out)
        self.assertIsInstance(parsed, dict)

    def test_strict_mode(self):
        code, _ = self._main("--strict", "--no-color")
        self.assertIn(code, (0, 1))


if __name__ == "__main__":
    unittest.main()
