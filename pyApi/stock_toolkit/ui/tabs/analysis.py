"""Analysis tab."""


import numpy as np
import pandas as pd
import streamlit as st

from stock_toolkit import score as ss
from stock_toolkit.ui.charts import (
    bbands_chart, correlation_heatmap, drawdown_compare_chart,
    mc_chart, price_compare_chart, rsi_chart,
    summary_table,
)
from stock_toolkit.ui.helpers import (
    fmt_val, get_prices,
)


def render(selected_symbols, date_from_str, date_to_str):
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


