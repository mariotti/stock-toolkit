"""
test_cli_coverage.py
===================
Coverage-focused CLI tests for score.py, alerts.py, and inventory.py —
drives each module's main() through argparse with patched argv against
the shared synthetic fixture DB. Alerts uses --notify console --dry-run
so nothing is actually sent.
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


def _run_main(mod, prog, args, expect_exit=False):
    with contextlib.redirect_stdout(io.StringIO()), \
         contextlib.redirect_stderr(io.StringIO()), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        old = sys.argv
        sys.argv = [prog, *args]
        try:
            if expect_exit:
                raised = False
                try:
                    mod.main()
                except SystemExit:
                    raised = True
                return raised
            mod.main()
        finally:
            sys.argv = old
            import matplotlib.pyplot as plt
            plt.close("all")
    return None


class TestScoreCli(FixtureTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ss = _load_module("score", cls.db, cls.tmp_dir)

    def test_default_all_symbols(self):
        _run_main(self.ss, "stock-score", [])

    def test_explicit_symbols_and_horizon(self):
        _run_main(self.ss, "stock-score",
                  ["-s", "AAPL", "MSFT", "--horizon", "year"])

    def test_detail_and_top(self):
        _run_main(self.ss, "stock-score",
                  ["-s", "AAPL", "MSFT", "--detail", "--top", "1",
                   "--mc-paths", "200"])

    def test_json_output(self):
        _run_main(self.ss, "stock-score", ["-s", "AAPL", "--json"])

    def test_each_horizon(self):
        for h in ("week", "month", "quarter", "year", "life"):
            with self.subTest(horizon=h):
                _run_main(self.ss, "stock-score",
                          ["-s", "AAPL", "--horizon", h, "--mc-paths", "100"])


class TestAlertsCli(FixtureTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sal = _load_module("alerts", cls.db, cls.tmp_dir)

    def test_list_conditions(self):
        _run_main(self.sal, "stock-alerts", ["--list-conditions"])

    def test_status(self):
        _run_main(self.sal, "stock-alerts", ["--status"])

    def test_reset(self):
        _run_main(self.sal, "stock-alerts", ["--reset"])

    def test_evaluate_console_dry_run(self):
        _run_main(self.sal, "stock-alerts",
                  ["-s", "AAPL", "MSFT", "--when", "rsi14 < 70",
                   "--when", "change_pct < -3", "--notify", "console",
                   "--dry-run"])

    def test_squeeze_condition(self):
        _run_main(self.sal, "stock-alerts",
                  ["-s", "AAPL", "--when", "bbands_squeeze",
                   "--notify", "console", "--dry-run"])

    def test_no_symbols_exits(self):
        # missing -s / --when → argparse error → SystemExit
        self.assertTrue(
            _run_main(self.sal, "stock-alerts", ["--notify", "console"],
                      expect_exit=True))


class TestInventoryCli(FixtureTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.inv = _load_module("inventory", cls.db, cls.tmp_dir)
        cls.inv.LIVE_DB = cls.db

    def test_list_all(self):
        _run_main(self.inv, "stock-inventory", [])

    def test_symbol_filter(self):
        _run_main(self.inv, "stock-inventory", ["-s", "AAPL"])

    def test_summary(self):
        _run_main(self.inv, "stock-inventory", ["--summary"])

    def test_json(self):
        _run_main(self.inv, "stock-inventory", ["--json"])

    def test_check(self):
        _run_main(self.inv, "stock-inventory", ["--check"])

    def test_check_specific_symbol(self):
        _run_main(self.inv, "stock-inventory", ["--check", "-s", "AAPL"])

    def _writable_db_copy(self):
        import shutil
        dst = self.tmp_dir / f"inv_copy_{id(self)}.db"
        shutil.copy(self.db, dst)
        return dst

    def test_remove_with_allow_env_deletes(self):
        import os
        import sqlite3
        from unittest import mock
        copy = self._writable_db_copy()
        with mock.patch.object(self.inv, "LIVE_DB", copy), \
             mock.patch.dict(os.environ, {"STOCK_INV_REMOVE": "allow"}):
            _run_main(self.inv, "stock-inventory", ["--remove", "AAPL"])
        con = sqlite3.connect(copy)
        n = con.execute(
            "SELECT count(*) FROM prices WHERE symbol='AAPL'").fetchone()[0]
        con.close()
        self.assertEqual(n, 0, "AAPL should have been deleted")

    def test_remove_prompt_cancelled(self):
        import os
        import sqlite3
        from unittest import mock
        copy = self._writable_db_copy()
        env = {k: v for k, v in os.environ.items() if k != "STOCK_INV_REMOVE"}
        with mock.patch.object(self.inv, "LIVE_DB", copy), \
             mock.patch.dict(os.environ, env, clear=True), \
             mock.patch("builtins.input", return_value="n"):
            _run_main(self.inv, "stock-inventory", ["--remove", "AAPL"])
        con = sqlite3.connect(copy)
        n = con.execute(
            "SELECT count(*) FROM prices WHERE symbol='AAPL'").fetchone()[0]
        con.close()
        self.assertGreater(n, 0, "cancelling the prompt must keep the data")

    def test_remove_unknown_symbol(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"STOCK_INV_REMOVE": "allow"}):
            _run_main(self.inv, "stock-inventory", ["--remove", "ZZZZ"])


if __name__ == "__main__":
    unittest.main()
