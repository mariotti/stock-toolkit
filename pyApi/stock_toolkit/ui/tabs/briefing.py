"""Briefing (Claude) tab."""


import json
import re

import pandas as pd
import streamlit as st

from stock_toolkit.ui.icons import heading, icon

# Canonical name of the auto-managed paper-trading strategy that
# executes Claude's structured trade proposals from the Briefing tab.
BRIEFING_STRATEGY_NAME = "Briefing strategy"

# Sentinel used to fence the JSON proposal block in Claude replies.
TRADE_BLOCK_OPEN  = "<<<TRADE_PROPOSALS_JSON"
TRADE_BLOCK_CLOSE = ">>>"
_TRADE_BLOCK_RE = re.compile(
    re.escape(TRADE_BLOCK_OPEN) + r"\s*(\[.*?\])\s*" + re.escape(TRADE_BLOCK_CLOSE),
    re.DOTALL,
)


def _parse_trade_proposals(text: str):
    """Pull a TRADE_PROPOSALS_JSON block out of Claude's reply.

    Returns (proposals, cleaned_text). proposals is a list of dicts;
    cleaned_text has the block stripped so the chat renders cleanly."""
    if not text:
        return [], text
    m = _TRADE_BLOCK_RE.search(text)
    if not m:
        return [], text
    raw = m.group(1)
    try:
        proposals = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return [], text
    if not isinstance(proposals, list):
        return [], text
    cleaned = (text[:m.start()].rstrip() + "\n\n"
               + text[m.end():].lstrip()).strip()
    return proposals, cleaned


def _briefing_strategy_record():
    """Lookup the 'Briefing strategy' portfolio (or None if not created yet)."""
    from stock_toolkit.game import list_portfolios
    for p in list_portfolios():
        if p["name"] == BRIEFING_STRATEGY_NAME:
            return p
    return None


def _briefing_state_summary() -> str:
    """Compact text snapshot of the Briefing strategy for the proposal prompt."""
    from stock_toolkit.game import mark_to_market
    rec = _briefing_strategy_record()
    if rec is None:
        return ("The Briefing strategy has not been created yet — it will be "
                "spun up the first time the user confirms a proposed trade.")
    mtm = mark_to_market(portfolio_id=rec["id"])
    lines = [
        f"Strategy: {mtm['name']}",
        f"  Cash:          {mtm['cash']:>10,.2f}",
        f"  Equity:        {mtm['equity']:>10,.2f}",
        f"  Total value:   {mtm['total']:>10,.2f}",
        f"  Return:        {mtm['total_return_pct']:+.2f}% from inception",
    ]
    if mtm["holdings"]:
        lines.append("  Open positions:")
        for h in mtm["holdings"]:
            lines.append(
                f"    {h['symbol']:<8} qty={h['qty']:>8.4f}  "
                f"value={h['value']:>8.2f}  P/L={h['pnl']:+.2f}"
            )
    else:
        lines.append("  Open positions: none")
    return "\n".join(lines)

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


def _alerts_to_summary(alerts_ctx: dict) -> str:
    """Format the per-symbol indicator context (RSI / Bollinger %B /
    squeeze / change) as the CURRENT INDICATORS block Claude sees.

    Resilient to partial data: a symbol that has only one of RSI / %B
    still renders; a symbol with neither is skipped. ``change`` of
    exactly 0.0% is reported (it is a real value, not "missing")."""
    lines = []
    for sym, ctx in alerts_ctx.items():
        rsi = ctx.get("rsi14")
        pb  = ctx.get("bbands_pct_b")
        sq  = ctx.get("bbands_squeeze")
        chg = ctx.get("change_pct")
        if rsi is None and pb is None:
            continue
        parts = []
        if rsi is not None:
            parts.append(f"RSI={rsi:.0f}")
        if pb is not None:
            parts.append(f"%B={pb:.2f}")
        line = f"  {sym}: " + ", ".join(parts)
        if sq:
            line += " ⚡SQUEEZE"
        if chg is not None:
            line += f", change={chg:+.1f}%"
        lines.append(line)
    return "\n".join(lines)


def _briefing_trade_panel(scores: list) -> None:
    """Inline 'place a paper trade from this briefing' form, rendered
    below Claude's response. Pulls the symbols Claude actually saw
    (from the score table) and routes through stock_toolkit.game into
    the active strategy. Same code path as the Game page's Buy form."""
    from stock_toolkit.game import (
        GameError, buy, get_active_portfolio_id, get_latest_price,
        mark_to_market,
    )

    st.markdown("---")
    st.markdown(heading("paper_trade", "Place a paper trade from this briefing"))

    if get_active_portfolio_id() is None:
        st.info(
            "No active strategy yet. Open the 🎮 Game page in the sidebar "
            "to create one — then come back here."
        )
        return

    mtm = mark_to_market()
    symbols = [r["symbol"] for r in scores] or []
    if not symbols:
        st.caption("No tradeable symbols in this briefing.")
        return

    a, b, c = st.columns([3, 2, 2])
    with a:
        sym = st.selectbox(
            "Symbol", symbols, key="brief_trade_sym",
            help="Limited to the symbols Claude saw in this briefing.",
        )
    with b:
        max_cash = float(mtm["cash"])
        amount   = st.number_input(
            f"Cash (max {max_cash:,.2f})",
            min_value=0.0, max_value=max_cash,
            value=min(500.0, max_cash), step=50.0,
            key="brief_trade_amt",
        )
    with c:
        st.markdown("**Strategy**")
        st.markdown(f"`{mtm['name']}` · {mtm['cash']:,.2f} cash")

    price, as_of = get_latest_price(sym)
    if price is None:
        st.warning(f"No price for `{sym}` — run `stock-collect` first.")
        return
    fill   = price * 1.001
    shares = amount / fill if fill > 0 else 0.0
    st.caption(
        f"Last close `{price:,.2f}` as of {as_of[:10]} · fill `{fill:,.2f}` "
        f"(+10 bps) → ≈ **{shares:.4f}** shares"
    )

    brief_note = st.text_input(
        "Why? (optional note — your thesis for this trade)",
        key="brief_trade_note",
        placeholder="e.g. based on Claude's read of today's briefing",
    )
    if st.button(f"{icon('buy')}  Buy into active strategy", type="primary",
                 key="brief_trade_btn", disabled=(amount <= 0)):
        try:
            out = buy(sym, amount, note=brief_note or None)
            st.success(
                f"Bought **{out['qty']:.4f}** {out['symbol']} into "
                f"`{mtm['name']}` @ {out['fill_price']:,.2f} for "
                f"{out['spent']:,.2f}"
            )
        except GameError as e:
            st.error(str(e))


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
        had_prices = 0   # symbols that had usable price data in the window

        for sym in symbols:
            df = get_prices(sym, date_from_str, date_to_str)
            if df.empty or len(df) < 10:
                continue
            had_prices += 1

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
        return scores, alerts_ctx, had_prices


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
Avoid recommending specific dollar amounts in free-text replies — \
but when the user explicitly asks for paper-trade proposals, you may \
include concrete sizes ONLY inside a fenced TRADE_PROPOSALS_JSON block \
(format provided in that turn's user message). \
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
        # News sentiment toggle — defaults ON if an Alpha Vantage key is
        # configured, OFF otherwise. The fetch is rate-limited by the AV
        # 25/day budget shared with the collector, so we only ever pull
        # for the top scored symbols (see below).
        _av_key_set = bool((_cfg.get("ALPHAVANTAGE_KEY") or "").strip())
        news_col, refresh_col = st.columns([5, 1])
        include_news = news_col.checkbox(
            "Include news sentiment (Alpha Vantage)",
            value=_av_key_set,
            disabled=not _av_key_set,
            help=("Pre-computed per-symbol sentiment from Alpha Vantage's "
                  "NEWS_SENTIMENT endpoint. Free tier is US-biased: "
                  "non-US tickers often return zero articles. Caps at "
                  "the top 5 scored symbols to protect the 25-call/day "
                  "shared budget."
                  if _av_key_set else
                  "Configure ALPHAVANTAGE_KEY in Admin → API Keys to "
                  "enable."),
            key="brief_include_news",
        )
        # Cache flush — useful when a previous fetch hit a throttle
        # (HTTP 200 + 'Note' or 'Information' instead of 'feed') and
        # the empty result got stuck under the 1-hour st.cache_data
        # TTL. One click here forgets the cached news so the next
        # Preview / Generate retries fresh.
        if refresh_col.button("🔄", key="brief_news_refresh",
                              disabled=not _av_key_set,
                              help="Refresh the news cache. Click after "
                                   "fixing an Alpha Vantage throttle or "
                                   "key change to discard stale empties."):
            from stock_toolkit.ui.helpers import get_news_sentiment as _g
            _g.clear()
            st.toast("News cache cleared — next preview will refetch.")

    with bf2:
        st.markdown(
            "<span style='color:#8ba0b4;font-size:0.82rem'>"
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

    def _assemble_prompt(scores, alerts_ctx):
        """Build the user-message text Claude will see. Shared between
        the Generate button (which then calls Claude) and the Preview
        button (which only stashes it for the inspector). Pulls in
        fundamentals + news sentiment behind the existing 1-hour
        st.cache_data so re-running is free until the cache expires."""
        with st.spinner("Fetching fundamentals (P/E, growth)…"):
            funda_table = _fundamentals_to_summary(
                get_fundamentals(tuple(r["symbol"] for r in scores))
            )

        # News sentiment — only the top-5 scored symbols, only if the
        # checkbox is set + key configured. The fetch is cached for 1h,
        # so toggling the checkbox / clicking Preview / clicking
        # Generate within an hour all hit the same cache.
        news_block = ""
        if include_news and scores:
            from stock_toolkit.news import format_for_prompt
            from stock_toolkit.ui.helpers import get_news_sentiment
            top_syms = tuple(r["symbol"] for r in scores[:5])
            with st.spinner("Fetching news sentiment…"):
                sentiment = get_news_sentiment(
                    top_syms, _cfg.get("ALPHAVANTAGE_KEY", ""),
                )
            news_block = format_for_prompt(sentiment) if sentiment else ""

        alert_block = _alerts_to_summary(alerts_ctx)

        return (
            f"Today's watchlist analysis — {brief_horizon} horizon\n"
            f"Budget: {brief_budget} CHF | Broker: {brief_broker}\n"
            f"Date range: {date_from_str} → {date_to_str}\n\n"
            f"SCORES (ranked best→worst):\n{_scores_to_summary(scores)}\n\n"
            + (f"FUNDAMENTALS (valuation snapshot, yfinance):\n{funda_table}\n\n"
               if funda_table else "")
            + "CURRENT INDICATORS:\n" + alert_block + "\n\n"
            + (f"NEWS SENTIMENT (pre-computed by Alpha Vantage; top-5 scored "
               f"symbols only):\n{news_block}\n\n"
               if news_block else "")
            + "Please give me:\n"
            "1. A 2-3 sentence plain-English summary of what stands out\n"
            "2. The top 2 symbols worth watching and why\n"
            "3. Any red flags to avoid right now\n"
            "4. One honest limitation of this analysis I should keep in mind"
        )

    # ── generate + preview buttons ────────────────────────────────────────────
    gen_col, prev_col = st.columns([2, 1])
    do_generate = gen_col.button(
        "📋  Generate today's briefing", type="primary",
        key="brief_generate",
    )
    do_preview = prev_col.button(
        "🔍  Preview prompt",
        key="brief_preview",
        help="Build the prompt locally so you can see what would be sent "
             "to Claude — including the effect of the news-sentiment "
             "toggle. No API call to Claude. The news fetch (if enabled) "
             "is cached for an hour.",
    )

    if do_generate or do_preview:
        with st.spinner("Running 7-step analysis on all symbols…"):
            scores, alerts_ctx, had_prices = _build_context(
                selected_symbols, date_from_str, date_to_str,
                brief_horizon, mc_paths=500
            )
        st.session_state.brief_context = {
            "scores":     scores,
            "alerts_ctx": alerts_ctx,
        }

        if not scores:
            if had_prices:
                # prices exist — the window is just too short for the horizon
                needed = {
                    "week": "~6 weeks", "month": "~3 months",
                    "quarter": "~1 year", "year": "~2 years",
                    "life": "~10 years",
                }.get(brief_horizon, "a longer history")
                st.warning(
                    f"**This is not missing data.** Your prices for "
                    f"{date_from_str} → {date_to_str} are there, but the "
                    f"briefing scores the **{brief_horizon}** horizon, which "
                    f"needs about **{needed}** of history. Widen the sidebar "
                    f"range (try **5Y** or **Max**), then Generate again."
                )
            else:
                st.warning(
                    "No price data for the selected symbols in this range. "
                    "Collect data first (Admin → Collect, or run "
                    "`stock-collect`)."
                )
            st.stop()

        user_msg = _assemble_prompt(scores, alerts_ctx)
        # Always stash so the existing "🔍 Prompt sent to Claude"
        # expander renders it. The Preview path stops here; only
        # Generate proceeds to call Claude.
        st.session_state.brief_prompt = user_msg

        if do_preview:
            st.info(
                "🔍  Preview built. Scroll down to **🔍 Prompt sent to "
                "Claude** to inspect the result — toggle the news "
                "checkbox and click Preview again to compare."
            )
        else:
            # Generate path: reset chat + proposal state, call Claude.
            st.session_state.brief_messages    = []
            st.session_state.brief_skipped_idx = set()
            st.session_state.brief_executed_idx = set()
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
                    # Hide the fenced TRADE_PROPOSALS_JSON block from the
                    # rendered chat — it's machine-readable, not for the user.
                    _, display_text = _parse_trade_proposals(msg["content"])
                    with st.chat_message("assistant",
                                         avatar=icon("chat_avatar")):
                        st.markdown(display_text)
                elif i > 0:
                    with st.chat_message("user", avatar="👤"):
                        st.markdown(msg["content"])

        # ── Claude-driven proposals for the Briefing strategy ─────────────────
        # On-demand: user clicks the button, we hand Claude the current
        # Briefing-strategy state + scoring data and ask for 0-3 structured
        # trade proposals. Each proposal renders as a confirm/skip card.
        if any(m["role"] == "assistant"
               for m in st.session_state.brief_messages):
            from stock_toolkit.game import (
                GameError as _GameError, buy as _buy,
                create_portfolio as _create_portfolio,
                sell as _sell,
            )

            st.markdown("---")
            st.markdown(heading("claude_strategy",
                                "Claude-driven Briefing strategy"))

            rec = _briefing_strategy_record()
            if rec is None:
                st.caption(
                    f"`{BRIEFING_STRATEGY_NAME}` will be created the first "
                    "time you confirm a proposed trade."
                )
            else:
                from stock_toolkit.game import mark_to_market as _mtm
                _bs = _mtm(portfolio_id=rec["id"])
                st.caption(
                    f"`{BRIEFING_STRATEGY_NAME}` · "
                    f"cash {_bs['cash']:,.2f} · "
                    f"equity {_bs['equity']:,.2f} · "
                    f"return {_bs['total_return_pct']:+.2f}%"
                )

            symbols_for_prompt = [r["symbol"] for r in ctx["scores"]]

            if st.button(
                f"{icon('claude_propose')}  Ask Claude to propose trades",
                key="brief_propose_btn",
                help=("Sends a hidden turn with the current Briefing-strategy "
                      "state and asks Claude for 0-3 paper-trade proposals. "
                      "Nothing executes until you confirm each one."),
            ):
                state_blob = _briefing_state_summary()
                propose_msg = (
                    "Please propose 0-3 specific paper-trade actions for the "
                    f"`{BRIEFING_STRATEGY_NAME}` paper-trading strategy based "
                    "on the watchlist analysis already in this conversation.\n\n"
                    "Current strategy state:\n"
                    f"{state_blob}\n\n"
                    f"Watchlist (must pick from these symbols only): "
                    f"{', '.join(symbols_for_prompt)}\n\n"
                    "End your reply with a fenced block in this EXACT format "
                    "(omit the block entirely if you don't see a good trade):\n"
                    f"{TRADE_BLOCK_OPEN}\n"
                    "[\n"
                    "  {\"side\":\"BUY\",\"symbol\":\"AAPL\",\"amount_chf\":200,"
                    "\"reason\":\"...\"},\n"
                    "  {\"side\":\"SELL\",\"symbol\":\"GOOGL\","
                    "\"qty_pct\":100,\"reason\":\"...\"}\n"
                    "]\n"
                    f"{TRADE_BLOCK_CLOSE}\n\n"
                    "Rules:\n"
                    "- BUY uses `amount_chf` (cash to deploy). SELL uses "
                    "`qty_pct` (percent of position to close, 1-100).\n"
                    "- Only SELL symbols actually present in Open positions "
                    "above; never short.\n"
                    "- Keep individual BUY amounts ≤ available cash.\n"
                    "- Each proposal must include a one-sentence `reason`."
                )
                # Fresh proposal turn → forget any prior skip/execute state.
                st.session_state.brief_skipped_idx = set()
                st.session_state.brief_executed_idx = set()
                st.session_state.brief_messages.append(
                    {"role": "user", "content": propose_msg}
                )
                with st.spinner("Claude is drafting proposals…"):
                    reply = _call_claude(
                        st.session_state.brief_messages, SYSTEM_PROMPT
                    )
                st.session_state.brief_messages.append(
                    {"role": "assistant", "content": reply}
                )
                st.rerun()

            # If the most recent assistant message contains a proposal block,
            # render each item as its own confirm/skip card.
            last_assistant = next(
                (m for m in reversed(st.session_state.brief_messages)
                 if m["role"] == "assistant"),
                None,
            )
            proposals = []
            if last_assistant is not None:
                proposals, _ = _parse_trade_proposals(last_assistant["content"])

            skipped = st.session_state.get("brief_skipped_idx", set())
            executed = st.session_state.get("brief_executed_idx", set())
            pending = [
                (i, p) for i, p in enumerate(proposals)
                if i not in skipped and i not in executed
            ]

            if proposals and not pending:
                st.caption("All proposals handled. Click the button again "
                           "to ask Claude for fresh ones.")

            for i, p in pending:
                side   = str(p.get("side", "")).upper()
                symbol = str(p.get("symbol", "")).upper()
                reason = str(p.get("reason", "")).strip()
                if side == "BUY":
                    amt = float(p.get("amount_chf", 0) or 0)
                    descr = f"**BUY** `{symbol}` for **{amt:,.2f} CHF**"
                elif side == "SELL":
                    pct = float(p.get("qty_pct", 0) or 0)
                    descr = f"**SELL** {pct:.0f}% of `{symbol}`"
                else:
                    continue

                with st.container(border=True):
                    st.markdown(f"{descr}  \n_{reason}_" if reason else descr)
                    confirm_col, skip_col = st.columns([1, 1])

                    if confirm_col.button(
                        "✅  Add to Briefing strategy",
                        key=f"brief_prop_confirm_{i}",
                        type="primary",
                    ):
                        rec_now = _briefing_strategy_record()
                        if rec_now is None:
                            # First confirmation creates the strategy. Use
                            # the "Available budget (CHF)" field above as
                            # a sane default starting cash (×20 so there's
                            # room for several trades).
                            start_cash = max(1000.0,
                                             float(brief_budget) * 20)
                            try:
                                rec_now = _create_portfolio(
                                    BRIEFING_STRATEGY_NAME,
                                    starting_cash=start_cash,
                                    activate=False,
                                )
                            except _GameError as e:
                                st.error(f"Couldn't create strategy: {e}")
                                continue
                        try:
                            # Archive Claude's reason as the trade note so
                            # future-you can read why this trade was made.
                            note = f"[Claude] {reason}" if reason else None
                            if side == "BUY":
                                _buy(symbol, amt,
                                     portfolio_id=rec_now["id"], note=note)
                                st.success(
                                    f"Bought {symbol} for {amt:,.2f} CHF "
                                    f"into `{BRIEFING_STRATEGY_NAME}`."
                                )
                            else:
                                from stock_toolkit.game import (
                                    get_positions as _gp,
                                )
                                pos = _gp(portfolio_id=rec_now["id"]).get(symbol)
                                if not pos:
                                    raise _GameError(
                                        f"No open {symbol} position to sell."
                                    )
                                qty = pos["qty"] * (pct / 100.0)
                                _sell(symbol, qty,
                                      portfolio_id=rec_now["id"], note=note)
                                st.success(
                                    f"Sold {qty:.4f} {symbol} "
                                    f"({pct:.0f}% of position) from "
                                    f"`{BRIEFING_STRATEGY_NAME}`."
                                )
                            executed_set = set(
                                st.session_state.get("brief_executed_idx",
                                                     set())
                            )
                            executed_set.add(i)
                            st.session_state.brief_executed_idx = executed_set
                            st.rerun()
                        except _GameError as e:
                            st.error(str(e))

                    if skip_col.button(
                        "✕  Skip",
                        key=f"brief_prop_skip_{i}",
                    ):
                        skipped_set = set(
                            st.session_state.get("brief_skipped_idx", set())
                        )
                        skipped_set.add(i)
                        st.session_state.brief_skipped_idx = skipped_set
                        st.rerun()

        # ── Act on it: paper-trade into the active Game strategy ──────────────
        # Renders only after Claude has actually responded — otherwise the
        # user has nothing to act on yet.
        if any(m["role"] == "assistant"
               for m in st.session_state.brief_messages):
            _briefing_trade_panel(ctx["scores"])

        # ── 7-step analysis results ───────────────────────────────────────────
        with st.expander(f"{icon('seven_step')}  7-step analysis — full metrics",
                         expanded=False):
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
                    "<span style='color:#8ba0b4;font-size:0.78rem'>"
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
                st.session_state.brief_skipped_idx  = set()
                st.session_state.brief_executed_idx = set()
                st.rerun()
        with col_cap:
            st.caption(
                "⚠️  Educational analysis only — not financial advice."
            )


