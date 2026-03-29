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

# ── lazy imports with friendly error messages ─────────────────────────────────
try:
    import plotly.graph_objects as go
    import plotly.express as px
    from plotly.subplots import make_subplots
except ImportError:
    st.error("plotly not installed — run: pip install plotly")
    st.stop()

try:
    import stock_score  as ss
    import stock_analysis as sa
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
        x=df["data_date"], y=df["close"],
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
        x=df["data_date"], y=dd,
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
    fig.add_trace(go.Scatter(x=df["data_date"], y=df["close"],
                             mode="lines", name="Price",
                             line=dict(color="#38bdf8", width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df["data_date"], y=rsi,
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
    fig.add_trace(go.Scatter(x=df["data_date"], y=upper,
                             line=dict(color="#6b7280", width=0.8, dash="dot"),
                             name="Upper", showlegend=False))
    fig.add_trace(go.Scatter(x=df["data_date"], y=lower,
                             fill="tonexty", fillcolor="rgba(56,189,248,0.06)",
                             line=dict(color="#6b7280", width=0.8, dash="dot"),
                             name="Lower", showlegend=False))
    fig.add_trace(go.Scatter(x=df["data_date"], y=mid,
                             line=dict(color="#6b7280", width=1),
                             name="SMA", showlegend=False))
    fig.add_trace(go.Scatter(x=df["data_date"], y=close,
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

tab_score, tab_analysis, tab_backtest, tab_alerts = st.tabs([
    "🏆  Score", "📊  Analysis", "🔁  Backtest", "🔔  Alerts"
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
                df_r = df.set_index("data_date").resample(gran).agg(
                    {"open":"first","high":"max","low":"min","close":"last","volume":"sum"}
                ).dropna(subset=["close"]).reset_index()
            except Exception:
                fb = {"ME":"M","QE":"Q"}.get(gran, gran)
                df_r = df.set_index("data_date").resample(fb).agg(
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
    col_left, col_right = st.columns([1, 3])

    with col_left:
        analysis_sym = st.selectbox("Symbol", selected_symbols, key="an_sym")
        analysis_tool = st.radio(
            "Tool",
            ["Price", "RSI", "Bollinger Bands", "Drawdown", "Monte Carlo", "Summary"],
            key="an_tool"
        )
        if analysis_tool == "RSI":
            rsi_w = st.slider("RSI window", 7, 30, 14, key="an_rsi_w")
        if analysis_tool == "Bollinger Bands":
            bb_w = st.slider("BB window", 10, 50, 20, key="an_bb_w")
        if analysis_tool == "Monte Carlo":
            mc_horizon = st.select_slider("Horizon (bars)",
                                           [5, 21, 63, 126, 252], value=63,
                                           key="an_mc_h")
            mc_n = st.select_slider("Paths",
                                     [200, 500, 1000, 2000], value=500,
                                     key="an_mc_n")

    with col_right:
        df = get_prices(analysis_sym, date_from_str, date_to_str)
        if df.empty:
            st.warning(f"No data for {analysis_sym} in this date range.")
        else:
            if analysis_tool == "Price":
                st.plotly_chart(price_chart(df, analysis_sym),
                                width='stretch')
                # quick stats
                rets = df["close"].pct_change().dropna()
                af   = 52
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Last price",   f"{df['close'].iloc[-1]:.2f}")
                m2.metric("Total return", f"{(df['close'].iloc[-1]/df['close'].iloc[0]-1)*100:+.1f}%")
                m3.metric("Ann. vol",     f"{rets.std()*np.sqrt(252)*100:.1f}%")
                m4.metric("Sharpe",       f"{rets.mean()/rets.std()*np.sqrt(252):.2f}")

            elif analysis_tool == "RSI":
                st.plotly_chart(rsi_chart(df, rsi_w),
                                width='stretch')
                rsi_val = ss._rsi(df["close"].dropna(), rsi_w)
                if not np.isnan(rsi_val):
                    signal = ("🔴 Overbought" if rsi_val > 70 else
                              "🟢 Oversold"   if rsi_val < 30 else
                              "⚪ Neutral")
                    st.markdown(f"**Current RSI({rsi_w}) = {rsi_val:.1f}  →  {signal}**")

            elif analysis_tool == "Bollinger Bands":
                st.plotly_chart(bbands_chart(df, bb_w),
                                width='stretch')
                pb = ss._pct_b(df["close"].dropna(), bb_w)
                sq = ss._bbands_squeeze(df["close"].dropna(), bb_w)
                if not np.isnan(pb):
                    st.markdown(
                        f"**%B = {pb:.2f}**  "
                        + ("  ⚡ Squeeze active — potential breakout" if sq else "")
                    )

            elif analysis_tool == "Drawdown":
                st.plotly_chart(drawdown_chart(df), width='stretch')
                res = ss.step_drawdown(df)
                if res:
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Max DD",  f"{res['max_dd']:.1f}%")
                    m2.metric("Calmar",  f"{res['calmar']:.2f}")
                    m3.metric("Recovered", "Yes ✅" if res["recovered"] else "No ❌")
                    m4.metric("Ann. return", f"{res['ann_ret']:.1f}%")

            elif analysis_tool == "Monte Carlo":
                st.plotly_chart(mc_chart(df, mc_n, mc_horizon),
                                width='stretch')

            elif analysis_tool == "Summary":
                res = ss.step_summary(df, ann_factor=52)
                reg = ss.step_regression(df)
                if res and reg:
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Total return", fmt_pct(res.get("total_ret")))
                    m2.metric("Sharpe",       fmt_val(res.get("sharpe")))
                    m3.metric("Ann. vol",     f"{res.get('ann_vol','—'):.1f}%")
                    m4.metric("R²",           fmt_val(reg.get("r2"), 3))
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Ann. trend",   fmt_pct(reg.get("ann_trend")))
                    m2.metric("First price",  fmt_val(res.get("first")))
                    m3.metric("Last price",   fmt_val(res.get("last")))
                    m4.metric("Bars",         str(res.get("n_bars", "—")))
                    st.plotly_chart(price_chart(df, analysis_sym),
                                    width='stretch')


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

