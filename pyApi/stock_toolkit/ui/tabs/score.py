"""Score tab."""


import pandas as pd
import streamlit as st

from stock_toolkit import score as ss
from stock_toolkit.ui.charts import (
    score_bar_chart,
)
from stock_toolkit.ui.helpers import (
    fmt_val, get_prices,
)


def render(selected_symbols, date_from_str, date_to_str):
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
        skipped = []
        progress = st.progress(0, text="Scoring symbols...")
        for i, sym in enumerate(selected_symbols):
            progress.progress((i + 1) / len(selected_symbols), text=f"Scoring {sym}…")
            df = get_prices(sym, date_from_str, date_to_str)
            if df.empty or len(df) < 10:
                skipped.append(sym)
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
                skipped.append(sym)
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
        st.session_state["score_skipped"] = skipped
        st.session_state["score_ran"] = True

    results = st.session_state.get("score_results", [])
    if not results:
        skipped = st.session_state.get("score_skipped", [])
        if st.session_state.get("score_ran") and skipped:
            st.warning(
                f"None of the {len(skipped)} selected symbol(s) had enough "
                f"bars for the **{horizon}** horizon over "
                f"{date_from_str} → {date_to_str}. "
                "Widen the date range (try the **5Y** or **Max** preset) "
                "or choose a shorter horizon."
            )
        else:
            st.markdown(
                "<div style='padding:2rem;text-align:center;color:#8ba0b4'>"
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


