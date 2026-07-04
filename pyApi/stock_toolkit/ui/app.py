"""
Streamlit dashboard entry — page setup, sidebar, and tab wiring.
All analytical logic stays in the stock_toolkit modules; each tab body
lives in stock_toolkit.ui.tabs.<name>.

Run:
    stock-ui                                  (installed entry point)
    streamlit run stock_toolkit/ui/app.py     (from a source checkout)

The app opens at http://localhost:8501
"""

import sys
from datetime import date
from pathlib import Path
import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import streamlit as st

# ── make the package importable when run as a bare script via streamlit ───────
sys.path.insert(0, str(Path(__file__).parents[2]))

# ── lazy imports with friendly error messages ─────────────────────────────────
try:
    import plotly  # noqa: F401 — fail fast with a friendly message
except ImportError:
    st.error("plotly not installed — run: pip install plotly")
    st.stop()

try:
    from stock_toolkit.ui.helpers import get_all_symbols, get_data_date_range
    from stock_toolkit.ui.tabs import (
        alerts as tabs_alerts,
        analysis as tabs_analysis,
        backtest as tabs_backtest,
        briefing as tabs_briefing,
        collect as tabs_collect,
        score as tabs_score,
    )
except ImportError as e:
    st.error(f"Could not import toolkit modules: {e}")
    st.error("Make sure the stock_toolkit package is installed or on PYTHONPATH.")
    st.stop()

# ─────────────────────────────────────────────
#  PAGE CONFIG & THEME
# ─────────────────────────────────────────────

from stock_toolkit.ui.theme import setup_page
setup_page("Stock Toolkit")

# ─────────────────────────────────────────────
#  SIDEBAR
# ─────────────────────────────────────────────

DEFAULT_SYMS = ["AAPL", "GOOGL", "MSFT", "CSMIB.MI", "TSLA", "ENEL.MI"]


def _preset_range(preset, lo, hi):
    """Map a preset label to a (from, to) date pair, clamped to [lo, hi]."""
    if preset == "Max":
        return lo, hi
    if preset == "YTD":
        return max(lo, date(hi.year, 1, 1)), hi
    months = {"1M": 1, "3M": 3, "6M": 6, "1Y": 12, "5Y": 60}.get(preset, 12)
    frm = (pd.Timestamp(hi) - pd.DateOffset(months=months)).date()
    return max(lo, frm), hi


def _sync_symbol(sym: str) -> None:
    """on_change: propagate one checkbox click into the owning set."""
    if st.session_state.get(f"symcb_{sym}"):
        st.session_state["sym_selection"].add(sym)
    else:
        st.session_state["sym_selection"].discard(sym)


def _symbol_picker(all_symbols):
    """Scrollable checkbox list with a filter + Select-all/Clear.

    OWNERSHIP: ``st.session_state["sym_selection"]`` (a plain set) is the
    single source of truth. It is a non-widget key, so it survives page
    switches (Streamlit garbage-collects *widget* state whenever a run
    doesn't render the widget — which is what used to reset the selection
    after visiting Admin/Game/Help). The checkboxes are pure views:
    they render FROM the set via ``value=`` and write INTO it via their
    ``on_change`` callback; the buttons mutate the set directly. Nothing
    downstream ever reads the ``symcb_*`` widget keys.
    """
    if "sym_selection" not in st.session_state:
        defaults = [s for s in DEFAULT_SYMS if s in all_symbols] or all_symbols[:6]
        st.session_state["sym_selection"] = set(defaults)
    # symbols can disappear from the DB between sessions
    st.session_state["sym_selection"] &= set(all_symbols)
    sel = st.session_state["sym_selection"]

    flt = st.text_input("Filter symbols", key="sym_filter",
                        placeholder="🔍  Filter…", label_visibility="collapsed")
    shown = ([s for s in all_symbols if flt.lower() in s.lower()]
             if flt else all_symbols)

    c1, c2 = st.columns(2)
    if c1.button("Select all", use_container_width=True):
        sel |= set(shown)
    if c2.button("Clear", use_container_width=True):
        sel -= set(shown)

    with st.container(height=280):
        if not shown:
            st.caption("No symbols match the filter.")
        for s in shown:
            # Render model → view, unconditionally, every run. (A value=
            # default would NOT do this: for keyed widgets the stored
            # widget state wins and the default is ignored, which is how
            # the views went stale against the set.) Clicks flow back
            # view → model through on_change before the rerun.
            st.session_state[f"symcb_{s}"] = s in sel
            st.checkbox(s, key=f"symcb_{s}",
                        on_change=_sync_symbol, args=(s,))

    selected = [s for s in all_symbols if s in sel]
    st.caption(f"**{len(selected)}** selected")
    return selected


def _date_range():
    """Quick-range presets + a data-bounded calendar (Custom mode)."""
    lo, hi = get_data_date_range()
    lo = lo or date(2015, 1, 1)
    hi = hi or pd.Timestamp("today").date()

    # Default to a wide window: the scoring horizons need years of bars
    # (quarter→60 weekly, year→100 weekly, life→120 monthly), so a short
    # default silently penalises every score. 5Y (clamped to available data)
    # restores the old ~3-year default's behaviour.
    # Like the symbol picker, the widget keys are wiped when another page
    # renders — the *_persist mirrors keep the choice across page switches.
    preset = st.segmented_control(
        "Range", ["1M", "3M", "6M", "YTD", "1Y", "5Y", "Max", "Custom"],
        default=st.session_state.get("date_preset_persist", "5Y"),
        key="date_preset",
    )
    preset = preset or st.session_state.get("date_preset_persist", "5Y")
    st.session_state["date_preset_persist"] = preset
    if preset == "Custom":
        default_from, _ = _preset_range("1Y", lo, hi)
        rng = st.date_input(
            "Dates",
            value=st.session_state.get("date_range_persist",
                                       (default_from, hi)),
            min_value=lo, max_value=hi, key="date_range")
        if isinstance(rng, (tuple, list)) and len(rng) == 2:
            st.session_state["date_range_persist"] = (rng[0], rng[1])
            return rng[0], rng[1]
        return default_from, hi
    frm, to = _preset_range(preset, lo, hi)
    st.caption(f"{frm}  →  {to}")
    return frm, to


with st.sidebar:
    from stock_toolkit.ui.icons import icon as _ic
    st.markdown(f"### {_ic('app.icon')} Stock Toolkit")
    st.markdown("---")

    all_symbols = get_all_symbols()
    if not all_symbols:
        st.warning("No data found. Run `stock_collector.py` first.")
        selected_symbols = []
    else:
        selected_symbols = _symbol_picker(all_symbols)

    st.markdown("---")
    date_from, date_to = _date_range()
    date_from_str = str(date_from)
    date_to_str   = str(date_to)

    st.markdown("---")
    st.markdown(
        "<span style='font-size:0.72rem;color:#8ba0b4'>"
        "Data from stock_data.db<br>"
        "Run `stock_collector.py` to refresh"
        "</span>",
        unsafe_allow_html=True
    )

if not selected_symbols:
    st.info("Select at least one symbol in the sidebar to get started.")
    st.stop()

# ─────────────────────────────────────────────
#  TABS
# ─────────────────────────────────────────────

from stock_toolkit.ui.icons import tab_label as _tab

tab_score, tab_analysis, tab_backtest, tab_alerts, tab_brief, tab_collect = st.tabs([
    _tab("tab.score",    "Score"),
    _tab("tab.analysis", "Analysis"),
    _tab("tab.backtest", "Backtest"),
    _tab("tab.alerts",   "Alerts"),
    _tab("tab.briefing", "Briefing"),
    _tab("tab.collect",  "Collect"),
])

with tab_score:
    tabs_score.render(selected_symbols, date_from_str, date_to_str)
with tab_analysis:
    tabs_analysis.render(selected_symbols, date_from_str, date_to_str)
with tab_backtest:
    tabs_backtest.render(selected_symbols, date_from_str, date_to_str)
with tab_alerts:
    tabs_alerts.render(selected_symbols, date_from_str, date_to_str)
with tab_brief:
    tabs_briefing.render(selected_symbols, date_from_str, date_to_str)
with tab_collect:
    tabs_collect.render(selected_symbols, date_from_str, date_to_str)
