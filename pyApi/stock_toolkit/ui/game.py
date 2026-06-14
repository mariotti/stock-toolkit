"""
Game page — paper-trading dashboard.

Reads/writes via stock_toolkit.game (the pure logic + portfolio.db
layer). Render only; no analytics live here.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from stock_toolkit.common import CONFIG_PATH, load_config
from stock_toolkit.game import (
    GameError, SLIPPAGE_BPS,
    archive_portfolio, benchmark_history, buy, create_portfolio,
    delete_portfolio, get_latest_price, init_portfolio, list_portfolios,
    mark_to_market, rename_portfolio, reset_portfolio, sell,
    set_active_portfolio, value_history,
)


def _money(v: float) -> str:
    return f"{v:,.2f}"


def _pct(v: float) -> str:
    return f"{v:+.2f}%"


def render():
    st.set_page_config(page_title="Stock Toolkit — Game",
                       page_icon="🎮", layout="wide")
    st.title("🎮 Game")
    st.caption(
        "Paper-trading portfolio. No real money, no API orders — fills "
        "use the latest close in your collected data with 0.1% slippage. "
        "Use it to test what the briefing suggests, then check back in a "
        "day, a week, or a month."
    )

    cfg            = load_config(CONFIG_PATH)
    config_symbols = {s.strip().upper()
                      for s in cfg.get("SYMBOLS", "").split(",")
                      if s.strip()}
    # Tradeable universe = anything with daily bars in any discoverable DB.
    # That's a superset of config.env SYMBOLS — covers anything you've ever
    # collected (manually, via bootstrap, or that got added by the live
    # collector's _symbols_from_db logic).
    try:
        from stock_toolkit.score import list_all_symbols
        db_symbols = set(list_all_symbols())
    except Exception:
        db_symbols = set()
    # Watchlist symbols first, then the rest — so the most-actively-tracked
    # show at the top of the dropdown.
    in_watchlist  = sorted(config_symbols & db_symbols)
    extra_in_db   = sorted(db_symbols - config_symbols)
    watchlist     = in_watchlist + extra_in_db

    # Ensure at least one portfolio exists (no-op once initialised)
    init_portfolio()
    portfolios = list_portfolios()

    # ─────────────────────────────────────────────────────────────────────
    #  Strategy selector + "+ New strategy" expander
    # ─────────────────────────────────────────────────────────────────────
    sel_col, new_col = st.columns([3, 2])
    with sel_col:
        names      = [p["name"] for p in portfolios]
        ids        = [p["id"]   for p in portfolios]
        active_mtm = mark_to_market()
        try:
            cur_idx = ids.index(active_mtm["id"])
        except (ValueError, KeyError):
            cur_idx = 0
        chosen = st.selectbox(
            "Active strategy", names, index=cur_idx, key="game_pf_select",
            help=("Each strategy has its own cash, positions, and trade "
                  "history. Switch any time — they all keep running."),
        )
        chosen_id = ids[names.index(chosen)]
        if chosen_id != active_mtm["id"]:
            set_active_portfolio(chosen_id)
            st.rerun()

    with new_col:
        with st.expander("➕  New strategy"):
            new_name = st.text_input("Name", key="game_new_name",
                                     placeholder="e.g. Aggressive growth")
            new_cash = st.number_input(
                "Starting cash", min_value=100.0, max_value=10_000_000.0,
                value=10_000.0, step=1000.0, key="game_new_cash",
            )
            if st.button("Create & activate", type="primary",
                         key="game_new_btn", disabled=not new_name.strip()):
                try:
                    create_portfolio(new_name, starting_cash=float(new_cash))
                    st.success(f"Created strategy {new_name!r}.")
                    st.rerun()
                except GameError as e:
                    st.error(str(e))

    mtm = mark_to_market()

    # ─────────────────────────────────────────────────────────────────────
    #  Header — value, equity, cash, total return
    # ─────────────────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total value", _money(mtm["total"]),
              delta=f"{_money(mtm['total_pnl'])} ({_pct(mtm['total_return_pct'])})")
    c2.metric("Cash", _money(mtm["cash"]))
    c3.metric("Equity", _money(mtm["equity"]))
    c4.metric("Starting cash", _money(mtm["starting_cash"]))

    st.caption(
        f"Started {mtm['created_at'][:10]} · "
        f"Last reset {mtm['last_reset_at'][:10]} · "
        f"Slippage: {SLIPPAGE_BPS} bps each side"
    )
    st.markdown("---")

    # ─────────────────────────────────────────────────────────────────────
    #  Open positions
    # ─────────────────────────────────────────────────────────────────────
    st.markdown("### 📈  Open positions")
    if mtm["holdings"]:
        df = pd.DataFrame(mtm["holdings"])
        df["weight"] = df["value"] / mtm["total"] * 100
        df_display = pd.DataFrame({
            "Symbol":     df["symbol"],
            "Shares":     df["qty"].map(lambda v: f"{v:.4f}"),
            "Avg cost":   df["avg_cost"].map(_money),
            "Last price": df["price"].map(_money),
            "Value":      df["value"].map(_money),
            "P/L":        df["pnl"].map(_money),
            "P/L %":      df["pnl_pct"].map(_pct),
            "Weight":     df["weight"].map(lambda v: f"{v:.1f}%"),
            "As of":      df["as_of"].map(lambda v: v[:10] if v else "—"),
        })
        st.dataframe(df_display, width="stretch", hide_index=True)
    else:
        st.info("No open positions yet — use the Buy form below to get started.")

    st.markdown("---")

    # ─────────────────────────────────────────────────────────────────────
    #  Buy / Sell forms
    # ─────────────────────────────────────────────────────────────────────
    buy_col, sell_col = st.columns(2)

    with buy_col:
        st.markdown("### 💰  Buy")
        if not watchlist:
            st.warning("No symbols with data yet — run `stock-collect` or "
                       "`stock-bootstrap` first.")
        else:
            sym_buy = st.selectbox(
                "Symbol", watchlist, key="game_buy_sym",
                help=(f"{len(in_watchlist)} from your watchlist · "
                      f"{len(extra_in_db)} more with collected data"),
            )
            price, as_of = get_latest_price(sym_buy)
            if price is None:
                st.warning(f"No price for `{sym_buy}` — run `stock-collect` "
                           "or `stock-bootstrap` first.")
            else:
                fill_buy = price * (1 + SLIPPAGE_BPS / 10000.0)
                max_cash = float(mtm["cash"])
                amount   = st.number_input(
                    f"Cash to spend (max {_money(max_cash)})",
                    min_value=0.0, max_value=max_cash,
                    value=min(500.0, max_cash), step=50.0,
                    key="game_buy_amt",
                )
                if amount > 0 and fill_buy > 0:
                    shares = amount / fill_buy
                    st.caption(
                        f"Last close: `{_money(price)}` as of {as_of[:10]} · "
                        f"fill `{_money(fill_buy)}` (+{SLIPPAGE_BPS} bps) → "
                        f"≈ **{shares:.4f}** shares"
                    )
                if st.button("▶  Buy", type="primary", key="game_buy_btn",
                             disabled=(amount <= 0)):
                    try:
                        out = buy(sym_buy, amount)
                        st.success(
                            f"Bought **{out['qty']:.4f}** {out['symbol']} "
                            f"@ {_money(out['fill_price'])} for "
                            f"{_money(out['spent'])}")
                        st.rerun()
                    except GameError as e:
                        st.error(str(e))

    with sell_col:
        st.markdown("### 💸  Sell")
        open_syms = [h["symbol"] for h in mtm["holdings"]]
        if not open_syms:
            st.info("No open positions to sell.")
        else:
            sym_sell = st.selectbox("Position", open_syms, key="game_sell_sym")
            pos      = next(h for h in mtm["holdings"]
                            if h["symbol"] == sym_sell)
            max_qty  = float(pos["qty"])
            qty_sell = st.number_input(
                f"Shares to sell (max {max_qty:.4f})",
                min_value=0.0, max_value=max_qty,
                value=max_qty, step=max_qty / 10 if max_qty > 0 else 0.1,
                format="%.4f",
                key="game_sell_qty",
            )
            if qty_sell > 0:
                fill_sell = pos["price"] * (1 - SLIPPAGE_BPS / 10000.0)
                proceeds  = qty_sell * fill_sell
                st.caption(
                    f"Last close: `{_money(pos['price'])}` · fill "
                    f"`{_money(fill_sell)}` (−{SLIPPAGE_BPS} bps) → "
                    f"proceeds **{_money(proceeds)}**"
                )
            if st.button("▶  Sell", type="primary", key="game_sell_btn",
                         disabled=(qty_sell <= 0)):
                try:
                    out = sell(sym_sell, qty_sell)
                    st.success(
                        f"Sold **{out['qty']:.4f}** {out['symbol']} "
                        f"@ {_money(out['fill_price'])} for "
                        f"{_money(out['proceeds'])}")
                    st.rerun()
                except GameError as e:
                    st.error(str(e))

    st.markdown("---")

    # ─────────────────────────────────────────────────────────────────────
    #  Portfolio value over time
    # ─────────────────────────────────────────────────────────────────────
    st.markdown("### 📊  Portfolio value over time")
    history = value_history()
    if not history:
        st.info("No history yet — trade something to start the curve.")
    else:
        h_df = pd.DataFrame(history)
        h_df["date"] = pd.to_datetime(h_df["date"])
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=h_df["date"], y=h_df["total"], mode="lines",
            name=mtm["name"], line=dict(color="#38bdf8", width=2),
        ))

        # Equal-weight buy-and-hold benchmark over the same period.
        # Honest scorecard: if your strategy can't beat just sitting on
        # the watchlist equally weighted, the strategy isn't adding value.
        from datetime import date as _date
        bench_syms = sorted(config_symbols) if config_symbols else watchlist
        if bench_syms:
            bench = benchmark_history(
                bench_syms,
                starting_cash=mtm["starting_cash"],
                start_date=_date.fromisoformat(mtm["created_at"][:10]),
            )
            if bench:
                b_df = pd.DataFrame(bench)
                b_df["date"] = pd.to_datetime(b_df["date"])
                fig.add_trace(go.Scatter(
                    x=b_df["date"], y=b_df["value"], mode="lines",
                    name=f"Buy-and-hold watchlist ({len(bench_syms)} eq-wt)",
                    line=dict(color="#a78bfa", width=1.5, dash="dot"),
                ))

        fig.add_hline(
            y=mtm["starting_cash"], line_dash="dot", line_color="#8ba0b4",
            annotation_text=f"Starting cash {_money(mtm['starting_cash'])}",
            annotation_position="bottom right",
            annotation_font_color="#8ba0b4",
        )
        fig.update_layout(
            paper_bgcolor="#0e1922", plot_bgcolor="#0e1922",
            font=dict(family="IBM Plex Mono", size=11, color="#8ba0b4"),
            margin=dict(l=48, r=16, t=16, b=36), height=320,
            legend=dict(bgcolor="rgba(0,0,0,0)",
                        font=dict(size=10, color="#c8d8e8")),
            xaxis=dict(gridcolor="#2d4258", linecolor="#2d4258"),
            yaxis=dict(gridcolor="#2d4258", linecolor="#2d4258",
                       tickformat=",.0f"),
        )
        st.plotly_chart(fig, width="stretch")

    # ─────────────────────────────────────────────────────────────────────
    #  Strategy comparison (overlay value history of every active portfolio)
    # ─────────────────────────────────────────────────────────────────────
    if len(portfolios) > 1:
        with st.expander(
            f"📈  Compare strategies ({len(portfolios)})", expanded=False
        ):
            palette = [
                "#38bdf8", "#facc15", "#34d399", "#f472b6",
                "#fb923c", "#a78bfa", "#22d3ee", "#fde047",
            ]
            cmp_fig = go.Figure()
            any_data = False
            for i, p in enumerate(portfolios):
                hist = value_history(portfolio_id=p["id"])
                if not hist:
                    continue
                any_data = True
                c_df = pd.DataFrame(hist)
                c_df["date"] = pd.to_datetime(c_df["date"])
                is_active = (p["id"] == mtm["id"])
                cmp_fig.add_trace(go.Scatter(
                    x=c_df["date"], y=c_df["total"], mode="lines",
                    name=p["name"] + (" (active)" if is_active else ""),
                    line=dict(
                        color=palette[i % len(palette)],
                        width=2.5 if is_active else 1.5,
                    ),
                ))
            if not any_data:
                st.caption("No trade history on any strategy yet.")
            else:
                cmp_fig.update_layout(
                    paper_bgcolor="#0e1922", plot_bgcolor="#0e1922",
                    font=dict(family="IBM Plex Mono", size=11, color="#8ba0b4"),
                    margin=dict(l=48, r=16, t=16, b=36), height=320,
                    legend=dict(bgcolor="rgba(0,0,0,0)",
                                font=dict(size=10, color="#c8d8e8")),
                    xaxis=dict(gridcolor="#2d4258", linecolor="#2d4258"),
                    yaxis=dict(gridcolor="#2d4258", linecolor="#2d4258",
                               tickformat=",.0f"),
                )
                st.plotly_chart(cmp_fig, width="stretch")
                st.caption(
                    "Absolute values — strategies with different starting "
                    "cash will start at different y-positions."
                )

    st.markdown("---")

    # ─────────────────────────────────────────────────────────────────────
    #  Trade history
    # ─────────────────────────────────────────────────────────────────────
    st.markdown("### 📜  Trade history")
    from stock_toolkit.game import get_trades
    trades = get_trades()
    if not trades:
        st.info("No trades yet.")
    else:
        t_df = pd.DataFrame(trades)
        t_display = pd.DataFrame({
            "When":       t_df["timestamp"].map(lambda v: v[:19].replace("T", " ")),
            "Side":       t_df["side"].map(str.upper),
            "Symbol":     t_df["symbol"],
            "Shares":     t_df["qty"].map(lambda v: f"{v:.4f}"),
            "Close":      t_df["price"].map(_money),
            "Fill":       t_df["fill_price"].map(_money),
            "Cash Δ":     t_df["cash_delta"].map(_money),
        })
        st.dataframe(
            t_display.iloc[::-1].reset_index(drop=True),   # newest first
            width="stretch", hide_index=True,
        )

    st.markdown("---")

    # ─────────────────────────────────────────────────────────────────────
    #  Settings — current strategy: rename / reset / archive / delete
    # ─────────────────────────────────────────────────────────────────────
    with st.expander(f"⚙️  Settings — strategy {mtm['name']!r}"):
        st.markdown("**Rename**")
        ren = st.text_input("New name", value=mtm["name"],
                            key="game_rename_input")
        if st.button("Rename", key="game_rename_btn",
                     disabled=(ren.strip() == mtm["name"] or not ren.strip())):
            try:
                rename_portfolio(mtm["id"], ren.strip())
                st.success(f"Renamed to {ren.strip()!r}.")
                st.rerun()
            except GameError as e:
                st.error(str(e))

        st.markdown("---")
        st.markdown("**Reset (wipe positions, keep the strategy)**")
        new_cash = st.number_input(
            "Starting cash for the reset",
            min_value=100.0, max_value=10_000_000.0,
            value=float(mtm["starting_cash"]), step=1000.0,
            key="game_reset_cash",
        )
        confirm_reset = st.checkbox(
            "I understand this wipes ALL positions and trade history "
            f"for {mtm['name']!r}.",
            key="game_reset_confirm")
        if st.button("🗑  Reset this strategy", disabled=not confirm_reset,
                     key="game_reset_btn"):
            reset_portfolio(starting_cash=float(new_cash))
            st.success(f"Reset {mtm['name']!r}. "
                       f"Starting cash: {_money(new_cash)}")
            st.rerun()

        st.markdown("---")
        st.markdown("**Archive (hide from selector, keep history)**")
        if st.button("📦  Archive this strategy", key="game_arch_btn"):
            archive_portfolio(mtm["id"])
            st.success(f"Archived {mtm['name']!r}.")
            st.rerun()

        st.markdown("---")
        st.markdown("**Delete (irreversible — wipes the strategy "
                    "and its trades)**")
        confirm_del = st.checkbox(
            f"I want to permanently delete {mtm['name']!r}.",
            key="game_del_confirm")
        if st.button("❌  Delete this strategy",
                     disabled=not confirm_del, key="game_del_btn"):
            delete_portfolio(mtm["id"])
            st.success(f"Deleted {mtm['name']!r}.")
            st.rerun()

    # Footer
    st.markdown("---")
    st.caption(
        "Educational tool — not financial advice. Fills use the most "
        "recent close in your collected data; real markets move."
    )


if __name__ == "__main__":
    render()
