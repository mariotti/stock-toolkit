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
    from stock_toolkit.ui.helpers import get_all_symbols
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

with st.sidebar:
    from stock_toolkit.ui.icons import icon as _ic
    st.markdown(f"### {_ic('app.icon')} Stock Toolkit")
    st.markdown("---")

    all_symbols = get_all_symbols()
    if not all_symbols:
        st.warning("No data found. Run `stock_collector.py` first.")
        selected_symbols = []
    else:
        default = [s for s in ["AAPL","GOOGL","MSFT","CSMIB.MI","TSLA","ENEL.MI"]
                   if s in all_symbols] or all_symbols[:6]
        selected_symbols = st.multiselect(
            "Symbols", all_symbols, default=default,
            help="Select symbols to analyse across all tabs"
        )

    st.markdown("---")
    date_from = st.date_input("From", value=pd.Timestamp("2023-01-01"),
                               help="Start of the analysis period")
    date_to   = st.date_input("To",   value=pd.Timestamp("today"),
                               help="End of the analysis period")
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
