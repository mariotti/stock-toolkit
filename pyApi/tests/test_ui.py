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
FIXTURE_DIR = pathlib.Path(_tmp.name)
os.environ["STOCK_DIR"] = str(FIXTURE_DIR)

from test_toolkit import SYMBOLS, make_fixture_db  # noqa: E402

make_fixture_db(FIXTURE_DIR)   # writes <tmp>/stock_data.db

# Under `unittest discover`, sibling test modules import stock_toolkit
# alphabetically BEFORE this module sets $STOCK_DIR, freezing the path
# constants to the (possibly DB-less) working directory. Rebind them on the
# already-imported core modules so the dashboard always reads the fixture.
import stock_toolkit.alerts as _sal      # noqa: E402
import stock_toolkit.analysis as _sa     # noqa: E402
import stock_toolkit.backtest as _sb     # noqa: E402
import stock_toolkit.common as _common   # noqa: E402
import stock_toolkit.score as _ss        # noqa: E402

for _mod in (_common, _ss, _sa, _sb, _sal):
    _mod.LIVE_DB  = FIXTURE_DIR / "stock_data.db"
    _mod.HIST_DIR = FIXTURE_DIR / "data"
_common.CONFIG_PATH = FIXTURE_DIR / "config.env"
_sal.STATE_PATH     = FIXTURE_DIR / ".alerts_state.json"

from streamlit.testing.v1 import AppTest  # noqa: E402

APP_PATH = PKG_ROOT / "stock_toolkit" / "ui" / "app.py"


def run_app(**session_state):
    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
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

    def test_exactly_six_tabs_render(self):
        # exactly one tab bar — regression test for a duplicated st.tabs()
        # call that rendered a second, empty tab bar above the real one
        self.assertEqual(len(self.at.tabs), 6)

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


def click_button(at, label_part):
    """Click the first button whose label contains label_part, then rerun."""
    for btn in at.button:
        if label_part in btn.label:
            btn.click()
            at.run()
            return at
    raise AssertionError(
        f"no button matching {label_part!r}; "
        f"have: {[b.label for b in at.button]}")


class TestScoreInteraction(unittest.TestCase):
    """Clicking 'Run scoring' computes and renders ranked results."""

    @classmethod
    def setUpClass(cls):
        cls.at = click_button(run_app(), "Run scoring")

    def test_no_exceptions(self):
        self.assertEqual([e.value for e in self.at.exception], [])

    def test_results_stored_and_rendered(self):
        results = self.at.session_state["score_results"]
        self.assertTrue(results, "expected non-empty score results")
        scored = {r["symbol"] for r in results}
        self.assertIn("AAPL", scored)


class TestBacktestInteraction(unittest.TestCase):
    """Clicking 'Run backtest' runs the default strategy on the first symbol."""

    @classmethod
    def setUpClass(cls):
        cls.at = click_button(run_app(), "Run backtest")

    def test_no_exceptions(self):
        self.assertEqual([e.value for e in self.at.exception], [])

    def test_backtest_state_populated(self):
        self.assertIn("bt_df", self.at.session_state)
        self.assertIn("bt_label", self.at.session_state)


class TestAnalysisInteraction(unittest.TestCase):
    """Changing the analysis symbol/sliders re-renders without errors."""

    def test_slider_change_reruns_clean(self):
        at = run_app()
        rsi = [s for s in at.slider if s.label == "RSI window"]
        self.assertTrue(rsi, "RSI window slider not found")
        rsi[0].set_value(21)
        at.run()
        self.assertEqual([e.value for e in at.exception], [])


class TestBriefingCacheBreakpoints(unittest.TestCase):
    """Prompt-caching markers land on the first and last message only."""

    def test_first_and_last_marked(self):
        from stock_toolkit.ui.tabs.briefing import _with_cache_breakpoints

        msgs = [
            {"role": "user", "content": "big market context"},
            {"role": "assistant", "content": "summary"},
            {"role": "user", "content": "follow-up question"},
        ]
        out = _with_cache_breakpoints(msgs)
        self.assertEqual(out[0]["content"][0]["cache_control"],
                         {"type": "ephemeral"})
        self.assertEqual(out[0]["content"][0]["text"], "big market context")
        self.assertEqual(out[1]["content"], "summary")        # untouched
        self.assertEqual(out[2]["content"][0]["cache_control"],
                         {"type": "ephemeral"})
        self.assertEqual(msgs[0]["content"], "big market context",
                         "input list must not be mutated")

    def test_single_message_marked_once(self):
        from stock_toolkit.ui.tabs.briefing import _with_cache_breakpoints

        out = _with_cache_breakpoints([{"role": "user", "content": "hello"}])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["content"][0]["cache_control"],
                         {"type": "ephemeral"})

    def test_block_content_passes_through(self):
        from stock_toolkit.ui.tabs.briefing import _with_cache_breakpoints

        msgs = [{"role": "user", "content": [{"type": "text", "text": "x"}]}]
        self.assertEqual(_with_cache_breakpoints(msgs), msgs)


class TestEmptyDatabase(unittest.TestCase):
    """With no DB at all the app warns instead of hanging.

    Regression test: score.discover_dbs used to call sys.exit, which froze
    the Streamlit script runner. It now raises NoDataError, which the UI
    helpers catch, leaving an empty symbol list and a sidebar warning."""

    def test_warning_shown(self):
        from stock_toolkit.ui import helpers

        empty_dir = FIXTURE_DIR / "empty"
        empty_dir.mkdir(exist_ok=True)
        old_live, old_hist = _ss.LIVE_DB, _ss.HIST_DIR
        _ss.LIVE_DB  = empty_dir / "stock_data.db"
        _ss.HIST_DIR = empty_dir / "data"
        helpers.get_all_symbols.clear()
        try:
            at = run_app()
            self.assertEqual([e.value for e in at.exception], [])
            self.assertTrue(at.sidebar.warning, "expected the 'no data' warning")
        finally:
            _ss.LIVE_DB, _ss.HIST_DIR = old_live, old_hist
            helpers.get_all_symbols.clear()


if __name__ == "__main__":
    runner = unittest.main(verbosity=2, exit=False)
    sys.exit(0 if runner.result.wasSuccessful() else 1)
