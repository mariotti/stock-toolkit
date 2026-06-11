"""Backtest tab."""


import pandas as pd
import streamlit as st

from stock_toolkit import backtest as sb
from stock_toolkit.ui.charts import (
    equity_chart,
)
from stock_toolkit.ui.helpers import (
    fmt_pct, fmt_val, get_prices,
)


def render(selected_symbols, date_from_str, date_to_str):
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


