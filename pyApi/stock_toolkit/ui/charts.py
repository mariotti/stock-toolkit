"""Plotly chart builders for the dashboard."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from stock_toolkit import score as ss
from stock_toolkit.ui.helpers import fmt_pct, fmt_val, score_color
from stock_toolkit.ui.theme import (
    CHART_AXIS, CHART_BG, CHART_FONT, CHART_GRID, CHART_INK, CHART_MUTED,
)

# ─────────────────────────────────────────────
#  PLOTLY CHART HELPERS
# ─────────────────────────────────────────────

CHART_LAYOUT = dict(
    template="plotly_white",
    paper_bgcolor=CHART_BG,
    plot_bgcolor=CHART_BG,
    font=dict(family=CHART_FONT, size=11, color=CHART_MUTED),
    margin=dict(l=48, r=16, t=36, b=36),
    # Legend labels (e.g. "Price", "RSI") need higher contrast than the
    # general chart font — they're a key UI element users scan first.
    legend=dict(bgcolor="rgba(0,0,0,0)",
                font=dict(size=10, color=CHART_INK)),
    xaxis=dict(gridcolor=CHART_GRID, linecolor=CHART_AXIS, zeroline=False),
    yaxis=dict(gridcolor=CHART_GRID, linecolor=CHART_AXIS, zeroline=False),
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
    fig.update_xaxes(range=[0, 105], gridcolor=CHART_GRID)
    fig.update_yaxes(autorange="reversed", gridcolor=CHART_GRID)
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
    fig.update_xaxes(gridcolor=CHART_GRID, linecolor=CHART_AXIS)
    fig.update_yaxes(gridcolor=CHART_GRID, linecolor=CHART_AXIS)
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
        colorscale=[[0, "#f87171"], [0.5, "#f5f7fa"], [1, "#4ade80"]],
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


