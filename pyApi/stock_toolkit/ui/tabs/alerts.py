"""Alerts tab."""


import pandas as pd
import streamlit as st

from stock_toolkit import alerts as sal


def render(selected_symbols, date_from_str, date_to_str):
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
                "<div style='padding:2rem;text-align:center;color:#8ba0b4'>"
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


