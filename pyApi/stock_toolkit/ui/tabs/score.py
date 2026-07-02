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
            needed = {
                "week": "~6 weeks", "month": "~3 months", "quarter": "~1 year",
                "year": "~2 years", "life": "~10 years",
            }.get(horizon, "a longer history")
            st.warning(
                f"**This is not missing price data.** Your prices for "
                f"{date_from_str} → {date_to_str} are there (see the Analysis "
                f"tab) — the **{horizon}** score just needs about **{needed}** "
                f"of history to compute, and this window is too short, so all "
                f"{len(skipped)} symbol(s) were skipped.\n\n"
                "→ Pick the **5Y** or **Max** range preset (or a shorter "
                "horizon), then **Run scoring** again."
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

    # ── self-validation: does this score actually predict returns? ────────────
    st.markdown("---")
    with st.expander("🔬  Does this score predict returns?  (walk-forward backtest)"):
        st.caption(
            "Scores every selected symbol as-of many past dates — using only "
            "the data available then — and measures the **real forward return** "
            "afterwards. A score worth trusting shows a positive, statistically "
            "significant Information Coefficient (IC). This is the honest check "
            "on whether the ranking above is predictive or just plausible-looking."
        )
        bcol1, bcol2 = st.columns(2)
        bt_lookback = bcol1.select_slider(
            "History per score (years)", [2, 3, 5, 10], value=5,
            key="score_bt_lookback")
        bt_rebal = bcol2.select_slider(
            "Rebalance every (months)", [3, 6, 12], value=6,
            key="score_bt_rebal")
        if len(selected_symbols) < 4:
            st.info("Pick at least ~4 symbols in the sidebar for a meaningful "
                    "cross-sectional test (more symbols = more reliable).")
        if st.button("🔬  Run score backtest", key="score_bt_run"):
            from stock_toolkit.score_validation import run_score_backtest
            with st.spinner("Walk-forward scoring across history… (can take a minute)"):
                st.session_state["score_bt"] = run_score_backtest(
                    selected_symbols, horizon,
                    lookback_years=bt_lookback, rebalance_months=bt_rebal,
                    mc_paths=200)

        res = st.session_state.get("score_bt")
        if res:
            if res["n_obs"] == 0:
                st.warning(res["verdict"])
            else:
                signal = ("significant signal" in res["verdict"].lower()
                          and "wrong way" not in res["verdict"].lower())
                (st.success if signal else st.warning)(res["verdict"])
                b1, b2, b3, b4 = st.columns(4)
                b1.metric("Mean IC", f"{res['mean_ic']:+.3f}")
                b2.metric("t-stat", f"{res['ic_tstat']:+.2f}")
                b3.metric("Dates IC>0", f"{res['pct_positive_ic']:.0f}%")
                b4.metric("Observations", f"{res['n_obs']}")
                st.caption(
                    f"{res['n_dates']} rebalance dates · {res['n_symbols']} "
                    f"symbols · forward window {res['forward_bars']} trading "
                    "days.  Rule of thumb: IC ~0.05 modest, ~0.10 strong; "
                    "|t| > 2 to be real.")
                terc = res["tercile_returns"]
                if all(v is not None for v in terc.values()):
                    import plotly.graph_objects as go
                    fig = go.Figure(go.Bar(
                        x=["Low score", "Mid", "High score"],
                        y=[terc["low"] * 100, terc["mid"] * 100,
                           terc["high"] * 100],
                        text=[f"{terc[k] * 100:+.2f}%"
                              for k in ("low", "mid", "high")],
                        textposition="outside",
                    ))
                    fig.update_layout(
                        title="Forward return by score tercile "
                              "(vs each date's average)",
                        yaxis_title="avg forward return %", height=300)
                    st.plotly_chart(fig, width='stretch',
                                    config={"displayModeBar": False})
                    if res["high_minus_low"] is not None:
                        st.caption(
                            "High-minus-low spread: "
                            f"**{res['high_minus_low'] * 100:+.2f}%** — positive "
                            "means top-scored beat bottom-scored.")


