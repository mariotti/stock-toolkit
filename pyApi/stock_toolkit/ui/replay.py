"""
⏪ Replay page — time-travel betting game on historical bars.

The engine (load panel, indicators, bet resolution) lives in
stock_toolkit.replay; this module is render-only. A session is a dict
in st.session_state — nothing is persisted on purpose: rewind, play,
close the tab, it never happened.
"""

import random

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from stock_toolkit import replay as rp
from stock_toolkit.ui.charts import CHART_LAYOUT
from stock_toolkit.ui.helpers import get_all_symbols
from stock_toolkit.ui.icons import heading, icon


def _money(v: float) -> str:
    return f"{v:,.2f}"


def _reset():
    for k in ("replay_state", "replay_panel"):
        st.session_state.pop(k, None)


def _day_label(state, i: int) -> str:
    """What to call position i: the real date, or 'Day k' when blind."""
    if state["blind"]:
        return f"Day {i - state['start_i'] + 1}"
    return str(state["panel_index"][i])


def _price_fig(closes: pd.Series, blind: bool) -> go.Figure:
    x = list(range(-len(closes) + 1, 1)) if blind else list(closes.index)
    fig = go.Figure(go.Scatter(
        x=x, y=list(closes.values), mode="lines", name="close",
        line=dict(color="#38bdf8", width=1.5),
        hovertemplate="%{y:.2f}<extra></extra>",
    ))
    layout = dict(CHART_LAYOUT, height=280,
                  margin=dict(l=48, r=16, t=16, b=32))
    if blind:
        layout["xaxis"] = dict(CHART_LAYOUT["xaxis"],
                               title="bars ago (dates hidden)")
    fig.update_layout(**layout)
    return fig


def _indicator_caption(ind: dict) -> str:
    parts = []
    if ind["chg_pct"] is not None:
        parts.append(f"today {ind['chg_pct']:+.1f}%")
    if ind["rsi14"] is not None:
        parts.append(f"RSI14 {ind['rsi14']:.0f}")
    if ind["pct_b"] is not None:
        parts.append(f"%B {ind['pct_b']:.2f}")
    if ind["sma20"] is not None:
        parts.append(f"SMA20 {ind['sma20']:,.2f}")
    if ind["sma50"] is not None:
        parts.append(f"SMA50 {ind['sma50']:,.2f}")
    return " · ".join(parts) if parts else "not enough history yet"


# ─────────────────────────────────────────────────────────────────────
#  Setup
# ─────────────────────────────────────────────────────────────────────

def _setup_form():
    st.markdown(heading("replay_setup", "New replay session"))
    st.caption(
        "Rewind to a past trading day. You see only what was knowable "
        "then — chart and indicators up to that day — and bet on the "
        "NEXT bar. Reveal, learn, advance. Nothing is saved."
    )
    syms_all = get_all_symbols()
    if not syms_all:
        st.warning("No price data on disk — collect some first "
                   "(Admin → Collect, or `stock-collect`).")
        return

    mode = st.radio(
        "Mode", ["single", "portfolio"],
        format_func=lambda m: (
            "🎯 Single symbol — call the next bar Higher/Lower"
            if m == "single" else
            "🧺 Portfolio — allocate percentages for the next bar"),
        key="replay_mode_choice", horizontal=False,
    )
    if mode == "single":
        chosen = [st.selectbox("Symbol", syms_all, key="replay_setup_sym")]
    else:
        chosen = st.multiselect(
            "Symbols (2-8 work best)", syms_all,
            default=syms_all[:min(4, len(syms_all))],
            key="replay_setup_syms")

    c1, c2, c3 = st.columns(3)
    random_start = c1.checkbox(
        "🎲 Random start date", value=True, key="replay_random",
        help="Drops you somewhere in the playable history.")
    blind = c2.checkbox(
        "🙈 Blind mode", value=False, key="replay_blind",
        help="Hides all dates so you can't recognise the era — the "
             "purest test of reading the chart, not your memory.")
    stake = c3.number_input(
        "Stake / starting pot (CHF)", min_value=100, max_value=1_000_000,
        value=1_000, step=100, key="replay_stake",
        help="Single mode: amount virtually staked on every call. "
             "Portfolio mode: the pot your allocations compound.")
    start_date = None
    if not random_start:
        start_date = st.date_input("Start date", key="replay_start_date")

    if st.button(f"{icon('replay_start')}  Start the session",
                 type="primary", key="replay_start_btn"):
        if not chosen:
            st.error("Pick at least one symbol.")
            return
        with st.spinner("Loading history…"):
            panel = rp.load_panel(chosen)
        first, last = rp.playable_positions(panel)
        if panel.empty or last < first:
            st.error(
                f"Not enough history: a session needs at least "
                f"{rp.MIN_HISTORY} bars of warm-up plus one day to bet "
                "on. Bootstrap more history (`stock-bootstrap`) or pick "
                "other symbols.")
            return
        if random_start or blind:
            i = random.randint(first, last)
        else:
            pos = panel.index.searchsorted(start_date)
            i = min(max(int(pos), first), last)
        st.session_state.replay_panel = panel
        st.session_state.replay_state = {
            "mode": mode, "symbols": list(panel.columns),
            "start_i": i, "i": i, "blind": bool(blind),
            "stake": float(stake), "pot": float(stake),
            "bench": float(stake), "rounds": [], "last": None,
            "panel_index": list(panel.index),
        }
        st.rerun()


# ─────────────────────────────────────────────────────────────────────
#  Play
# ─────────────────────────────────────────────────────────────────────

def _reveal_last(state):
    """Show what the previous bet did — the feedback beat of the game."""
    last = state["last"]
    if not last:
        return
    if last.get("skipped"):
        st.info(f"⏭ Skipped {last['label']} — no bet, no lesson.")
        return
    if state["mode"] == "single":
        arrow = "📈" if last["ret_pct"] > 0 else (
            "📉" if last["ret_pct"] < 0 else "➡️")
        msg = (f"{arrow} {last['symbol']} moved **{last['ret_pct']:+.2f}%** "
               f"→ you called **{last['direction'].upper()}** and "
               f"{'won' if last['outcome'] == 'hit' else 'lost' if last['outcome'] == 'miss' else 'pushed'} "
               f"**{_money(last['gain'])}**.")
        (st.success if last["outcome"] == "hit"
         else st.error if last["outcome"] == "miss" else st.info)(msg)
    else:
        per = "  ·  ".join(f"{s} {r:+.2f}%"
                           for s, r in last["per_symbol"].items())
        msg = (f"Your allocation returned **{last['ret_pct']:+.2f}%** "
               f"(equal-weight basket: {last['bench_ret']:+.2f}%). {per}")
        (st.success if last["ret_pct"] >= last["bench_ret"]
         else st.warning)(msg)


def _play(state):
    panel = st.session_state.replay_panel
    i = state["i"]
    game_over = i + 1 >= len(panel)

    # header metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Now at", _day_label(state, i))
    m2.metric("Rounds", len(state["rounds"]))
    if state["mode"] == "single":
        s = rp.single_summary(state["rounds"])
        m3.metric("Hit rate", f"{s['hit_rate'] * 100:.0f}%",
                  delta=f"{s['hits']} / {s['misses']} W/L")
        m4.metric("Total P/L", _money(s["total_gain"]))
    else:
        m3.metric("Pot", _money(state["pot"]),
                  delta=f"{(state['pot'] / state['stake'] - 1) * 100:+.1f}%")
        m4.metric("Eq-weight benchmark", _money(state["bench"]),
                  delta=f"{(state['bench'] / state['stake'] - 1) * 100:+.1f}%")

    _reveal_last(state)

    if game_over:
        st.warning("🏁 End of recorded history — session over.")
        _summary(state)
        return

    # per-symbol chart + indicators (single: one; portfolio: tabs)
    st.markdown(heading("replay_chart", "What you know today"))
    show_syms = ([state["symbols"][0]] if state["mode"] == "single"
                 else state["symbols"])
    tabs = st.tabs(show_syms) if len(show_syms) > 1 else [st.container()]
    for tab, sym in zip(tabs, show_syms):
        with tab:
            win = rp.window(panel, sym, i)
            if win.empty:
                st.caption(f"{sym}: not trading yet at this date.")
                continue
            st.plotly_chart(_price_fig(win, state["blind"]),
                            width="stretch")
            ind = rp.indicators(win)
            st.caption(f"**{sym}** close {ind['price']:,.2f} · "
                       + _indicator_caption(ind))

    # bet controls
    st.markdown(heading("replay_bet", "Your bet for the next bar"))
    if state["mode"] == "single":
        sym = state["symbols"][0]
        b1, b2, b3 = st.columns(3)
        bet = None
        if b1.button("📈  Higher", key="replay_bet_up", type="primary"):
            bet = "up"
        if b2.button("📉  Lower", key="replay_bet_down", type="primary"):
            bet = "down"
        if b3.button("⏭  Skip day", key="replay_bet_skip"):
            state["last"] = {"skipped": True,
                             "label": _day_label(state, i + 1)}
            state["i"] = i + 1
            st.rerun()
        if bet:
            rnd = rp.resolve_single(panel, sym, i, bet, state["stake"])
            if rnd is not None:
                state["rounds"].append(rnd)
                state["last"] = rnd
                state["i"] = i + 1
                st.rerun()
    else:
        st.caption("Percent of the pot per symbol — the rest sits in "
                   "cash at 0%.")
        cols = st.columns(min(4, len(state["symbols"])))
        weights = {}
        for k, sym in enumerate(state["symbols"]):
            weights[sym] = cols[k % len(cols)].number_input(
                sym, min_value=0, max_value=100,
                value=0, step=5, key=f"replay_w_{sym}")
        total_w = sum(weights.values())
        if total_w > 100:
            st.error(f"Allocated {total_w}% — that's more than the pot.")
        elif st.button(f"{icon('replay_place')}  Place allocation "
                       "and advance", type="primary",
                       key="replay_bet_alloc"):
            rnd = rp.resolve_portfolio(panel, weights, i)
            if rnd is not None:
                rnd["bench_ret"] = rp.equal_weight_return(panel, i)
                state["pot"] *= 1 + rnd["ret_pct"] / 100.0
                state["bench"] *= 1 + rnd["bench_ret"] / 100.0
                state["rounds"].append(rnd)
                state["last"] = rnd
                state["i"] = i + 1
                st.rerun()

    st.markdown("---")
    if st.button("🏁  End session", key="replay_end_btn"):
        state["ended"] = True
        st.rerun()


# ─────────────────────────────────────────────────────────────────────
#  Summary
# ─────────────────────────────────────────────────────────────────────

def _summary(state):
    st.markdown(heading("replay_summary", "Session summary"))
    if state["blind"]:
        # The big reveal: where (when) were you playing?
        st.info(f"🗓 You were playing from "
                f"**{state['panel_index'][state['start_i']]}** to "
                f"**{state['panel_index'][state['i']]}**.")
    if state["mode"] == "single":
        s = rp.single_summary(state["rounds"])
        st.markdown(
            f"- **{s['rounds']}** rounds — {s['hits']} hits, "
            f"{s['misses']} misses, {s['pushes']} pushes "
            f"(hit rate **{s['hit_rate'] * 100:.0f}%**)\n"
            f"- Total P/L at {_money(state['stake'])} per call: "
            f"**{_money(s['total_gain'])}**\n"
            f"- A coin flip averages 50% — anything above it on >20 "
            f"rounds means you read something real (or got lucky; "
            f"play again to find out which).")
    else:
        edge = (state["pot"] / state["bench"] - 1) * 100
        st.markdown(
            f"- Pot: **{_money(state['pot'])}** "
            f"({(state['pot'] / state['stake'] - 1) * 100:+.1f}%)\n"
            f"- Equal-weight benchmark: **{_money(state['bench'])}** "
            f"({(state['bench'] / state['stake'] - 1) * 100:+.1f}%)\n"
            f"- Your allocation edge vs the lazy basket: "
            f"**{edge:+.2f}%** — beating it consistently is the whole "
            f"game.")
    if st.button("🔄  Play again", key="replay_again_btn", type="primary"):
        _reset()
        st.rerun()


# ─────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────

def render():
    from stock_toolkit.ui.theme import setup_page
    setup_page("Stock Toolkit — Replay")
    st.title(f"{icon('page.replay')} Replay")
    st.caption(
        "Time-travel paper betting: the market already knows what "
        "happened next — do you? Ephemeral by design: nothing is "
        "stored, no portfolio is touched."
    )
    state = st.session_state.get("replay_state")
    if state is None:
        _setup_form()
    elif state.get("ended"):
        _summary(state)
    else:
        _play(state)
