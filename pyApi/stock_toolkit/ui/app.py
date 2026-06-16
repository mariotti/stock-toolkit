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

st.set_page_config(
    page_title="Stock Toolkit",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS — dark financial aesthetic, monospace numbers, tight spacing
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap');

html, body, [class*="css"] {
    font-family: 'IBM Plex Sans', sans-serif;
}

/* Metric numbers */
[data-testid="stMetricValue"] {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1.4rem !important;
    font-weight: 500;
}

/* Dataframe / table */
[data-testid="stDataFrame"] {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.82rem;
}

/* Score bar cells */
.score-bar {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.78rem;
    color: #888;
}

/* Header strip */
.header-strip {
    background: linear-gradient(90deg, #0f1923 0%, #1a2535 100%);
    border-bottom: 1px solid #2d3f55;
    padding: 0.6rem 1rem;
    margin: -1rem -1rem 1.5rem -1rem;
    display: flex;
    align-items: center;
    gap: 0.6rem;
}
.header-title {
    font-family: 'IBM Plex Mono', monospace;
    font-weight: 500;
    font-size: 1.05rem;
    color: #c8d8e8;
    letter-spacing: 0.04em;
}

/* Sidebar */
[data-testid="stSidebar"] {
    background: #0e1922;
    border-right: 1px solid #1e2f40;
}
[data-testid="stSidebar"] label {
    color: #8ba0b4 !important;
    font-size: 0.82rem !important;
}

/* Page navigation (auto-generated when there's a pages/ dir): the
   "Stock Toolkit" page name and the "Admin" entry below it. Streamlit's
   defaults are too dim against our dark sidebar — force the bright
   header color so the nav is legible. Covers both legacy and current
   testid selectors. */
[data-testid="stSidebarNav"] a,
[data-testid="stSidebarNav"] span,
[data-testid="stSidebarNavItems"] a,
[data-testid="stSidebarNavItems"] span {
    color: #c8d8e8 !important;
}
[data-testid="stSidebarNav"] a:hover,
[data-testid="stSidebarNavItems"] a:hover {
    color: #ffffff !important;
}

/* Tab labels */
button[data-baseweb="tab"] {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.82rem;
    letter-spacing: 0.03em;
}

/* Code blocks */
code {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.8rem;
    background: #1a2535;
    padding: 0.1em 0.3em;
    border-radius: 3px;
}

/* Alert pill */
.pill-green { color: #4ade80; font-weight: 600; }
.pill-red   { color: #f87171; font-weight: 600; }
.pill-amber { color: #fbbf24; font-weight: 600; }
.pill-gray  { color: #6b7280; }

/* Divider */
hr { border-color: #1e2f40 !important; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
#  SIDEBAR
# ─────────────────────────────────────────────

with st.sidebar:
    st.markdown("### 📈 Stock Toolkit")
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
