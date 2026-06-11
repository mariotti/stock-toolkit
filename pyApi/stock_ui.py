"""
stock_ui.py
===========
Streamlit dashboard for the Stock Toolkit.
Wraps stock_score.py, stock_analysis.py, stock_backtest.py, and stock_alerts.py
into a unified browser UI. All analytical logic stays in the original scripts.

Run:
    pip install streamlit plotly
    streamlit run stock_ui.py

The app opens at http://localhost:8501
"""

import sys
from pathlib import Path
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import streamlit as st

# ── resolve script directory so imports work when launched from ~/bin ─────────
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from stock_common import load_config

_cfg = load_config(SCRIPT_DIR / "config.env")

# ── lazy imports with friendly error messages ─────────────────────────────────
try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except ImportError:
    st.error("plotly not installed — run: pip install plotly")
    st.stop()

try:
    import stock_score  as ss
    import stock_analysis as sa  # noqa: F401 — imported to fail fast if missing
    import stock_backtest as sb
    import stock_alerts  as sal
except ImportError as e:
    st.error(f"Could not import toolkit scripts: {e}")
    st.error("Make sure stock_score.py, stock_analysis.py, stock_backtest.py, "
             "and stock_alerts.py are in the same folder as stock_ui.py.")
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
#  SHARED STATE & HELPERS
# ─────────────────────────────────────────────

@st.cache_data(ttl=300)
def get_all_symbols():
    try:
        return ss.list_all_symbols()
    except Exception:
        return []


@st.cache_data(ttl=300)
def get_prices(symbol, date_from, date_to):
    try:
        return ss.load_prices(symbol, date_from or None, date_to or None)
    except Exception:
        return pd.DataFrame()


def score_color(score):
    if score >= 60:   return "#4ade80"
    if score >= 40:   return "#fbbf24"
    return "#f87171"


def fmt_pct(v, decimals=1):
    if v is None: return "—"
    return f"{v:+.{decimals}f}%"


def fmt_val(v, decimals=2):
    if v is None: return "—"
    return f"{v:.{decimals}f}"


# ─────────────────────────────────────────────
#  PLOTLY CHART HELPERS
# ─────────────────────────────────────────────

CHART_LAYOUT = dict(
    template="plotly_dark",
    paper_bgcolor="#0e1922",
    plot_bgcolor="#0e1922",
    font=dict(family="IBM Plex Mono", size=11, color="#8ba0b4"),
    margin=dict(l=48, r=16, t=36, b=36),
    legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
    xaxis=dict(gridcolor="#1e2f40", linecolor="#1e2f40", zeroline=False),
    yaxis=dict(gridcolor="#1e2f40", linecolor="#1e2f40", zeroline=False),
)


def price_chart(df: pd.DataFrame, title: str = "") -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["timestamp"], y=df["close"],
        mode="lines", name="close",
        line=dict(color="#38bdf8", width=1.5),
        hovertemplate="%{x|%Y-%m-%d}<br>%{y:.2f}<extra></extra>",
    ))
    fig.update_layout(**CHART_LAYOUT, title=dict(text=title, font=dict(size=12)))
    return fig


def score_bar_chart(results: list[dict]) -> go.Figure:
    syms   = [r["symbol"] for r in results]
    scores = [r["score"]  for r in results]
    colors = [score_color(s) for s in scores]

    fig = go.Figure(go.Bar(
        x=scores, y=syms,
        orientation="h",
        marker_color=colors,
        text=[f"{s:.1f}" for s in scores],
        textposition="outside",
        hovertemplate="%{y}: %{x:.1f}/100<extra></extra>",
    ))
    # Build layout without xaxis/yaxis keys (already in CHART_LAYOUT)
    layout = {k: v for k, v in CHART_LAYOUT.items()
              if k not in ("xaxis", "yaxis")}
    fig.update_layout(**layout,
                      height=max(260, len(syms) * 38),
                      showlegend=False)
    fig.update_xaxes(range=[0, 105], gridcolor="#1e2f40")
    fig.update_yaxes(autorange="reversed", gridcolor="#1e2f40")
    return fig


def equity_chart(dates, equity, bh_equity) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=pd.to_datetime(dates), y=equity,
        mode="lines", name="Strategy",
        line=dict(color="#38bdf8", width=2),
    ))
    fig.add_trace(go.Scatter(
        x=pd.to_datetime(dates), y=bh_equity,
        mode="lines", name="Buy & hold",
        line=dict(color="#6b7280", width=1.2, dash="dot"),
    ))
    fig.update_layout(**CHART_LAYOUT, title="Equity curve")
    return fig


def drawdown_chart(df: pd.DataFrame) -> go.Figure:
    s    = df["close"].dropna().values
    hwm  = np.maximum.accumulate(s)
    dd   = (s - hwm) / hwm * 100
    fig  = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["timestamp"], y=dd,
        fill="tozeroy",
        mode="lines",
        line=dict(color="#f87171", width=1),
        fillcolor="rgba(248,113,113,0.15)",
        name="Drawdown",
    ))
    fig.update_layout(**CHART_LAYOUT, title="Drawdown (%)", yaxis_tickformat=".1f")
    return fig


def rsi_chart(df: pd.DataFrame, window: int = 14) -> go.Figure:
    close = df["close"].dropna()
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1/window, adjust=False).mean()
    loss  = (-delta).clip(lower=0).ewm(alpha=1/window, adjust=False).mean()
    rsi   = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))

    fig = make_subplots(rows=2, cols=1, row_heights=[0.65, 0.35], shared_xaxes=True,
                        vertical_spacing=0.04)
    fig.add_trace(go.Scatter(x=df["timestamp"], y=df["close"],
                             mode="lines", name="Price",
                             line=dict(color="#38bdf8", width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df["timestamp"], y=rsi,
                             mode="lines", name="RSI",
                             line=dict(color="#a78bfa", width=1.5)), row=2, col=1)
    fig.add_hline(y=70, line_dash="dot", line_color="#f87171", row=2, col=1)
    fig.add_hline(y=30, line_dash="dot", line_color="#4ade80", row=2, col=1)

    lo = CHART_LAYOUT.copy()
    lo.update(height=420)
    fig.update_layout(**lo)
    fig.update_xaxes(gridcolor="#1e2f40", linecolor="#1e2f40")
    fig.update_yaxes(gridcolor="#1e2f40", linecolor="#1e2f40")
    return fig


def bbands_chart(df: pd.DataFrame, window: int = 20) -> go.Figure:
    close = df["close"]
    mid   = close.rolling(window).mean()
    std   = close.rolling(window).std()
    upper = mid + 2 * std
    lower = mid - 2 * std

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["timestamp"], y=upper,
                             line=dict(color="#6b7280", width=0.8, dash="dot"),
                             name="Upper", showlegend=False))
    fig.add_trace(go.Scatter(x=df["timestamp"], y=lower,
                             fill="tonexty", fillcolor="rgba(56,189,248,0.06)",
                             line=dict(color="#6b7280", width=0.8, dash="dot"),
                             name="Lower", showlegend=False))
    fig.add_trace(go.Scatter(x=df["timestamp"], y=mid,
                             line=dict(color="#6b7280", width=1),
                             name="SMA", showlegend=False))
    fig.add_trace(go.Scatter(x=df["timestamp"], y=close,
                             line=dict(color="#38bdf8", width=1.5),
                             name="Price"))
    fig.update_layout(**CHART_LAYOUT, title=f"Bollinger Bands ({window})")
    return fig


def mc_chart(df: pd.DataFrame, n_paths: int = 500, horizon: int = 63) -> go.Figure:
    price = df["close"].dropna().values.astype(float)
    rets  = np.diff(np.log(price))
    mu, sigma = rets.mean(), rets.std()
    s0    = price[-1]
    rng   = np.random.default_rng(42)
    eps   = rng.standard_normal((horizon, n_paths))
    paths = s0 * np.cumprod(np.exp((mu - 0.5 * sigma**2) + sigma * eps), axis=0)

    # sample 80 paths to display
    n_show = min(80, n_paths)
    fig = go.Figure()
    for i in range(n_show):
        fig.add_trace(go.Scatter(
            y=np.concatenate([[s0], paths[:, i]]),
            mode="lines",
            line=dict(color="rgba(56,189,248,0.05)", width=0.8),
            showlegend=False,
            hoverinfo="skip",
        ))

    final   = paths[-1]
    pcts    = np.percentile(final, [5, 50, 95])
    p5_path = np.percentile(paths, 5, axis=1)
    p50_path= np.percentile(paths, 50, axis=1)
    p95_path= np.percentile(paths, 95, axis=1)
    x       = list(range(horizon + 1))

    for y_arr, name, color in [
        (np.concatenate([[s0], p95_path]), "P95", "#4ade80"),
        (np.concatenate([[s0], p50_path]), "P50", "#fbbf24"),
        (np.concatenate([[s0], p5_path]),  "P5",  "#f87171"),
    ]:
        fig.add_trace(go.Scatter(
            x=x, y=y_arr, mode="lines", name=name,
            line=dict(color=color, width=2),
        ))

    prob = (final > s0).mean() * 100
    fig.update_layout(
        **CHART_LAYOUT,
        title=f"Monte Carlo  |  {n_paths} paths × {horizon} bars  |  "
              f"P(gain)={prob:.0f}%  P50={pcts[1]:.2f}  P5={pcts[0]:.2f}",
        xaxis_title="Bars forward",
        yaxis_title="Price",
    )
    return fig



def price_compare_chart(dfs: dict[str, pd.DataFrame]) -> go.Figure:
    """Normalised price comparison — all series start at 100."""
    COLORS = ["#38bdf8","#4ade80","#fbbf24","#f87171","#a78bfa","#fb923c","#34d399","#e879f9"]
    fig = go.Figure()
    for i, (sym, df) in enumerate(dfs.items()):
        s = df["close"].dropna()
        if s.empty:
            continue
        norm = s / s.iloc[0] * 100
        fig.add_trace(go.Scatter(
            x=df["timestamp"], y=norm,
            mode="lines", name=sym,
            line=dict(color=COLORS[i % len(COLORS)], width=1.8),
            hovertemplate=f"{sym}<br>%{{x|%Y-%m-%d}}<br>%{{y:.1f}}<extra></extra>",
        ))
    fig.update_layout(**CHART_LAYOUT, title="Price — normalised to 100",
                      yaxis_title="Indexed price (start = 100)")
    return fig


def drawdown_compare_chart(dfs: dict[str, pd.DataFrame]) -> go.Figure:
    """Drawdown overlay for multiple symbols."""
    COLORS = ["#38bdf8","#4ade80","#fbbf24","#f87171","#a78bfa","#fb923c","#34d399","#e879f9"]
    fig = go.Figure()
    for i, (sym, df) in enumerate(dfs.items()):
        s   = df["close"].dropna().values
        hwm = np.maximum.accumulate(s)
        dd  = (s - hwm) / hwm * 100
        fig.add_trace(go.Scatter(
            x=df["timestamp"], y=dd,
            mode="lines", name=sym,
            line=dict(color=COLORS[i % len(COLORS)], width=1.5),
            hovertemplate=f"{sym}  %{{y:.1f}}%<extra></extra>",
        ))
    fig.update_layout(**CHART_LAYOUT, title="Drawdown comparison (%)",
                      yaxis_tickformat=".1f")
    return fig


def correlation_heatmap(dfs: dict[str, pd.DataFrame]) -> go.Figure:
    """Pearson correlation matrix of weekly returns."""
    series = {}
    for sym, df in dfs.items():
        w = df.set_index("timestamp")["close"].resample("W-FRI").last().dropna()
        if len(w) > 5:
            series[sym] = w.pct_change().dropna()

    if len(series) < 2:
        return go.Figure()

    aligned = pd.DataFrame(series).dropna()
    corr    = aligned.corr()
    syms    = list(corr.columns)
    z       = corr.values

    fig = go.Figure(go.Heatmap(
        z=z, x=syms, y=syms,
        colorscale=[[0,"#f87171"],[0.5,"#1e2f40"],[1,"#4ade80"]],
        zmin=-1, zmax=1,
        text=[[f"{v:.2f}" for v in row] for row in z],
        texttemplate="%{text}",
        hovertemplate="%{y} / %{x}: %{z:.3f}<extra></extra>",
        showscale=True,
    ))
    lo = {k: v for k, v in CHART_LAYOUT.items() if k not in ("xaxis","yaxis")}
    fig.update_layout(**lo, title="Return correlation (weekly)")
    return fig


def summary_table(dfs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Summary stats table for multiple symbols."""
    rows = []
    for sym, df in dfs.items():
        r = ss.step_summary(df, ann_factor=52)
        g = ss.step_regression(df)
        d = ss.step_drawdown(df)
        e = ss.step_entry_timing(df)
        if not r:
            continue
        rows.append({
            "Symbol":     sym,
            "Last":       fmt_val(r.get("last")),
            "Total ret":  fmt_pct(r.get("total_ret")),
            "Sharpe":     fmt_val(r.get("sharpe")),
            "Vol":        f"{r['ann_vol']:.1f}%" if r.get("ann_vol") else "—",
            "Max DD":     f"{d['max_dd']:.1f}%"  if d.get("max_dd") else "—",
            "Calmar":     fmt_val(d.get("calmar")),
            "R²":         fmt_val(g.get("r2"), 3),
            "Trend/yr":   fmt_pct(g.get("ann_trend")),
            "RSI":        f"{e['rsi14']:.0f}"    if e.get("rsi14") else "—",
            "%B":         f"{e['pct_b']:.2f}"    if e.get("pct_b") is not None else "—",
        })
    return pd.DataFrame(rows)


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
        "<span style='font-size:0.72rem;color:#4a6075'>"
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

tab_score, tab_analysis, tab_backtest, tab_alerts, tab_brief, tab_collect = st.tabs([
    "🏆  Score", "📊  Analysis", "🔁  Backtest", "🔔  Alerts", "🤖  Briefing", "📥  Collect"
])

# ═════════════════════════════════════════════
#  TAB 1 — SCORE
# ═════════════════════════════════════════════

with tab_score:
    c1, c2, c3 = st.columns([2, 2, 3])
    with c1:
        horizon = st.selectbox(
            "Horizon", list(ss.HORIZON_PROFILES.keys()),
            index=2,  # quarter
            format_func=lambda h: {
                "week":    "Week  — next 5 days",
                "month":   "Month — next 21 days",
                "quarter": "Quarter — next 63 days",
                "year":    "Year  — next 252 days",
                "life":    "Life  — buy & hold 5yr+",
            }[h]
        )
    with c2:
        mc_paths = st.select_slider("Monte Carlo paths",
                                     options=[200, 500, 1000, 2000], value=500)
    with c3:
        profile = ss.HORIZON_PROFILES[horizon]
        w       = profile["weights"]
        st.markdown(
            f"**Weights:**  "
            f"entry `{w['rsi_entry']+w['pct_b_entry']}pts`  "
            f"trend `{w['r2']+w['ann_trend']}pts`  "
            f"risk `{w['sharpe']+w['calmar']}pts`  "
            f"MC `{w['prob_gain']}pts`",
            help="How points are distributed for this horizon"
        )

    if st.button("▶  Run scoring", type="primary"):
        results = []
        progress = st.progress(0, text="Scoring symbols...")
        for i, sym in enumerate(selected_symbols):
            progress.progress((i + 1) / len(selected_symbols), text=f"Scoring {sym}…")
            df = get_prices(sym, date_from_str, date_to_str)
            if df.empty or len(df) < 10:
                continue

            gran = profile["gran"]
            try:
                df_r = df.set_index("timestamp").resample(gran).agg(
                    {"open":"first","high":"max","low":"min","close":"last","volume":"sum"}
                ).dropna(subset=["close"]).reset_index()
            except Exception:
                fb = {"ME":"M","QE":"Q"}.get(gran, gran)
                df_r = df.set_index("timestamp").resample(fb).agg(
                    {"open":"first","high":"max","low":"min","close":"last","volume":"sum"}
                ).dropna(subset=["close"]).reset_index()

            if len(df_r) < 10:
                continue

            raw = {
                "symbol":     sym,
                "horizon":    horizon,
                "summary":    ss.step_summary(df_r, ann_factor=profile["ann_factor"]),
                "regression": ss.step_regression(df_r),
                "drawdown":   ss.step_drawdown(df_r),
                "entry":      ss.step_entry_timing(df_r),
                "montecarlo": ss.step_montecarlo(df_r, mc_paths, profile["mc_bars"]),
            }
            score, notes = ss.score_symbol(raw, weights=w,
                                            min_bars=profile["min_bars"])
            raw["score"] = score
            raw["notes"] = notes
            results.append(raw)

        progress.empty()
        results.sort(key=lambda x: x["score"], reverse=True)
        st.session_state["score_results"] = results

    results = st.session_state.get("score_results", [])
    if not results:
        st.markdown(
            "<div style='padding:2rem;text-align:center;color:#4a6075'>"
            "Select symbols and horizon, then click <b>Run scoring</b>."
            "</div>", unsafe_allow_html=True
        )
    else:
        # ── bar chart ──────────────────────────────────────────────────────────
        st.plotly_chart(score_bar_chart(results),
                        width='stretch', config={"displayModeBar": False})

        # ── summary table ──────────────────────────────────────────────────────
        rows = []
        for r in results:
            s  = r.get("summary",    {})
            dd = r.get("drawdown",   {})
            en = r.get("entry",      {})
            mc = r.get("montecarlo", {})
            rows.append({
                "Symbol":   r["symbol"],
                "Score":    f"{r['score']:.1f}",
                "Sharpe":   fmt_val(s.get("sharpe")),
                "Calmar":   fmt_val(dd.get("calmar")),
                "Vol":      f"{s['ann_vol']:.1f}%"  if s.get("ann_vol")  else "—",
                "Max DD":   f"{dd['max_dd']:.1f}%"  if dd.get("max_dd")  else "—",
                "RSI":      f"{en['rsi14']:.0f}"     if en.get("rsi14")   else "—",
                "%B":       f"{en['pct_b']:.2f}"     if en.get("pct_b")   is not None else "—",
                "P(gain)":  f"{mc['prob_gain']:.0f}%" if mc.get("prob_gain") else "—",
                "P50":      fmt_val(mc.get("p50")),
                "P5":       fmt_val(mc.get("p5")),
            })
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)

        # ── detail expander for top pick ───────────────────────────────────────
        top = results[0]
        with st.expander(f"📋  {top['symbol']}  scoring breakdown  ({top['score']:.1f}/100)"):
            for note in top.get("notes", []):
                icon = "✅" if "PENALTY" not in note else "❌"
                st.markdown(f"{icon} `{note}`")

        # ── best pair hint ─────────────────────────────────────────────────────
        top_eu     = "." in top["symbol"]
        candidates = [r for r in results[1:]
                      if ("." in r["symbol"]) != top_eu]
        if candidates:
            pair = candidates[0]
            st.info(
                f"**Suggested pair:** {top['symbol']} ({top['score']:.1f})  +  "
                f"{pair['symbol']} ({pair['score']:.1f})  —  "
                f"different exchange families → likely low correlation"
            )


# ═════════════════════════════════════════════
#  TAB 2 — ANALYSIS
# ═════════════════════════════════════════════

with tab_analysis:
    # ── tool selector + parameters (top bar) ──────────────────────────────────
    MULTI_TOOLS  = {"Summary", "Price (compare)", "Drawdown (compare)", "Correlation"}
    SINGLE_TOOLS = {"RSI", "Bollinger Bands", "Monte Carlo"}
    ALL_TOOLS    = ["Summary", "Price (compare)", "Drawdown (compare)",
                    "Correlation", "RSI", "Bollinger Bands", "Monte Carlo"]

    tp1, tp2, tp3 = st.columns([3, 3, 3])
    with tp1:
        analysis_tool = st.radio(
            "Tool", ALL_TOOLS, horizontal=False,
            key="an_tool",
            help="Multi-symbol: Summary, Price, Drawdown, Correlation use all sidebar symbols. "
                 "Single-symbol: RSI, BBands, Monte Carlo show one symbol at a time."
        )

    with tp2:
        # Parameters vary by tool
        rsi_w = bb_w = mc_horizon = mc_n = None
        if analysis_tool == "RSI":
            rsi_w = st.slider("RSI window", 7, 30, 14, key="an_rsi_w")
        elif analysis_tool == "Bollinger Bands":
            bb_w = st.slider("BB window", 10, 50, 20, key="an_bb_w")
        elif analysis_tool == "Monte Carlo":
            mc_horizon = st.select_slider(
                "Horizon (bars)", [5, 21, 63, 126, 252], value=63, key="an_mc_h")
            mc_n = st.select_slider(
                "Paths", [200, 500, 1000, 2000], value=500, key="an_mc_n")

    with tp3:
        # Single-symbol tools need a symbol selector here, clearly labelled
        if analysis_tool in SINGLE_TOOLS:
            analysis_sym = st.selectbox(
                "Viewing symbol",
                selected_symbols, key="an_sym",
                help="Chart is shown for this symbol. "
                     "Use the sidebar to manage your overall watchlist."
            )
        else:
            st.markdown(
                "<span style='color:#4a6075;font-size:0.82rem'>"
                f"Using all {len(selected_symbols)} sidebar symbol(s)"
                "</span>",
                unsafe_allow_html=True
            )
            analysis_sym = selected_symbols[0] if selected_symbols else None

    st.markdown("---")

    # ── load data ─────────────────────────────────────────────────────────────
    if analysis_tool in MULTI_TOOLS:
        dfs = {sym: get_prices(sym, date_from_str, date_to_str)
               for sym in selected_symbols}
        dfs = {sym: df for sym, df in dfs.items() if not df.empty}
    else:
        single_df = get_prices(analysis_sym, date_from_str, date_to_str)                     if analysis_sym else pd.DataFrame()

    # ── render ────────────────────────────────────────────────────────────────
    if analysis_tool == "Summary":
        if not dfs:
            st.warning("No data found for selected symbols.")
        else:
            tbl = summary_table(dfs)
            st.dataframe(tbl, width='stretch', hide_index=True)
            st.plotly_chart(price_compare_chart(dfs), width='stretch')

    elif analysis_tool == "Price (compare)":
        if not dfs:
            st.warning("No data found for selected symbols.")
        else:
            st.plotly_chart(price_compare_chart(dfs), width='stretch')
            # per-symbol return metrics below the chart
            cols = st.columns(min(len(dfs), 4))
            for i, (sym, df) in enumerate(dfs.items()):
                ret = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
                cols[i % 4].metric(sym, f"{df['close'].iloc[-1]:.2f}",
                                   delta=f"{ret:+.1f}%")

    elif analysis_tool == "Drawdown (compare)":
        if not dfs:
            st.warning("No data found for selected symbols.")
        else:
            st.plotly_chart(drawdown_compare_chart(dfs), width='stretch')
            # drawdown stats table
            rows = []
            for sym, df in dfs.items():
                d = ss.step_drawdown(df)
                if d:
                    rows.append({
                        "Symbol":    sym,
                        "Max DD":    f"{d['max_dd']:.1f}%",
                        "Calmar":    fmt_val(d.get("calmar")),
                        "Recovered": "✅" if d["recovered"] else "❌",
                        "Ann. ret":  f"{d['ann_ret']:.1f}%",
                    })
            if rows:
                st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)

    elif analysis_tool == "Correlation":
        if len(dfs) < 2:
            st.info("Select at least 2 symbols in the sidebar to show correlation.")
        else:
            st.plotly_chart(correlation_heatmap(dfs), width='stretch')
            st.caption(
                "Pearson correlation of weekly returns.  "
                "Values near 0 = genuine diversification.  "
                "Values near 1 = same bet."
            )

    elif analysis_tool == "RSI":
        if single_df.empty:
            st.warning(f"No data for {analysis_sym}.")
        else:
            st.plotly_chart(rsi_chart(single_df, rsi_w), width='stretch')
            rsi_val = ss._rsi(single_df["close"].dropna(), rsi_w)
            if not np.isnan(rsi_val):
                signal = ("🔴 Overbought" if rsi_val > 70 else
                          "🟢 Oversold"   if rsi_val < 30 else
                          "⚪ Neutral")
                st.markdown(f"**RSI({rsi_w}) = {rsi_val:.1f}  →  {signal}**")

    elif analysis_tool == "Bollinger Bands":
        if single_df.empty:
            st.warning(f"No data for {analysis_sym}.")
        else:
            st.plotly_chart(bbands_chart(single_df, bb_w), width='stretch')
            pb = ss._pct_b(single_df["close"].dropna(), bb_w)
            sq = ss._bbands_squeeze(single_df["close"].dropna(), bb_w)
            if not np.isnan(pb):
                st.markdown(
                    f"**%B = {pb:.2f}**  "
                    + ("  ⚡ Squeeze active — potential breakout" if sq else "")
                )

    elif analysis_tool == "Monte Carlo":
        if single_df.empty:
            st.warning(f"No data for {analysis_sym}.")
        else:
            st.plotly_chart(mc_chart(single_df, mc_n, mc_horizon), width='stretch')


# ═════════════════════════════════════════════
#  TAB 3 — BACKTEST
# ═════════════════════════════════════════════

with tab_backtest:
    b1, b2, b3 = st.columns(3)

    with b1:
        bt_sym = st.selectbox("Symbol", selected_symbols, key="bt_sym")
        strategy = st.selectbox(
            "Strategy",
            ["rsi", "sma_cross", "bbands", "breakout"],
            format_func=lambda s: {
                "rsi":       "RSI reversal",
                "sma_cross": "SMA crossover",
                "bbands":    "Bollinger Bands",
                "breakout":  "N-bar breakout",
            }[s]
        )

    with b2:
        if strategy == "rsi":
            bt_window = st.slider("RSI window", 7, 30, 14, key="bt_w")
            bt_buy    = st.slider("Buy when RSI <", 10, 45, 30, key="bt_buy")
            bt_sell   = st.slider("Sell when RSI >", 55, 90, 70, key="bt_sell")
        elif strategy == "sma_cross":
            bt_fast = st.slider("Fast SMA", 5, 50, 20, key="bt_fast")
            bt_slow = st.slider("Slow SMA", 20, 200, 50, key="bt_slow")
        elif strategy in ("bbands", "breakout"):
            bt_window = st.slider("Window", 5, 50, 20, key="bt_w2")

    with b3:
        capital    = st.number_input("Capital ($)", 1000, 100000, 10000, step=1000)
        commission = st.select_slider("Commission", [0.0005, 0.001, 0.002, 0.005],
                                       value=0.001,
                                       format_func=lambda x: f"{x*100:.2f}%")
        slippage   = st.select_slider("Slippage",   [0.0005, 0.001, 0.002],
                                       value=0.001,
                                       format_func=lambda x: f"{x*100:.2f}%")

    if st.button("▶  Run backtest", type="primary"):
        df = get_prices(bt_sym, date_from_str, date_to_str)
        if df.empty:
            st.warning(f"No data for {bt_sym}.")
        else:
            bt = sb.Backtester(capital=capital, commission=commission,
                                slippage=slippage)

            if strategy == "rsi":
                sigs  = sb.signals_rsi(df, bt_window, bt_buy, bt_sell)
                label = f"RSI({bt_window}) buy<{bt_buy} sell>{bt_sell}"
            elif strategy == "sma_cross":
                sigs  = sb.signals_sma_cross(df, bt_fast, bt_slow)
                label = f"SMA cross {bt_fast}/{bt_slow}"
            elif strategy == "bbands":
                sigs  = sb.signals_bbands(df, bt_window)
                label = f"BBands({bt_window})"
            else:
                sigs  = sb.signals_breakout(df, bt_window)
                label = f"Breakout({bt_window})"

            result   = bt.run(df, sigs)
            bh_sigs  = pd.Series(0, index=df.index)
            bh_sigs.iloc[0] = 1
            bh_result = bt.run(df, bh_sigs)

            st.session_state["bt_result"]   = result
            st.session_state["bt_bh"]       = bh_result
            st.session_state["bt_df"]       = df
            st.session_state["bt_label"]    = label
            st.session_state["bt_sym_name"] = bt_sym

    result = st.session_state.get("bt_result")
    if result is None:
        st.markdown(
            "<div style='padding:2rem;text-align:center;color:#4a6075'>"
            "Configure strategy parameters and click <b>Run backtest</b>."
            "</div>", unsafe_allow_html=True
        )
    else:
        bh       = st.session_state["bt_bh"]
        df_bt    = st.session_state["bt_df"]
        label    = st.session_state["bt_label"]
        sym_name = st.session_state["bt_sym_name"]

        m = result["metrics"]
        b = bh["metrics"]

        # ── metrics comparison ─────────────────────────────────────────────────
        st.markdown(f"#### {sym_name}  —  {label}")
        cols = st.columns(6)
        pairs = [
            ("Total return", fmt_pct(m.get("total_return_pct")),
                             fmt_pct(b.get("total_return_pct"))),
            ("CAGR",         fmt_pct(m.get("cagr_pct")),
                             fmt_pct(b.get("cagr_pct"))),
            ("Sharpe",       fmt_val(m.get("sharpe"), 3),
                             fmt_val(b.get("sharpe"), 3)),
            ("Max DD",       fmt_pct(m.get("max_dd_pct"), 1),
                             fmt_pct(b.get("max_dd_pct"), 1)),
            ("Win rate",     f"{m.get('win_rate_pct','—'):.1f}%" if m.get("win_rate_pct") else "—",
                             "—"),
            ("Trades",       str(m.get("n_trades", "—")), "1"),
        ]
        for col, (label_m, strat_v, bh_v) in zip(cols, pairs):
            col.metric(label_m, strat_v, delta=None,
                       help=f"Buy & hold: {bh_v}")

        # ── equity chart ───────────────────────────────────────────────────────
        st.plotly_chart(
            equity_chart(result["dates"], result["equity"], bh["equity"]),
            width='stretch'
        )

        # ── trade log ──────────────────────────────────────────────────────────
        trades = result.get("trades", [])
        if trades:
            with st.expander(f"Trade log  ({len(trades)} trades)"):
                trade_rows = [{
                    "Entry":       t["entry_date"].strftime("%Y-%m-%d"),
                    "Exit":        t["exit_date"].strftime("%Y-%m-%d"),
                    "Buy @":       f"{t['entry_price']:.2f}",
                    "Sell @":      f"{t['exit_price']:.2f}",
                    "P&L %":       f"{t['pnl_pct']:+.2f}%",
                    "P&L $":       f"${t['pnl_abs']:+.2f}",
                } for t in trades]
                st.dataframe(pd.DataFrame(trade_rows),
                             width='stretch', hide_index=True)


# ═════════════════════════════════════════════
#  TAB 4 — ALERTS
# ═════════════════════════════════════════════

with tab_alerts:
    al1, al2 = st.columns([2, 3])

    with al1:
        st.markdown("#### Define conditions")
        conditions_input = st.text_area(
            "Conditions (one per line)",
            value="rsi14 < 30\nbbands_pct_b < 0.1\nchange_pct < -3",
            height=130,
            help="Any indicator expression. One per line."
        )
        conditions = [c.strip() for c in conditions_input.splitlines()
                      if c.strip()]

        dry_run = st.checkbox("Dry run (evaluate only, don't fire or save state)",
                               value=True)

        if st.button("▶  Check alerts", type="primary"):
            alert_results = []
            for sym in selected_symbols:
                df_a = sal.load_series(sym, n_bars=300)
                if df_a.empty:
                    alert_results.append({
                        "symbol": sym, "conditions": [],
                        "error": "No data"
                    })
                    continue
                ctx = sal.build_context(df_a)
                cond_results = []
                for cond in conditions:
                    result_val = sal.evaluate_condition(cond, ctx)
                    cond_results.append({
                        "condition": cond,
                        "result":    result_val,
                    })
                alert_results.append({
                    "symbol":     sym,
                    "conditions": cond_results,
                    "ctx":        ctx,
                    "error":      None,
                })
            st.session_state["alert_results"] = alert_results

        # ── quick indicator reference ──────────────────────────────────────────
        with st.expander("📖  Available indicators"):
            st.markdown("""
| Name | Description |
|---|---|
| `price` | Latest close |
| `rsi7` `rsi9` `rsi14` `rsi21` | RSI at different periods |
| `sma20` `sma50` `sma200` | Simple moving averages |
| `ema20` `ema50` `ema200` | Exponential moving averages |
| `bbands_pct_b` | %B: 0=lower band, 1=upper band |
| `bbands_squeeze` | True when bandwidth is compressed |
| `macd` `macd_signal` `macd_hist` | MACD line, signal, histogram |
| `change_pct` | % change from previous close |
| `volume_spike` | Volume > 2× 20-bar average |
| `near_52w_high` | Within 5% of 52-week high |
| `near_52w_low` | Within 5% of 52-week low |
""")

    with al2:
        alert_results = st.session_state.get("alert_results", [])

        if not alert_results:
            st.markdown(
                "<div style='padding:2rem;text-align:center;color:#4a6075'>"
                "Define conditions and click <b>Check alerts</b>."
                "</div>", unsafe_allow_html=True
            )
        else:
            st.markdown("#### Results")

            # summary table
            rows = []
            for r in alert_results:
                sym = r["symbol"]
                if r["error"]:
                    rows.append({"Symbol": sym, "Condition": "—",
                                 "Result": r["error"]})
                    continue
                for cr in r["conditions"]:
                    val = cr["result"]
                    if val is True:
                        badge = "🟢 TRUE"
                    elif val is False:
                        badge = "⚪ false"
                    else:
                        badge = "⚠ unavailable"
                    rows.append({
                        "Symbol":    sym,
                        "Condition": cr["condition"],
                        "Result":    badge,
                    })
            st.dataframe(pd.DataFrame(rows),
                         width='stretch', hide_index=True)

            # ── indicator snapshot for selected symbol ─────────────────────────
            snap_sym = st.selectbox("Indicator snapshot for",
                                     [r["symbol"] for r in alert_results
                                      if not r.get("error")],
                                     key="al_snap")
            snap = next((r for r in alert_results
                          if r["symbol"] == snap_sym and not r.get("error")), None)
            if snap:
                ctx = snap["ctx"]
                with st.expander(f"All indicators for {snap_sym}"):
                    snap_rows = [
                        {"Indicator": k, "Value": str(round(v, 4)) if isinstance(v, float) else str(v)}
                        for k, v in sorted(ctx.items()) if v is not None
                    ]
                    st.dataframe(pd.DataFrame(snap_rows),
                                 width='stretch', hide_index=True)

            if dry_run:
                st.caption("ℹ️  Dry run — no state saved, no notifications sent.")


# ═════════════════════════════════════════════
#  TAB 5 — BRIEFING  (Claude AI analyst)
# ═════════════════════════════════════════════

with tab_brief:

    # ── helpers ───────────────────────────────────────────────────────────────

    def _build_context(symbols, date_from_str, date_to_str, horizon,
                        mc_paths=500) -> dict:
        """Collect all analytical data for the briefing prompt."""
        profile = ss.HORIZON_PROFILES[horizon]
        scores  = []
        alerts_ctx = {}

        for sym in symbols:
            df = get_prices(sym, date_from_str, date_to_str)
            if df.empty or len(df) < 10:
                continue

            gran = profile["gran"]
            try:
                df_r = df.set_index("timestamp").resample(gran).agg(
                    {"open":"first","high":"max","low":"min",
                     "close":"last","volume":"sum"}
                ).dropna(subset=["close"]).reset_index()
            except Exception:
                fb   = {"ME":"M","QE":"Q"}.get(gran, gran)
                df_r = df.set_index("timestamp").resample(fb).agg(
                    {"open":"first","high":"max","low":"min",
                     "close":"last","volume":"sum"}
                ).dropna(subset=["close"]).reset_index()

            if len(df_r) < 10:
                continue

            raw = {
                "symbol":     sym,
                "summary":    ss.step_summary(df_r, ann_factor=profile["ann_factor"]),
                "regression": ss.step_regression(df_r),
                "drawdown":   ss.step_drawdown(df_r),
                "entry":      ss.step_entry_timing(df_r),
                "montecarlo": ss.step_montecarlo(df_r, mc_paths,
                                                   profile["mc_bars"]),
            }
            score, notes = ss.score_symbol(
                raw, weights=profile["weights"],
                min_bars=profile["min_bars"]
            )
            raw["score"] = score
            scores.append(raw)

            # indicator snapshot for alerts
            df_a = sal.load_series(sym, n_bars=300)
            if not df_a.empty:
                alerts_ctx[sym] = sal.build_context(df_a)

        scores.sort(key=lambda x: x["score"], reverse=True)
        return scores, alerts_ctx


    def _call_claude(messages: list, system: str) -> str:
        """Call the Claude API and return the text response."""
        import os
        # Key resolution: config.env ANTHROPIC_KEY → env var ANTHROPIC_API_KEY
        api_key = (
            _cfg.get("ANTHROPIC_KEY", "").strip()     # config.env
            or os.environ.get("ANTHROPIC_API_KEY", "") # environment variable
        )
        if not api_key:
            return (
                "⚠️  No Claude API key found.\n\n"
                "Add one of:\n"
                "  • `ANTHROPIC_KEY=sk-ant-...` in config.env\n"
                "  • `export ANTHROPIC_API_KEY=sk-ant-...` in your shell before starting Streamlit"
            )
        try:
            resp = __import__("requests").post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "Content-Type":            "application/json",
                    "x-api-key":               api_key,
                    "anthropic-version":       "2023-06-01",
                },
                json={
                    "model":      "claude-sonnet-4-6",
                    "max_tokens": 1500,
                    "system":     system,
                    "messages":   messages,
                },
                timeout=30,
            )
            data = resp.json()
            if resp.status_code != 200:
                return f"API error {resp.status_code}: {data.get('error',{}).get('message','unknown')}"
            blocks = data.get("content", [])
            return "\n".join(b["text"] for b in blocks if b.get("type") == "text")
        except Exception as e:
            return f"Request failed: {e}"


    def _scores_to_summary(scores: list) -> str:
        """Convert score dicts to a compact text table for the prompt."""
        lines = ["Symbol   Score  Sharpe  Calmar  Vol     MaxDD    RSI   %B    P(gain)"]
        lines.append("─" * 70)
        for r in scores:
            s  = r.get("summary",    {})
            dd = r.get("drawdown",   {})
            en = r.get("entry",      {})
            mc = r.get("montecarlo", {})
            lines.append(
                f"{r['symbol']:<9}"
                f"{r['score']:>5.1f}  "
                f"{s.get('sharpe', 0):>6.2f}  "
                f"{dd.get('calmar', 0):>6.2f}  "
                f"{s.get('ann_vol', 0):>5.1f}%  "
                f"{dd.get('max_dd', 0):>6.1f}%  "
                f"{en.get('rsi14', 0) or 0:>4.0f}  "
                f"{en.get('pct_b', 0) or 0:>5.2f}  "
                f"{mc.get('prob_gain', 0):>5.1f}%"
            )
        return "\n".join(lines)


    SYSTEM_PROMPT = """\
You are a personal finance assistant helping a retail investor \
screen stocks for educational purposes. \
The investor treats the stock market as a learning game — \
small positions, studying how markets work, not managing a serious portfolio. \
Be direct, practical, and honest about uncertainty. \
Never recommend specific investment amounts. \
Always remind the user that this is data analysis, not financial advice. \
Keep responses concise and conversational."""

    # ── UI ────────────────────────────────────────────────────────────────────

    st.markdown(
        "Runs the full 7-step analysis on your watchlist and lets you ask "
        "Claude to interpret the results in plain English."
    )

    bf1, bf2 = st.columns([2, 3])
    with bf1:
        brief_horizon = st.selectbox(
            "Horizon", list(ss.HORIZON_PROFILES.keys()), index=2,
            format_func=lambda h: {
                "week": "Week", "month": "Month",
                "quarter": "Quarter (default)",
                "year": "Year", "life": "Life",
            }[h],
            key="brief_horizon"
        )
        brief_broker = st.selectbox(
            "Broker (for fee context)",
            ["Yuh (0.5% + FX)", "Interactive Brokers (~$0.35)",
             "Saxo Bank (0.08%)", "DEGIRO (~€1.75)", "Other"],
            key="brief_broker"
        )
        brief_budget = st.number_input(
            "Available budget (CHF)", min_value=100, max_value=100000,
            value=500, step=100, key="brief_budget"
        )

    with bf2:
        st.markdown(
            "<span style='color:#4a6075;font-size:0.82rem'>"
            "The briefing will analyse all symbols selected in the sidebar. "
            "It calls the Claude API once per conversation turn — "
            "no data is stored outside this session."
            "</span>",
            unsafe_allow_html=True
        )

    # initialise chat history in session state
    if "brief_messages" not in st.session_state:
        st.session_state.brief_messages = []
    if "brief_context" not in st.session_state:
        st.session_state.brief_context  = None

    # ── generate briefing button ──────────────────────────────────────────────
    if st.button("📋  Generate today's briefing", type="primary"):
        with st.spinner("Running 7-step analysis on all symbols…"):
            scores, alerts_ctx = _build_context(
                selected_symbols, date_from_str, date_to_str,
                brief_horizon, mc_paths=500
            )
        st.session_state.brief_context = {
            "scores":     scores,
            "alerts_ctx": alerts_ctx,
        }
        st.session_state.brief_messages = []   # reset chat on new briefing

        if not scores:
            st.warning("No data found. Run stock_collector.py first.")
            st.stop()

        # build the initial briefing prompt
        score_table   = _scores_to_summary(scores)
        alert_summary = []
        for sym, ctx in alerts_ctx.items():
            rsi = ctx.get("rsi14")
            pb  = ctx.get("bbands_pct_b")
            sq  = ctx.get("bbands_squeeze")
            chg = ctx.get("change_pct")
            if rsi is not None:
                alert_summary.append(
                    f"  {sym}: RSI={rsi:.0f}, %B={pb:.2f}"
                    + (" ⚡SQUEEZE" if sq else "")
                    + (f", change={chg:+.1f}%" if chg else "")
                )

        user_msg = (
            f"Today's watchlist analysis — {brief_horizon} horizon\n"
            f"Budget: {brief_budget} CHF | Broker: {brief_broker}\n"
            f"Date range: {date_from_str} → {date_to_str}\n\n"
            f"SCORES (ranked best→worst):\n{score_table}\n\n"
            f"CURRENT INDICATORS:\n" + "\n".join(alert_summary) + "\n\n"
            "Please give me:\n"
            "1. A 2-3 sentence plain-English summary of what stands out\n"
            "2. The top 2 symbols worth watching and why\n"
            "3. Any red flags to avoid right now\n"
            "4. One honest limitation of this analysis I should keep in mind"
        )

        st.session_state.brief_prompt = user_msg   # store before call so expanders work even on failure

        with st.spinner("Claude is reading the numbers…"):
            reply = _call_claude(
                [{"role": "user", "content": user_msg}],
                SYSTEM_PROMPT
            )

        st.session_state.brief_messages = [
            {"role": "user",      "content": user_msg},
            {"role": "assistant", "content": reply},
        ]

    # ── chat interface ─────────────────────────────────────────────────────────
    # ── always show analysis + prompt once data is computed ───────────────────
    # These render even if the Claude API call failed — the 7-step data
    # and the prompt are available as soon as the analysis button is clicked.
    ctx = st.session_state.get("brief_context")
    if ctx and ctx.get("scores"):
        st.markdown("---")

        # ── Claude response (if available) ────────────────────────────────────
        if st.session_state.brief_messages:
            for i, msg in enumerate(st.session_state.brief_messages):
                if msg["role"] == "assistant":
                    with st.chat_message("assistant", avatar="🤖"):
                        st.markdown(msg["content"])
                elif i > 0:
                    with st.chat_message("user", avatar="👤"):
                        st.markdown(msg["content"])

        # ── 7-step analysis results ───────────────────────────────────────────
        with st.expander("📊  7-step analysis — full metrics", expanded=False):
            scores = ctx["scores"]
            rows = []
            for r in scores:
                s  = r.get("summary",    {})
                dd = r.get("drawdown",   {})
                en = r.get("entry",      {})
                mc = r.get("montecarlo", {})
                rg = r.get("regression", {})
                rows.append({
                    "Symbol":     r["symbol"],
                    "Score":      f"{r['score']:.1f}",
                    "Sharpe":     fmt_val(s.get("sharpe")),
                    "Calmar":     fmt_val(dd.get("calmar")),
                    "Vol %":      f"{s['ann_vol']:.1f}" if s.get("ann_vol") else "—",
                    "Max DD %":   f"{dd['max_dd']:.1f}" if dd.get("max_dd") else "—",
                    "Recovered":  "✅" if dd.get("recovered") else "❌",
                    "R²":         fmt_val(rg.get("r2"), 3),
                    "Trend/yr %": fmt_pct(rg.get("ann_trend")),
                    "RSI":        f"{en['rsi14']:.0f}" if en.get("rsi14") else "—",
                    "%B":         f"{en['pct_b']:.2f}" if en.get("pct_b") is not None else "—",
                    "⚡":         "yes" if en.get("bbands_squeeze") else "",
                    "P(gain) %":  f"{mc['prob_gain']:.0f}" if mc.get("prob_gain") else "—",
                    "P50":        fmt_val(mc.get("p50")),
                    "P5":         fmt_val(mc.get("p5")),
                    "Total ret %":fmt_pct(s.get("total_ret")),
                    "Bars":       str(s.get("n_bars", "—")),
                })
            st.dataframe(pd.DataFrame(rows),
                         width="stretch", hide_index=True)

            if ctx.get("alerts_ctx"):
                st.markdown("**Current indicators (live)**")
                ind_rows = []
                for sym, ictx in ctx["alerts_ctx"].items():
                    ind_rows.append({
                        "Symbol":   sym,
                        "Price":    fmt_val(ictx.get("price")),
                        "RSI14":    f"{ictx['rsi14']:.1f}" if ictx.get("rsi14") else "—",
                        "%B":       f"{ictx['bbands_pct_b']:.2f}" if ictx.get("bbands_pct_b") is not None else "—",
                        "Squeeze":  "⚡" if ictx.get("bbands_squeeze") else "",
                        "MACD hist":fmt_val(ictx.get("macd_hist")),
                        "Chg %":    fmt_pct(ictx.get("change_pct")),
                        "SMA50":    fmt_val(ictx.get("sma50")),
                        "SMA200":   fmt_val(ictx.get("sma200")),
                        ">52w low": "✅" if ictx.get("near_52w_low") else "",
                        ">52w hi":  "🔴" if ictx.get("near_52w_high") else "",
                    })
                st.dataframe(pd.DataFrame(ind_rows),
                             width="stretch", hide_index=True)

        # ── prompt inspector ──────────────────────────────────────────────────
        prompt = st.session_state.get("brief_prompt")
        if prompt:
            with st.expander("🔍  Prompt sent to Claude", expanded=False):
                st.markdown(
                    "<span style='color:#4a6075;font-size:0.78rem'>"
                    "This is the exact text sent to the Claude API."
                    "</span>",
                    unsafe_allow_html=True,
                )
                st.markdown("**System prompt:**")
                st.code(SYSTEM_PROMPT, language=None)
                st.markdown("**User message (analysis data):**")
                st.code(prompt, language=None)

        # ── follow-up chat ────────────────────────────────────────────────────
        follow_up = st.chat_input(
            "Ask a follow-up question about any symbol or signal…"
        )
        if follow_up:
            st.session_state.brief_messages.append(
                {"role": "user", "content": follow_up}
            )
            with st.spinner("Thinking…"):
                reply = _call_claude(
                    st.session_state.brief_messages,
                    SYSTEM_PROMPT
                )
            st.session_state.brief_messages.append(
                {"role": "assistant", "content": reply}
            )
            st.rerun()

        col_reset, col_cap = st.columns([1, 4])
        with col_reset:
            if st.button("🗑  Clear", key="brief_clear"):
                st.session_state.brief_messages = []
                st.session_state.brief_context  = None
                st.session_state.brief_prompt   = None
                st.rerun()
        with col_cap:
            st.caption(
                "⚠️  Educational analysis only — not financial advice."
            )


# ═════════════════════════════════════════════
#  TAB 6 — COLLECT
# ═════════════════════════════════════════════

with tab_collect:
    import subprocess

    st.markdown("### 📥  Data Collection")
    st.markdown(
        "Run the data collector on demand — useful for adding a new symbol "
        "or refreshing data outside the cron schedule. "
        "Background cron jobs continue to run independently."
    )

    # ── allowed sources from config ───────────────────────────────────────────
    _raw_sources = _cfg.get("UI_COLLECT_SOURCES", "yfinance")
    ALLOWED_SOURCES = [s.strip() for s in _raw_sources.split(",") if s.strip()]
    ALL_SOURCES = ["yfinance", "alphavantage", "finnhub", "polygon",
                   "fmp", "twelvedata", "marketstack"]

    # ── layout ────────────────────────────────────────────────────────────────
    col_sym, col_src = st.columns([2, 3])

    with col_sym:
        new_symbol = st.text_input(
            "Add / collect symbol",
            placeholder="e.g. NVDA or RACE.MI",
            help=(
                "Leave blank to collect all symbols already in config.env and the DB. "
                "Enter a ticker to collect that symbol specifically — "
                "once collected it will be picked up by the cron jobs automatically."
            ),
            key="collect_symbol_input"
        ).strip().upper()

    with col_src:
        selected_sources = st.multiselect(
            "Sources",
            options=ALLOWED_SOURCES,
            default=ALLOWED_SOURCES,
            help=(
                "Sources available here are controlled by UI_COLLECT_SOURCES in config.env. "
                "Default: yfinance only (no API key needed, no rate limits)."
            ),
            key="collect_sources_select"
        )

    # note if sources are restricted
    locked_out = [s for s in ALL_SOURCES if s not in ALLOWED_SOURCES]
    if locked_out:
        st.caption(
            f"🔒  {', '.join(locked_out)} are disabled for the UI. "
            "To enable them add them to `UI_COLLECT_SOURCES` in config.env."
        )

    # ── run button ────────────────────────────────────────────────────────────
    st.markdown("---")
    run_col, info_col = st.columns([1, 3])

    with run_col:
        run_clicked = st.button(
            "▶  Run collection",
            type="primary",
            disabled=not selected_sources,
            key="collect_run_btn"
        )

    with info_col:
        if not selected_sources:
            st.warning("Select at least one source.")
        else:
            cmd_preview = ["python3", "stock_collector.py"]
            if new_symbol:
                cmd_preview += ["-s", new_symbol]
            cmd_preview += ["--sources"] + selected_sources
            st.code(" ".join(cmd_preview), language="bash")

    # ── execute ───────────────────────────────────────────────────────────────
    if run_clicked and selected_sources:
        collector = SCRIPT_DIR / "stock_collector.py"
        if not collector.exists():
            st.error(f"stock_collector.py not found in {SCRIPT_DIR}")
        else:
            cmd = [sys.executable, str(collector)]
            if new_symbol:
                cmd += ["-s", new_symbol]
            cmd += ["--sources"] + selected_sources

            label = f"Collecting {'`' + new_symbol + '`' if new_symbol else 'all symbols'} via {', '.join(selected_sources)}…"
            output_area = st.empty()

            with st.spinner(label):
                try:
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        cwd=str(SCRIPT_DIR),
                        timeout=300,   # 5 min hard limit
                    )
                    stdout = result.stdout.strip()
                    stderr = result.stderr.strip()
                    combined = "\n".join(filter(None, [stdout, stderr]))
                except subprocess.TimeoutExpired:
                    combined = "⏱  Collection timed out after 5 minutes."
                    result = None
                except Exception as e:
                    combined = f"Error launching collector: {e}"
                    result = None

            # ── results ───────────────────────────────────────────────────────
            if result is not None and result.returncode == 0:
                st.success("✅  Collection finished successfully.")
                get_all_symbols.clear()   # invalidate cache → sidebar refreshes on next rerun
            elif result is not None:
                st.error(f"❌  Collector exited with code {result.returncode}.")
            # else: timeout/exception already shown

            with st.expander("📋  Collector output", expanded=True):
                if combined:
                    st.code(combined, language=None)
                else:
                    st.write("_(no output)_")

            # refresh symbol count hint
            if new_symbol and result is not None and result.returncode == 0:
                st.info(
                    f"**{new_symbol}** has been collected and added to the database. "
                    "It will now be included in all future cron collection runs automatically."
                )

    # ── currently tracked symbols ─────────────────────────────────────────────
    st.markdown("---")
    st.markdown("**Currently tracked symbols**")

    try:
        import sqlite3 as _sq3
        _db = SCRIPT_DIR / _cfg.get("DB_FILE", "stock_data.db")
        if _db.exists():
            _con = _sq3.connect(_db)
            _syms = _con.execute(
                "SELECT symbol, COUNT(*) as n, MIN(timestamp) as first, "
                "MAX(timestamp) as last FROM prices WHERE interval='1d' "
                "GROUP BY symbol ORDER BY symbol"
            ).fetchall()
            _con.close()
            if _syms:
                _df = pd.DataFrame(_syms, columns=["Symbol", "Bars", "First", "Last"])
                _df["In config"] = _df["Symbol"].apply(
                    lambda s: "✅" if s in selected_symbols else "—"
                )
                st.dataframe(_df, hide_index=True)
            else:
                st.write("No daily data found yet. Run the collector first.")
        else:
            st.write("Database not found. Run the collector first.")
    except Exception as e:
        st.warning(f"Could not read database: {e}")
