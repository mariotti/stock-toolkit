"""Single source of truth for page config + global CSS.

Streamlit isolates `st.markdown` / `st.set_page_config` per page — each
file under `pages/` renders in its own runtime, so any CSS injected in
`app.py` does not reach Admin / Game / Help. Without this module the
main dashboard looked dark while the sidebar pages reverted to
Streamlit's default light theme.

Every page (the main app + every file in `pages/`) calls
``setup_page(...)`` first thing in render so the brand, favicon, and
custom CSS are applied uniformly.
"""

import streamlit as st

from stock_toolkit.ui.icons import icon


# All custom CSS in one block so every page applies identical styling.
# The selectors override Streamlit's light-theme defaults so the
# dashboard looks the same in browsers that auto-switch on OS theme.
_THEME_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap');

html, body, [class*="css"] {
    font-family: 'IBM Plex Sans', sans-serif;
}

/* App background — single source of truth across main + pages. */
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
.main {
    background: #0e1922;
    color: #c8d8e8;
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

/* Sidebar — same dark surface across main + every page. */
[data-testid="stSidebar"] {
    background: #0e1922;
    border-right: 1px solid #1e2f40;
}
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span,
[data-testid="stSidebar"] li {
    color: #8ba0b4 !important;
    font-size: 0.82rem !important;
}
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3,
[data-testid="stSidebar"] h4 {
    color: #c8d8e8 !important;
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
"""


def setup_page(title: str = "Stock Toolkit") -> None:
    """Apply page config + global CSS. Call as the first line of every
    page's render() (the main app and every file in pages/)."""
    st.set_page_config(
        page_title=title,
        page_icon=icon("app.icon"),
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(_THEME_CSS, unsafe_allow_html=True)
