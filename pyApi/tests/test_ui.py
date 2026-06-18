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
