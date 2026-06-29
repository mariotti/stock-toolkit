"""
test_setup_wizard.py
==================
Coverage-focused tests for stock_toolkit/setup_wizard.py — the config
read/write round-trip, the ask()/ask_yn() prompts (with mocked input),
and the non-interactive wizard + main() (--non-interactive / --show).
No real terminal interaction.
"""
import contextlib
import io
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("MPLBACKEND", "Agg")
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from stock_toolkit import setup_wizard as sw  # noqa: E402


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()), \
         contextlib.redirect_stderr(io.StringIO()):
        return fn(*a, **k)


class TestConfigIO(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = pathlib.Path(self.tmp.name) / "config.env"

    def test_write_then_load_roundtrip_no_template(self):
        sw.write_cfg(self.path, {"SYMBOLS": "AAPL,MSFT", "FINNHUB_KEY": "x"})
        cfg = sw.load_cfg(self.path)
        self.assertEqual(cfg["SYMBOLS"], "AAPL,MSFT")
        self.assertEqual(cfg["FINNHUB_KEY"], "x")

    def test_load_missing_returns_empty(self):
        self.assertEqual(sw.load_cfg(self.path), {})

    def test_write_with_template_preserves_comments(self):
        tmpl = pathlib.Path(self.tmp.name) / "config.env.template"
        tmpl.write_text("# header\nSYMBOLS=\n# note\nFINNHUB_KEY=\n")
        sw.write_cfg(self.path, {"SYMBOLS": "AAPL"}, template=tmpl)
        text = self.path.read_text()
        self.assertIn("# header", text)
        self.assertIn("SYMBOLS=AAPL", text)


class TestPrompts(unittest.TestCase):
    def test_ask_keeps_default_on_empty(self):
        with mock.patch("builtins.input", return_value=""):
            self.assertEqual(_quiet(sw.ask, "Sym", default="AAPL"), "AAPL")

    def test_ask_returns_entered_value(self):
        with mock.patch("builtins.input", return_value="MSFT"):
            self.assertEqual(_quiet(sw.ask, "Sym", default="AAPL"), "MSFT")

    def test_ask_required_loops_until_value(self):
        with mock.patch("builtins.input", side_effect=["", "FILLED"]):
            self.assertEqual(_quiet(sw.ask, "Key", required=True), "FILLED")

    def test_ask_secret_masks(self):
        with mock.patch("builtins.input", return_value=""):
            self.assertEqual(_quiet(sw.ask, "Pass", default="abcdefgh",
                                    secret=True), "abcdefgh")

    def test_ask_yn_variants(self):
        with mock.patch("builtins.input", return_value="y"):
            self.assertTrue(_quiet(sw.ask_yn, "ok?"))
        with mock.patch("builtins.input", return_value="n"):
            self.assertFalse(_quiet(sw.ask_yn, "ok?"))
        with mock.patch("builtins.input", return_value=""):
            self.assertTrue(_quiet(sw.ask_yn, "ok?", default=True))


class TestWizardAndMain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = pathlib.Path(self.tmp.name) / "config.env"
        self._patches = [
            mock.patch.object(sw, "CONFIG_PATH", self.path),
            mock.patch.object(sw, "TEMPLATE_PATH",
                              pathlib.Path(self.tmp.name) / "none.template"),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _main(self, *args, expect_exit=False):
        old = sys.argv
        sys.argv = ["stock-setup", *args]
        try:
            if expect_exit:
                with self.assertRaises(SystemExit):
                    _quiet(sw.main)
            else:
                _quiet(sw.main)
        finally:
            sys.argv = old

    def test_run_wizard_non_interactive(self):
        cfg = _quiet(sw.run_wizard, non_interactive=True)
        self.assertIsInstance(cfg, dict)
        self.assertIn("SYMBOLS", cfg)

    def test_main_non_interactive_writes_config(self):
        self._main("--non-interactive")
        self.assertTrue(self.path.exists())
        self.assertIn("SYMBOLS", sw.load_cfg(self.path))

    def test_main_show_existing(self):
        sw.write_cfg(self.path, {"SYMBOLS": "AAPL", "FINNHUB_KEY": "secret"})
        self._main("--show")

    def test_main_show_missing_exits(self):
        self._main("--show", expect_exit=True)


if __name__ == "__main__":
    unittest.main()
