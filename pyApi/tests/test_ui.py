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
import warnings

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
        # the picker renders one checkbox per symbol (key symcb_<SYM>)
        keys = {c.key for c in self.at.sidebar.checkbox}
        for sym in SYMBOLS:
            self.assertIn(f"symcb_{sym}", keys)

    def test_default_selection_non_empty(self):
        # some symbols are pre-checked on first load so the tabs have data
        checked = [c for c in self.at.sidebar.checkbox if c.value]
        self.assertTrue(checked, "expected some symbols selected by default")

    def test_no_error_boxes(self):
        self.assertEqual([e.value for e in self.at.error], [])


def _checkbox(at, key):
    got = [c for c in at.sidebar.checkbox if c.key == key]
    assert got, f"checkbox {key} not found in sidebar"
    return got[0]


class TestSidebarInteraction(unittest.TestCase):
    """Driving the checkbox picker re-runs the app cleanly."""

    def test_single_symbol_selection(self):
        at = run_app()
        click_button(at, "Clear")                       # start from empty
        _checkbox(at, "symcb_AAPL").set_value(True).run()
        self.assertEqual([e.value for e in at.exception], [])
        checked = [c.key for c in at.sidebar.checkbox if c.value]
        self.assertEqual(checked, ["symcb_AAPL"])       # exactly one active

    def test_empty_selection_stops_with_hint(self):
        at = run_app()
        click_button(at, "Clear")                       # unchecks every symbol
        self.assertEqual([e.value for e in at.exception], [])
        checked = [c for c in at.sidebar.checkbox if c.value]
        self.assertEqual(checked, [])
        self.assertTrue(at.info, "expected the 'select at least one symbol' hint")


class TestSidebarSymbolPicker(unittest.TestCase):
    """The filter + Select-all/Clear behaviour of the checkbox picker."""

    def test_filter_narrows_the_visible_checkboxes(self):
        at = run_app()
        [t for t in at.sidebar.text_input
         if t.key == "sym_filter"][0].set_value("AAPL").run()
        keys = {c.key for c in at.sidebar.checkbox}
        self.assertIn("symcb_AAPL", keys)
        self.assertNotIn("symcb_MSFT", keys)   # filtered out of the list

    def test_select_all_checks_every_symbol(self):
        at = run_app()
        click_button(at, "Select all")
        boxes = at.sidebar.checkbox
        self.assertTrue(boxes)
        self.assertTrue(all(c.value for c in boxes))


class TestSidebarDateRange(unittest.TestCase):
    """Preset ranges + the Custom calendar."""

    def test_max_preset_spans_full_data_range(self):
        # fixture data starts 2022-01-03 → the Max caption reflects it
        at = run_app(date_preset="Max")
        caps = " ".join(c.value for c in at.sidebar.caption)
        self.assertIn("2022-01-03", caps)

    def test_custom_preset_reveals_bounded_calendar(self):
        at = run_app(date_preset="Custom")
        self.assertEqual([e.value for e in at.exception], [])
        rng = [d for d in at.date_input if d.key == "date_range"]
        self.assertTrue(rng, "Custom mode should show the date_range calendar")


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

    def test_default_range_gives_real_scores(self):
        # Regression: a too-short default date range (the 1Y sidebar preset)
        # left every symbol below the horizon's min_bars, so score_symbol
        # applied a -30 thin-data penalty and all scores collapsed to ~1.
        # The default range must be wide enough to produce meaningful scores.
        results = self.at.session_state["score_results"]
        self.assertGreater(
            max(r["score"] for r in results), 10,
            "all scores near-zero → default date range too short for horizon")


class TestScoreRangeWarning(unittest.TestCase):
    """A date range too short for the horizon warns instead of silently
    returning penalised/empty results."""

    def test_one_month_range_warns_not_silent(self):
        at = run_app(date_preset="1M")          # ~21 daily bars
        click_button(at, "Run scoring")
        self.assertEqual([e.value for e in at.exception], [])
        self.assertFalse(at.session_state["score_results"],
                         "1M can't satisfy the quarter horizon")
        warns = " ".join(w.value for w in at.warning).lower()
        self.assertIn("history", warns,
                      "expected a 'needs a longer history' warning")
        self.assertIn("not missing price data", warns,
                      "warning must clarify the prices aren't missing")


class TestBriefingRangeWarning(unittest.TestCase):
    """Briefing over a too-short range explains it's a history-length issue
    — regression: it used to say 'No data found. Run stock_collector.py'
    even though the prices were present."""

    def _preview(self, at):
        btn = [b for b in at.button if "Preview" in b.label]
        self.assertTrue(btn, "Preview prompt button not found")
        btn[0].click()
        at.run()
        return at

    def test_short_range_says_not_missing_data(self):
        at = self._preview(run_app(date_preset="1M"))
        warns = " ".join(w.value for w in at.warning).lower()
        self.assertIn("not missing data", warns,
                      "briefing must not imply the prices are gone")
        self.assertNotIn("stock_collector", warns,
                         "the misleading 'run stock_collector' message is gone")

    def test_default_range_builds_prompt(self):
        at = self._preview(run_app())            # default 5Y range
        self.assertEqual([e.value for e in at.exception], [])
        self.assertTrue(at.session_state["brief_prompt"],
                        "briefing prompt should build over the default range")


class TestScoreBacktestUI(unittest.TestCase):
    """The self-validating 'Does this score predict returns?' section in
    the Score tab drives stock_toolkit.score_validation and renders the
    verdict without exceptions."""

    def _run_bt(self, at):
        btn = [b for b in at.button if "Run score backtest" in b.label]
        self.assertTrue(btn, "score-backtest button not found")
        btn[0].click()
        at.run()
        return at

    def test_backtest_runs_clean_and_stores_result(self):
        at = self._run_bt(run_app())
        self.assertEqual([e.value for e in at.exception], [])
        self.assertIn("score_bt", at.session_state)
        self.assertIn("verdict", at.session_state["score_bt"])

    def test_short_lookback_produces_observations(self):
        # 2-year lookback fits the ~3-year fixture → the walk-forward
        # actually scores something (the empty path is the 5y default).
        at = run_app(score_bt_lookback=2, score_bt_rebal=3)
        at = self._run_bt(at)
        self.assertEqual([e.value for e in at.exception], [])
        self.assertGreater(at.session_state["score_bt"]["n_obs"], 0)


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

    def test_every_analysis_tool_renders(self):
        """Drive the 'Tool' radio through all 7 options — each renders a
        different chart/table branch."""
        tools = ["Summary", "Price (compare)", "Drawdown (compare)",
                 "Correlation", "RSI", "Bollinger Bands", "Monte Carlo"]
        for tool in tools:
            with self.subTest(tool=tool):
                at = run_app()
                radio = [r for r in at.radio if r.key == "an_tool"]
                self.assertTrue(radio, "an_tool radio not found")
                radio[0].set_value(tool)
                at.run()
                self.assertEqual([e.value for e in at.exception], [],
                                 f"exception rendering {tool}")


class TestAlertsTabInteraction(unittest.TestCase):
    """Clicking 'Check alerts' evaluates the default conditions against
    the fixture DB and renders the results table (no network)."""

    def test_check_alerts_renders_results(self):
        at = run_app()
        btn = [b for b in at.button if "Check alerts" in b.label]
        self.assertTrue(btn, "Check alerts button not found")
        btn[0].click()
        at.run()
        self.assertEqual([e.value for e in at.exception], [])
        # results are stashed in session state and rendered
        self.assertIn("alert_results", at.session_state)


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


class TestTradeProposalParser(unittest.TestCase):
    """Parser pulls a fenced TRADE_PROPOSALS_JSON block out of Claude's reply."""

    def test_extracts_proposals_and_strips_block(self):
        from stock_toolkit.ui.tabs.briefing import _parse_trade_proposals

        reply = (
            "Here's my read. AAPL looks set up, GOOGL not so much.\n\n"
            "<<<TRADE_PROPOSALS_JSON\n"
            '[{"side":"BUY","symbol":"AAPL","amount_chf":200,"reason":"momentum"},'
            '{"side":"SELL","symbol":"GOOGL","qty_pct":50,"reason":"breakdown"}]\n'
            ">>>\n\nLet me know if you want detail on any of these."
        )
        props, cleaned = _parse_trade_proposals(reply)
        self.assertEqual(len(props), 2)
        self.assertEqual(props[0]["side"],   "BUY")
        self.assertEqual(props[0]["symbol"], "AAPL")
        self.assertEqual(props[0]["amount_chf"], 200)
        self.assertEqual(props[1]["side"],   "SELL")
        self.assertEqual(props[1]["qty_pct"], 50)
        # Block is gone from the rendered text.
        self.assertNotIn("<<<", cleaned)
        self.assertNotIn("TRADE_PROPOSALS_JSON", cleaned)
        self.assertIn("AAPL looks set up", cleaned)
        self.assertIn("Let me know if you want detail", cleaned)

    def test_no_block_returns_empty_and_text_unchanged(self):
        from stock_toolkit.ui.tabs.briefing import _parse_trade_proposals

        reply = "Free-form reply with no proposals."
        props, cleaned = _parse_trade_proposals(reply)
        self.assertEqual(props, [])
        self.assertEqual(cleaned, reply)

    def test_malformed_json_falls_back_safely(self):
        from stock_toolkit.ui.tabs.briefing import _parse_trade_proposals

        reply = (
            "thinking...\n<<<TRADE_PROPOSALS_JSON\n[ broken json ]\n>>>\nend."
        )
        props, cleaned = _parse_trade_proposals(reply)
        # Bad JSON → no proposals, original text returned unmodified so the
        # user at least sees what Claude tried to say.
        self.assertEqual(props, [])
        self.assertEqual(cleaned, reply)

    def test_empty_text_is_safe(self):
        from stock_toolkit.ui.tabs.briefing import _parse_trade_proposals

        self.assertEqual(_parse_trade_proposals(""),   ([], ""))
        self.assertEqual(_parse_trade_proposals(None), ([], None))


class TestFundamentals(unittest.TestCase):
    """yfinance valuation snapshot: fetch (mocked) and prompt formatting."""

    INFO = {"trailingPE": 35.2, "forwardPE": 30.3,
            "revenueGrowth": 0.166, "earningsGrowth": 0.218}

    def _install_fake_yf(self, info_by_sym):
        import types
        from unittest import mock

        class FakeTicker:
            def __init__(self, sym):
                self._sym = sym

            @property
            def info(self):
                val = info_by_sym[self._sym]
                if isinstance(val, Exception):
                    raise val
                return val

        fake = types.ModuleType("yfinance")
        fake.Ticker = FakeTicker
        patcher = mock.patch.dict(sys.modules, {"yfinance": fake})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_get_fundamentals_parses_and_skips_failures(self):
        from stock_toolkit.ui import helpers

        self._install_fake_yf({
            "AAPL": dict(self.INFO),
            "EMPTY": {},                          # no fields → omitted
            "BOOM": RuntimeError("rate limited"),  # error → omitted
        })
        helpers.get_fundamentals.clear()
        try:
            out = helpers.get_fundamentals(("AAPL", "EMPTY", "BOOM"))
        finally:
            helpers.get_fundamentals.clear()
        self.assertEqual(list(out), ["AAPL"])
        self.assertEqual(out["AAPL"]["forward_pe"], 30.3)
        self.assertEqual(out["AAPL"]["revenue_growth"], 0.166)

    def test_summary_formats_values_and_missing(self):
        from stock_toolkit.ui.tabs.briefing import _fundamentals_to_summary

        table = _fundamentals_to_summary({
            "AAPL": {"trailing_pe": 35.2, "forward_pe": 30.3,
                     "revenue_growth": 0.166, "earnings_growth": None},
        })
        self.assertIn("AAPL", table)
        self.assertIn("35.2", table)
        self.assertIn("+16.6%", table)
        self.assertIn("n/a", table)

    def test_empty_dict_gives_empty_string(self):
        from stock_toolkit.ui.tabs.briefing import _fundamentals_to_summary

        self.assertEqual(_fundamentals_to_summary({}), "")


class TestAlertSummary(unittest.TestCase):
    """The CURRENT INDICATORS block must survive partial indicator data.

    Regression: the builder used to format %B and change unconditionally,
    so a symbol with an RSI alert but no Bollinger %B (a None) raised
    TypeError, and a real change of 0.0% was silently dropped by an
    `if chg` truthiness check."""

    def test_rsi_only_no_pct_b_does_not_crash(self):
        from stock_toolkit.ui.tabs.briefing import _alerts_to_summary

        out = _alerts_to_summary({
            "AAPL": {"rsi14": 72.0, "bbands_pct_b": None,
                     "bbands_squeeze": False, "change_pct": 1.3},
        })
        self.assertIn("AAPL", out)
        self.assertIn("RSI=72", out)
        self.assertNotIn("%B", out)
        self.assertIn("change=+1.3%", out)

    def test_pct_b_only_no_rsi(self):
        from stock_toolkit.ui.tabs.briefing import _alerts_to_summary

        out = _alerts_to_summary({
            "MSFT": {"rsi14": None, "bbands_pct_b": 0.95,
                     "bbands_squeeze": True, "change_pct": None},
        })
        self.assertIn("%B=0.95", out)
        self.assertNotIn("RSI", out)
        self.assertIn("⚡SQUEEZE", out)
        self.assertNotIn("change", out)

    def test_both_missing_skips_symbol(self):
        from stock_toolkit.ui.tabs.briefing import _alerts_to_summary

        out = _alerts_to_summary({
            "NVDA": {"rsi14": None, "bbands_pct_b": None,
                     "bbands_squeeze": False, "change_pct": 2.0},
        })
        self.assertEqual(out, "")

    def test_zero_change_is_reported_not_dropped(self):
        from stock_toolkit.ui.tabs.briefing import _alerts_to_summary

        out = _alerts_to_summary({
            "TSLA": {"rsi14": 50.0, "bbands_pct_b": 0.5,
                     "bbands_squeeze": False, "change_pct": 0.0},
        })
        self.assertIn("change=+0.0%", out)

    def test_empty_context_gives_empty_string(self):
        from stock_toolkit.ui.tabs.briefing import _alerts_to_summary

        self.assertEqual(_alerts_to_summary({}), "")


class TestBriefingStateSummary(unittest.TestCase):
    """The proposal-prompt snapshot of the Briefing paper-trading strategy.

    Exercises the three branches of _briefing_state_summary against an
    isolated portfolio.db: strategy not created yet, created but flat,
    and created with an open position. Prices are stubbed so the test
    needs no stock_data.db."""

    def setUp(self):
        from unittest import mock

        import stock_toolkit.game as game
        self._game = game
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db = pathlib.Path(self._tmp.name) / "portfolio.db"

        # Isolate the portfolio DB and stub the price feed (both buy()
        # and mark_to_market() reference get_latest_price as a module
        # global, so one patch covers both).
        p_db = mock.patch.object(game, "DEFAULT_PORTFOLIO_DB", db)
        p_px = mock.patch.object(game, "get_latest_price",
                                 return_value=(100.0, "2026-06-29"))
        p_db.start(); p_px.start()
        self.addCleanup(p_db.stop)
        self.addCleanup(p_px.stop)

    def test_not_created_yet_returns_placeholder(self):
        from stock_toolkit.ui.tabs.briefing import _briefing_state_summary

        out = _briefing_state_summary()
        self.assertIn("has not been created yet", out)

    def test_created_but_flat_lists_no_positions(self):
        from stock_toolkit.game import create_portfolio
        from stock_toolkit.ui.tabs.briefing import (
            BRIEFING_STRATEGY_NAME, _briefing_state_summary,
        )

        create_portfolio(BRIEFING_STRATEGY_NAME, starting_cash=10_000.0,
                         activate=False)
        out = _briefing_state_summary()
        self.assertIn(BRIEFING_STRATEGY_NAME, out)
        self.assertIn("Cash:", out)
        self.assertIn("10,000.00", out)
        self.assertIn("+0.00% from inception", out)
        self.assertIn("Open positions: none", out)

    def test_created_with_holding_renders_position_line(self):
        from stock_toolkit.game import buy, create_portfolio
        from stock_toolkit.ui.tabs.briefing import (
            BRIEFING_STRATEGY_NAME, _briefing_state_summary,
        )

        rec = create_portfolio(BRIEFING_STRATEGY_NAME, starting_cash=10_000.0,
                              activate=False)
        buy("AAPL", 1_000.0, portfolio_id=rec["id"])
        out = _briefing_state_summary()
        self.assertIn("Open positions:", out)
        self.assertNotIn("Open positions: none", out)
        self.assertIn("AAPL", out)
        self.assertIn("qty=", out)
        self.assertIn("P/L=", out)


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


class TestBriefingTradePanel(unittest.TestCase):
    """The briefing → game inline trade form renders against the same
    fixture STOCK_DIR the other UI tests use. Verifies the panel shows
    the symbols Claude saw, the active strategy info, and the Buy button."""

    def test_panel_renders_with_active_portfolio(self):
        import tempfile as _tf
        from streamlit.testing.v1 import AppTest

        from stock_toolkit.game import init_portfolio
        init_portfolio()

        driver = (
            "import sys\n"
            f"sys.path.insert(0, {str(PKG_ROOT)!r})\n"
            "from stock_toolkit.ui.tabs.briefing import _briefing_trade_panel\n"
            "_briefing_trade_panel([\n"
            "    {'symbol': 'AAPL'},\n"
            "    {'symbol': 'MSFT'},\n"
            "    {'symbol': 'GOOGL'},\n"
            "])\n"
        )
        with _tf.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(driver)
            driver_path = f.name
        try:
            at = AppTest.from_file(driver_path, default_timeout=60)
            at.run()
        finally:
            import os as _os
            _os.unlink(driver_path)

        self.assertEqual([e.value for e in at.exception], [])
        symbol_selects = [s for s in at.selectbox if s.key == "brief_trade_sym"]
        self.assertEqual(len(symbol_selects), 1)
        self.assertEqual(set(symbol_selects[0].options),
                         {"AAPL", "MSFT", "GOOGL"})
        self.assertTrue(any("Buy into active strategy" in b.label
                            for b in at.button))


class TestAdminPageRenders(unittest.TestCase):
    """Admin page (⚙️) renders against the fixture data dir."""

    def test_renders_without_exceptions(self):
        from streamlit.testing.v1 import AppTest as _AppTest

        # Drive the actual page shim — same code path Streamlit uses,
        # exercises the sys.path setup inside the shim, and verifies
        # the emoji/digit filename doesn't trip the runner.
        page = PKG_ROOT / "stock_toolkit" / "ui" / "pages" / "01_⚙️_Admin.py"
        self.assertTrue(page.exists(), f"missing page file: {page}")

        at = _AppTest.from_file(str(page), default_timeout=60)
        at.run()
        self.assertEqual([e.value for e in at.exception], [])
        markdown_text = "\n".join(m.value for m in at.markdown)
        for heading in ("Watchlist", "Collect", "Inventory", "Suppressed"):
            self.assertIn(heading, markdown_text,
                          f"admin page missing '{heading}' section")
        # v1.13: API Keys expander is present (renders as a caption).
        captions = "\n".join(c.value for c in at.caption)
        self.assertIn("Add free API keys here", captions,
                      "admin page missing API Keys section caption")


class TestApiKeySave(unittest.TestCase):
    """Saving keys via update_config_value round-trips through config.env.

    This is a unit test on the writer that the Admin page calls; it
    doesn't go through the Streamlit form (covered by the render
    test) but it does verify the persistence contract."""

    def test_saved_key_round_trips_and_reload_picks_it_up(self):
        import tempfile
        from pathlib import Path
        from stock_toolkit.common import (
            load_config, update_config_value,
        )

        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "config.env"
            cfg_path.write_text(
                "SYMBOLS=AAPL,MSFT\n"
                "ALPHAVANTAGE_KEY=existing_av_key\n"
                "# comment line\n"
            )

            # Add a brand-new key (Anthropic wasn't in the file).
            update_config_value(
                "ANTHROPIC_API_KEY", "sk-ant-fake", cfg_path,
            )
            # Replace an existing key.
            update_config_value(
                "ALPHAVANTAGE_KEY", "new_av_key", cfg_path,
            )

            cfg = load_config(cfg_path)
            self.assertEqual(cfg["ANTHROPIC_API_KEY"], "sk-ant-fake")
            self.assertEqual(cfg["ALPHAVANTAGE_KEY"],  "new_av_key")
            self.assertEqual(cfg["SYMBOLS"],           "AAPL,MSFT")
            # Comment line preserved.
            self.assertIn("# comment line", cfg_path.read_text())

    def test_reload_config_mutates_in_place(self):
        """reload_config() must mutate the dict, not rebind it, so
        importers that captured the reference still see new values."""
        from unittest import mock
        from stock_toolkit.ui import helpers

        captured = helpers._cfg
        original_state = dict(captured)
        try:
            # Pretend config.env now contains a different mapping.
            with mock.patch(
                "stock_toolkit.ui.helpers.load_config",
                return_value={"FOO": "from_disk", "BAR": "also_disk"},
            ):
                helpers.reload_config()

            self.assertIs(captured, helpers._cfg,
                          "_cfg was rebound — importers that captured a "
                          "reference will not see updated values")
            self.assertEqual(captured["FOO"], "from_disk")
            self.assertEqual(captured["BAR"], "also_disk")
        finally:
            # Restore the real cfg so later tests aren't polluted.
            captured.clear()
            captured.update(original_state)


class TestIconRegistry(unittest.TestCase):
    """Central icon mapping is well-formed and used by the UI."""

    def test_every_semantic_maps_to_a_known_concept(self):
        from stock_toolkit.ui import icons
        for sem, token in icons.SEMANTIC.items():
            self.assertIn(token, icons.GLYPHS,
                          f"semantic '{sem}' points at unknown concept "
                          f"'{token}'")

    def test_icon_returns_glyph_for_known_name(self):
        from stock_toolkit.ui.icons import icon
        self.assertEqual(icon("tab.score"),    "◉")
        self.assertEqual(icon("tab.briefing"), "✦")
        self.assertEqual(icon("page.admin"),   "⚙️")
        self.assertEqual(icon("page.game"),    "🎮")
        self.assertEqual(icon("page.help"),    "❓")

    def test_icon_falls_back_for_unknown_name(self):
        from stock_toolkit.ui.icons import icon
        self.assertEqual(icon("nonexistent.thing"), "?")

    def test_tab_label_format(self):
        from stock_toolkit.ui.icons import tab_label
        self.assertEqual(tab_label("tab.score", "Score"), "◉  Score")

    def test_heading_format(self):
        from stock_toolkit.ui.icons import heading
        self.assertEqual(
            heading("watchlist", "Watchlist"), "### ▪  Watchlist",
        )
        self.assertEqual(
            heading("watchlist", "Watchlist", level=2),
            "## ▪  Watchlist",
        )

    def test_concept_change_propagates_everywhere(self):
        """Restyling = one GLYPHS edit. Verify by patching."""
        from unittest import mock
        from stock_toolkit.ui import icons

        with mock.patch.dict(icons.GLYPHS, {"achievement": "★"}):
            # Every element mapped to "achievement" now uses ★.
            self.assertEqual(icons.icon("tab.score"),     "★")
            self.assertEqual(icons.icon("outcome_stats"), "★")
            # Other concepts unaffected.
            self.assertEqual(icons.icon("tab.analysis"),  "◆")


class TestDataDirMigration(unittest.TestCase):
    """v1.17 — single DATA_DIR + auto-migration of legacy loose state.

    Exercises the _resolve_data_dir() + _auto_migrate() helpers as
    pure functions against a tempdir BASE_DIR. Each test runs the
    functions in isolation; we don't reimport common.py (it caches
    module-level constants at import time)."""

    def _stage_legacy_layout(self, base):
        """Lay down a v1.16 install: loose DBs at BASE_DIR/ plus a
        bootstrap historical at BASE_DIR/data/. Returns the staged
        files for assertion."""
        (base / "stock_data.db").write_bytes(b"live\0")
        (base / "stock_failures.db").write_bytes(b"fail\0")
        (base / "portfolio.db").write_bytes(b"port\0")
        (base / ".collector_state.json").write_text('{"a":1}')
        (base / ".alerts_state.json").write_text('{"b":2}')
        (base / "data").mkdir()
        (base / "data" / "stock_data_2025-2026.db").write_bytes(b"hist\0")

    def test_migration_moves_loose_files(self):
        from unittest import mock as _mock

        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            self._stage_legacy_layout(base)
            data_dir = base / "data"

            with _mock.patch.object(_common, "BASE_DIR", base):
                _common._auto_migrate(data_dir)

            for name in (
                "stock_data.db", "stock_failures.db", "portfolio.db",
                ".collector_state.json", ".alerts_state.json",
            ):
                self.assertFalse((base / name).exists(),
                                 f"{name} should have moved out of BASE_DIR")
                self.assertTrue((data_dir / name).exists(),
                                f"{name} should now live in DATA_DIR")
            # Historicals renamed too.
            self.assertTrue(
                (data_dir / "historical" / "stock_data_2025-2026.db").exists()
            )

    def test_migration_is_idempotent(self):
        """Running the migration a second time is a no-op — no files
        change, no exception."""
        from unittest import mock as _mock

        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            self._stage_legacy_layout(base)
            data_dir = base / "data"

            with _mock.patch.object(_common, "BASE_DIR", base):
                _common._auto_migrate(data_dir)
                snapshot = {
                    p.name: p.stat().st_mtime_ns
                    for p in data_dir.iterdir() if p.is_file()
                }
                # Re-run.
                _common._auto_migrate(data_dir)
                after = {
                    p.name: p.stat().st_mtime_ns
                    for p in data_dir.iterdir() if p.is_file()
                }
            self.assertEqual(snapshot, after)

    def test_migration_skipped_when_data_dir_equals_base(self):
        """Docker / OUTPUT_DIR=BASE_DIR: data already lives at the
        root, the loose-files step is a no-op. Only the historical
        rename runs."""
        from unittest import mock as _mock

        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            # Lay out the Docker pre-v1.17 mess: state at root,
            # historicals at root/data/.
            self._stage_legacy_layout(base)
            with _mock.patch.object(_common, "BASE_DIR", base):
                _common._auto_migrate(base)
            # Loose files stay put.
            self.assertTrue((base / "stock_data.db").exists())
            # Historicals get renamed to base/historical/.
            self.assertTrue(
                (base / "historical" / "stock_data_2025-2026.db").exists()
            )

    def test_migration_never_overwrites_existing_target(self):
        """If a target file already exists at DATA_DIR (user already
        migrated by hand), the source is preserved untouched."""
        from unittest import mock as _mock

        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            data_dir = base / "data"
            data_dir.mkdir()
            (base / "stock_data.db").write_bytes(b"old_contents")
            (data_dir / "stock_data.db").write_bytes(b"new_contents")

            with _mock.patch.object(_common, "BASE_DIR", base):
                _common._auto_migrate(data_dir)

            self.assertEqual(
                (data_dir / "stock_data.db").read_bytes(), b"new_contents",
                "migration must NOT overwrite an existing destination",
            )
            # And the source was left alone for the user to investigate.
            self.assertTrue((base / "stock_data.db").exists())


class TestDataDirConfigDeprecation(unittest.TestCase):
    """v1.19 — DATA_DIR is the new user-facing name; OUTPUT_DIR still
    works for back-compat with a DeprecationWarning. Covers the three
    precedence branches in _resolve_data_dir()."""

    def _resolve_with(self, cfg_text):
        """Run _resolve_data_dir() against a tempdir CONFIG_PATH."""
        from unittest import mock as _mock
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            cfg = base / "config.env"
            cfg.write_text(cfg_text)
            with _mock.patch.object(_common, "BASE_DIR", base), \
                 _mock.patch.object(_common, "CONFIG_PATH", cfg), \
                 _mock.patch.dict(os.environ, {}, clear=False):
                # Make sure STOCK_DIR doesn't leak through to the
                # final fallback branch.
                os.environ.pop("STOCK_DIR", None)
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    result = _common._resolve_data_dir()
                return result, caught

    def test_data_dir_wins_no_warning(self):
        path, warns = self._resolve_with("DATA_DIR=/tmp/sanity_dd\n")
        self.assertEqual(path, pathlib.Path("/tmp/sanity_dd").resolve())
        self.assertFalse(
            any(issubclass(w.category, DeprecationWarning) for w in warns),
            "DATA_DIR alone must not emit a DeprecationWarning",
        )

    def test_legacy_output_dir_still_resolves(self):
        path, warns = self._resolve_with("OUTPUT_DIR=/tmp/sanity_od\n")
        self.assertEqual(path, pathlib.Path("/tmp/sanity_od").resolve())
        self.assertTrue(
            any(issubclass(w.category, DeprecationWarning)
                and "OUTPUT_DIR" in str(w.message) for w in warns),
            "OUTPUT_DIR must emit a DeprecationWarning",
        )

    def test_data_dir_overrides_output_dir(self):
        path, warns = self._resolve_with(
            "OUTPUT_DIR=/tmp/old\nDATA_DIR=/tmp/new\n"
        )
        self.assertEqual(path, pathlib.Path("/tmp/new").resolve())
        self.assertFalse(
            any(issubclass(w.category, DeprecationWarning) for w in warns),
            "DATA_DIR taking precedence must not warn — the user already "
            "migrated; OUTPUT_DIR is harmless residue.",
        )


class TestThemeApplied(unittest.TestCase):
    """v1.16 — every page calls setup_page() so the dark sidebar /
    background CSS is applied uniformly. Without this, sidebar pages
    fall back to Streamlit's default light theme."""

    def test_every_renderable_calls_setup_page(self):
        for mod_name in (
            "stock_toolkit.ui.app",
            "stock_toolkit.ui.admin",
            "stock_toolkit.ui.game",
            "stock_toolkit.ui.help",
        ):
            import importlib
            import inspect
            mod = importlib.import_module(mod_name)
            src = inspect.getsource(mod)
            self.assertIn(
                "setup_page", src,
                f"{mod_name} does not call setup_page() — sidebar pages "
                "will render in Streamlit's light default theme.",
            )

    def test_theme_module_exports_setup_page(self):
        from stock_toolkit.ui import theme
        self.assertTrue(callable(getattr(theme, "setup_page", None)))


class TestAdminSettingsRoundTrip(unittest.TestCase):
    """v1.15 — the Settings expander writes all the right keys to config.env."""

    def test_settings_keys_round_trip(self):
        """Each field maps to its expected config.env key via the same
        update_config_value() writer the Settings expander uses."""
        import tempfile
        from pathlib import Path
        from stock_toolkit.common import load_config, update_config_value

        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "config.env"
            cfg_path.write_text("SYMBOLS=AAPL\n")

            # Mimic what the Save settings button does end-to-end.
            updates = {
                "FINNHUB_PAID":       "true",
                "ALPHAVANTAGE_PAID":  "false",
                "UI_COLLECT_SOURCES": "yfinance,finnhub",
                "ALERT_EMAIL":        "you@example.com",
                "ALERT_SMTP_HOST":    "smtp.gmail.com",
                "ALERT_SMTP_PORT":    "587",
                "ALERT_SMTP_USER":    "you@gmail.com",
                "ALERT_SMTP_PASS":    "app-pw",
                "PUSHOVER_USER_KEY":  "po-user",
                "PUSHOVER_APP_TOKEN": "po-token",
                "SLACK_WEBHOOK_URL":  "https://hooks.slack.com/services/T/B/X",
            }
            for k, v in updates.items():
                update_config_value(k, v, cfg_path)

            cfg = load_config(cfg_path)
            for k, expected in updates.items():
                self.assertEqual(
                    cfg.get(k), expected,
                    f"{k}: expected {expected!r}, got {cfg.get(k)!r}",
                )
            # SYMBOLS was preserved through every write.
            self.assertEqual(cfg["SYMBOLS"], "AAPL")


class TestAdminInteraction(unittest.TestCase):
    """Click the Admin page's offline buttons — watchlist/keys/settings
    save, sanity check, and inventory summary/check. The network buttons
    (Run collection / Bootstrap) are deliberately NOT clicked."""

    PAGE = PKG_ROOT / "stock_toolkit" / "ui" / "pages" / "01_⚙️_Admin.py"

    def _page(self):
        from streamlit.testing.v1 import AppTest as _AppTest
        at = _AppTest.from_file(str(self.PAGE), default_timeout=60)
        at.run()
        return at

    def _click_and_check(self, by, value):
        at = self._page()
        if by == "key":
            btns = [b for b in at.button if b.key == value]
        else:
            btns = [b for b in at.button if value in b.label]
        if not btns:
            self.skipTest(f"button {value} not present")
        btns[0].click()
        at.run()
        self.assertEqual([e.value for e in at.exception], [],
                         f"exception after clicking {value}")

    def test_save_watchlist(self):
        self._click_and_check("label", "Save watchlist")

    def test_save_keys(self):
        self._click_and_check("key", "adm_save_keys")

    def test_save_settings(self):
        self._click_and_check("key", "adm_save_settings")

    def test_run_sanity(self):
        self._click_and_check("key", "adm_run_sanity")

    def test_inventory_summary(self):
        self._click_and_check("label", "Summary")

    def test_inventory_check_gaps(self):
        self._click_and_check("label", "Check gaps")


class TestGamePageInteraction(unittest.TestCase):
    """Drive the Game page forms: create a strategy, buy, then sell —
    covering the interaction branches (no network; prices from fixture)."""

    PAGE = PKG_ROOT / "stock_toolkit" / "ui" / "pages" / "02_🎮_Game.py"

    def setUp(self):
        # Isolate the portfolio DB explicitly. Under `unittest discover`,
        # another module can import stock_toolkit.common before test_ui sets
        # STOCK_DIR, locking DATA_DIR to the dev tree — which would make these
        # create/buy/sell forms write into the developer's real portfolio.db.
        import tempfile
        from unittest import mock
        from stock_toolkit import game
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        p = mock.patch.object(
            game, "DEFAULT_PORTFOLIO_DB",
            pathlib.Path(self._tmp.name) / "portfolio.db")
        p.start()
        self.addCleanup(p.stop)

    def _page(self):
        from streamlit.testing.v1 import AppTest as _AppTest
        at = _AppTest.from_file(str(self.PAGE), default_timeout=60)
        at.run()
        return at

    def _click(self, at, key):
        btn = [b for b in at.button if b.key == key]
        self.assertTrue(btn, f"button {key} not found")
        btn[0].click()
        at.run()
        return at

    def test_create_buy_sell_flow(self):
        import time
        name = f"AppTestStrat{int(time.time() * 1000) % 100000}"

        at = self._page()
        # create + activate a fresh strategy
        at.text_input(key="game_new_name").set_value(name)
        at.number_input(key="game_new_cash").set_value(10_000.0)
        at = self._click(at, "game_new_btn")
        self.assertEqual([e.value for e in at.exception], [])

        # buy: pick a symbol present in the fixture, set an amount, submit
        buy_sym = [s for s in at.selectbox if s.key == "game_buy_sym"]
        if buy_sym and buy_sym[0].options:
            buy_sym[0].set_value(buy_sym[0].options[0])
            amt = [n for n in at.number_input if n.key == "game_buy_amt"]
            if amt:
                amt[0].set_value(1_000.0)
            at = self._click(at, "game_buy_btn")
            self.assertEqual([e.value for e in at.exception], [])

        # sell whatever is now held (if the sell form is present)
        if [b for b in at.button if b.key == "game_sell_btn"]:
            at = self._click(at, "game_sell_btn")
            self.assertEqual([e.value for e in at.exception], [])


class TestGameHistoryExpanderRenders(unittest.TestCase):
    """v2.4.2 — the Game page's History expander reads the audit_log
    table and surfaces every mutation. Smoke-test that the expander +
    its three filter selectboxes are present and the page renders
    without exceptions (the underlying audit-log shape is covered in
    detail by tests/test_audit_log.py)."""

    def test_renders_with_audit_filters(self):
        from streamlit.testing.v1 import AppTest as _AppTest

        page = PKG_ROOT / "stock_toolkit" / "ui" / "pages" / "02_🎮_Game.py"
        self.assertTrue(page.exists(), f"missing page file: {page}")

        at = _AppTest.from_file(str(page), default_timeout=60)
        at.run()
        self.assertEqual([e.value for e in at.exception], [])

        # All three history filters render as selectboxes with stable keys.
        keys = [s.key for s in at.selectbox]
        for k in ("game_audit_scope", "game_audit_prefix",
                  "game_audit_limit"):
            self.assertIn(k, keys, f"missing audit filter selectbox: {k}")

        # The History expander label is on the expander element itself,
        # not in the markdown stream.
        expander_labels = "\n".join(e.label for e in at.expander)
        self.assertIn("History (audit log)", expander_labels,
                      "History expander label missing")
        # The recovery-source caption appears in the captions stream.
        captions = "\n".join(c.value for c in at.caption)
        self.assertIn("Every mutation", captions,
                      "History expander caption missing")


class TestHelpPageRenders(unittest.TestCase):
    """Help page (❓) renders and contains the orientation sections.

    The Help page is static markdown, so the assertion just confirms
    the key headings are present — that's the contract a returning
    user expects."""

    def test_renders_without_exceptions(self):
        from streamlit.testing.v1 import AppTest as _AppTest

        page = PKG_ROOT / "stock_toolkit" / "ui" / "pages" / "03_❓_Help.py"
        self.assertTrue(page.exists(), f"missing page file: {page}")

        at = _AppTest.from_file(str(page), default_timeout=60)
        at.run()
        self.assertEqual([e.value for e in at.exception], [])
        markdown_text = "\n".join(m.value for m in at.markdown)
        for section in ("Where to start", "Main page tabs",
                        "Sidebar pages", "Concepts worth knowing",
                        "Need more?"):
            self.assertIn(section, markdown_text,
                          f"help page missing '{section}' section")


if __name__ == "__main__":
    runner = unittest.main(verbosity=2, exit=False)
    sys.exit(0 if runner.result.wasSuccessful() else 1)
