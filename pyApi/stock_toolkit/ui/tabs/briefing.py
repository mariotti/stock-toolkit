"""Briefing (Claude) tab."""


import pandas as pd
import streamlit as st

from stock_toolkit import alerts as sal
from stock_toolkit import score as ss
from stock_toolkit.ui.helpers import (
    _cfg, fmt_pct, fmt_val, get_fundamentals, get_prices,
)


def _fundamentals_to_summary(funda: dict) -> str:
    """Format the yfinance valuation snapshot as a compact text table."""
    if not funda:
        return ""

    def _num(v, pct=False):
        if v is None:
            return "   n/a"
        return f"{v * 100:>+5.1f}%" if pct else f"{v:>6.1f}"

    lines = ["Symbol     P/E   Fwd P/E  Rev growth (YoY)  EPS growth (YoY)"]
    lines.append("─" * 60)
    for sym, row in funda.items():
        lines.append(
            f"{sym:<9}"
            f"{_num(row.get('trailing_pe'))}  "
            f"{_num(row.get('forward_pe'))}   "
            f"{_num(row.get('revenue_growth'), pct=True):>14}  "
            f"{_num(row.get('earnings_growth'), pct=True):>14}"
        )
    return "\n".join(lines)


def _with_cache_breakpoints(messages: list) -> list:
    """Add prompt-caching breakpoints to a string-content message list.

    Two `cache_control` markers (Anthropic prompt caching, prefix-matched):
      - first message — the large market-data context, stable for the whole
        conversation, so system + context are cached once per briefing
      - last message — each turn extends the cached prefix incrementally

    Marked messages get block-form content; the rest stay as plain strings.
    """
    out = []
    last = len(messages) - 1
    for i, msg in enumerate(messages):
        if i in (0, last) and isinstance(msg.get("content"), str):
            out.append({
                "role": msg["role"],
                "content": [{
                    "type": "text",
                    "text": msg["content"],
                    "cache_control": {"type": "ephemeral"},
                }],
            })
        else:
            out.append(msg)
    return out


def render(selected_symbols, date_from_str, date_to_str):

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
        # Key resolution: config.env ANTHROPIC_API_KEY (or legacy
        # ANTHROPIC_KEY) → env var ANTHROPIC_API_KEY
        api_key = (
            _cfg.get("ANTHROPIC_API_KEY", "").strip()   # config.env
            or _cfg.get("ANTHROPIC_KEY", "").strip()    # config.env (legacy name)
            or os.environ.get("ANTHROPIC_API_KEY", "")  # environment variable
        )
        if not api_key:
            return (
                "⚠️  No Claude API key found.\n\n"
                "Add one of:\n"
                "  • `ANTHROPIC_API_KEY=sk-ant-...` in config.env\n"
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
                    "messages":   _with_cache_breakpoints(messages),
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
        with st.spinner("Fetching fundamentals (P/E, growth)…"):
            funda_table = _fundamentals_to_summary(
                get_fundamentals(tuple(r["symbol"] for r in scores))
            )
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
            + (f"FUNDAMENTALS (valuation snapshot, yfinance):\n{funda_table}\n\n"
               if funda_table else "")
            + "CURRENT INDICATORS:\n" + "\n".join(alert_summary) + "\n\n"
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


