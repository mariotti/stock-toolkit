"""
test_ui.py
==========
Offline tests for the Streamlit dashboard (stock_toolkit/ui/).

Runs the full app through streamlit.testing.v1.AppTest against a synthetic
fixture database — no network, no API keys, no browser needed.

Run:
    python3 tests/test_ui.py
"""

import os
import pathlib
import sys
import tempfile
import unittest

SCRIPT_DIR = pathlib.Path(__file__).parent
PKG_ROOT   = SCRIPT_DIR.parent
sys.path.insert(0, str(PKG_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

# Point the toolkit at a temp data dir BEFORE anything imports stock_toolkit:
# common.BASE_DIR is resolved from $STOCK_DIR at import time.
_tmp = tempfile.TemporaryDirectory()
os.environ["STOCK_DIR"] = _tmp.name

from test_toolkit import SYMBOLS, make_fixture_db  # noqa: E402

make_fixture_db(pathlib.Path(_tmp.name))   # writes <tmp>/stock_data.db

from streamlit.testing.v1 import AppTest  # noqa: E402

APP_PATH = PKG_ROOT / "stock_toolkit" / "ui" / "app.py"


def run_app(**session_state):
    at = AppTest.from_file(str(APP_PATH), default_timeout=120)
    for key, val in session_state.items():
        at.session_state[key] = val
    at.run()
    return at


class TestDashboardRenders(unittest.TestCase):
    """The full app renders against the fixture DB without exceptions."""

    @classmethod
    def setUpClass(cls):
        cls.at = run_app()

    def test_no_exceptions(self):
        errs = [e.value for e in self.at.exception]
        self.assertEqual(errs, [])

    def test_all_six_tabs_render(self):
        # 6 top-level tabs (some tabs nest their own sub-tabs)
        self.assertGreaterEqual(len(self.at.tabs), 6)

    def test_sidebar_lists_fixture_symbols(self):
        ms = self.at.sidebar.multiselect
        self.assertEqual(len(ms), 1)
        for sym in SYMBOLS:
            self.assertIn(sym, ms[0].options)

    def test_default_selection_non_empty(self):
        self.assertTrue(self.at.sidebar.multiselect[0].value)

    def test_no_error_boxes(self):
        self.assertEqual([e.value for e in self.at.error], [])


class TestSidebarInteraction(unittest.TestCase):
    """Changing the symbol selection re-runs the app cleanly."""

    def test_single_symbol_selection(self):
        at = run_app()
        at.sidebar.multiselect[0].set_value(["AAPL"]).run()
        self.assertEqual([e.value for e in at.exception], [])

    def test_empty_selection_stops_with_hint(self):
        at = run_app()
        at.sidebar.multiselect[0].set_value([]).run()
        self.assertEqual([e.value for e in at.exception], [])
        self.assertTrue(at.info, "expected the 'select at least one symbol' hint")


if __name__ == "__main__":
    runner = unittest.main(verbosity=2, exit=False)
    sys.exit(0 if runner.result.wasSuccessful() else 1)
