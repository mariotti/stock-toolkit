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
    delete_portfolio, get_audit_log, get_latest_price, init_portfolio,
    list_portfolios, mark_to_market, rename_portfolio, reset_portfolio,
    sell, set_active_portfolio, value_history,
)
from stock_toolkit.ui.icons import heading, icon
from stock_toolkit.ui.theme import (
    CHART_AXIS, CHART_BG, CHART_FONT, CHART_GRID, CHART_INK, CHART_MUTED,
)


def _money(v: float) -> str:
    return f"{v:,.2f}"


def _pct(v: float) -> str:
    return f"{v:+.2f}%"


def render():
    from stock_toolkit.ui.theme import setup_page
    setup_page("Stock Toolkit — Game")
    st.title(f"{icon('page.game')} Game")
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
        ids        = [p["id"] for p in portfolios]
        active_mtm = mark_to_market()
        # Per-strategy return % rendered inline so the user can pick the
        # winner without opening the compare expander.
        mtms = {p["id"]: mark_to_market(portfolio_id=p["id"])
                for p in portfolios}
        labels = [
            f"{p['name']} ({mtms[p['id']]['total_return_pct']:+.1f}%)"
            for p in portfolios
        ]
        try:
            cur_idx = ids.index(active_mtm["id"])
        except (ValueError, KeyError):
            cur_idx = 0
        chosen = st.selectbox(
            "Active strategy", labels, index=cur_idx, key="game_pf_select",
            help=("Each strategy has its own cash, positions, and trade "
                  "history. Switch any time — they all keep running."),
        )
        chosen_id = ids[labels.index(chosen)]
        if chosen_id != active_mtm["id"]:
            set_active_portfolio(chosen_id)
            st.rerun()

    with new_col:
        with st.expander(f"{icon('new_strategy')}  New strategy"):
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

    # Risk-adjusted return row — meaningful even before any close, since
    # mark-to-market moves daily on the open positions.
    from stock_toolkit.game import risk_stats
    rs = risk_stats()
    if rs.get("n_days", 0) >= 2:
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("CAGR",    f"{rs['cagr']:+.2f}%",
                  help="Compound annual growth rate, annualised from the "
                       "daily mark-to-market curve.")
        r2.metric("Sharpe",  f"{rs['sharpe']:.2f}",
                  help="Annualised Sharpe (risk-free=0). "
                       ">1 decent, >2 good, >3 exceptional.")
        r3.metric("Sortino", f"{rs['sortino']:.2f}",
                  help="Annualised Sortino — like Sharpe but only "
                       "penalises downside volatility. Usually higher "
                       "than Sharpe when up-days are bigger than down-days.")
        r4.metric("Max DD",  f"{rs['max_dd']:.2f}%",
                  help="Largest peak-to-trough decline of total value, "
                       "ever. Risk-of-ruin proxy.")

    st.caption(
        f"Started {mtm['created_at'][:10]} · "
        f"Last reset {mtm['last_reset_at'][:10]} · "
        f"Slippage: {SLIPPAGE_BPS} bps each side"
    )
    st.markdown("---")

    # ─────────────────────────────────────────────────────────────────────
    #  Open positions
    # ─────────────────────────────────────────────────────────────────────
    st.markdown(heading("positions", "Open positions"))
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
        # One-click "Sell all" per position — faster than picking the
        # symbol + dialling shares in the Sell form below.
        st.caption("Close a position in one click:")
        positions = mtm["holdings"]
        cols_per_row = 4
        for chunk_start in range(0, len(positions), cols_per_row):
            row_positions = positions[chunk_start:chunk_start + cols_per_row]
            cols = st.columns(cols_per_row)
            for col, pos in zip(cols, row_positions):
                btn_label = f"{icon('sell_all')} Sell all {pos['symbol']}"
                if col.button(btn_label, key=f"game_sellall_{pos['symbol']}"):
                    try:
                        sell(pos["symbol"])
                        st.success(
                            f"Sold {pos['qty']:.4f} {pos['symbol']} "
                            f"@ ~{_money(pos['price'])}."
                        )
                        st.rerun()
                    except GameError as e:
                        st.error(str(e))

        # ── Diversification check ────────────────────────────────────────
        # Two simple "are you over-concentrated?" signals computed from
        # the holdings table + last 60 days of closes.
        if len(positions) >= 2:
            top_w = float(df["weight"].max())
            try:
                from datetime import date as _ddt, timedelta as _td
                from stock_toolkit.ui.helpers import get_prices as _gp
                end = _ddt.today().isoformat()
                start = (_ddt.today() - _td(days=90)).isoformat()
                closes = {}
                for h in positions:
                    pdf = _gp(h["symbol"], start, end)
                    if not pdf.empty:
                        closes[h["symbol"]] = (
                            pdf.set_index("timestamp")["close"].astype(float)
                        )
                if len(closes) >= 2:
                    cdf = pd.DataFrame(closes).pct_change().dropna()
                    corr = cdf.corr()
                    # Mean of the upper triangle (excluding the diagonal).
                    import numpy as _np
                    tri = corr.where(_np.triu(
                        _np.ones(corr.shape, dtype=bool), k=1
                    ))
                    avg_corr = float(tri.stack().mean())
                else:
                    avg_corr = None
            except Exception:
                avg_corr = None

            warn_lines = []
            if top_w >= 40.0:
                warn_lines.append(
                    f"⚠️  Top position weight **{top_w:.0f}%** of equity "
                    "— concentrated."
                )
            if avg_corr is not None and avg_corr >= 0.7:
                warn_lines.append(
                    f"⚠️  Avg pairwise correlation of holdings "
                    f"**{avg_corr:.2f}** — low diversification "
                    "(your positions tend to move together)."
                )
            ok_lines = []
            if not warn_lines:
                ok_lines.append(
                    f"✅  Top weight {top_w:.0f}%"
                    + (f" · avg corr {avg_corr:.2f}"
                       if avg_corr is not None else "")
                    + " — healthy spread."
                )
            for line in warn_lines + ok_lines:
                st.caption(line)
    else:
        st.info("No open positions yet — use the Buy form below to get started.")

    st.markdown("---")

    # ─────────────────────────────────────────────────────────────────────
    #  Buy / Sell forms
    # ─────────────────────────────────────────────────────────────────────
    buy_col, sell_col = st.columns(2)

    with buy_col:
        st.markdown(heading("buy", "Buy"))
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
                # Sizing helper — picks how the cash amount is decided:
                #   Fixed CHF       → you type a raw amount (legacy default)
                #   % of cash       → fixed-fractional sizing off available cash
                #   % of equity     → percent of total portfolio value
                sizing = st.radio(
                    "Sizing",
                    ["Fixed CHF", "% of cash", "% of equity"],
                    horizontal=True, key="game_buy_sizing",
                    help=("Fixed-fractional sizing (5% of cash) is the "
                          "classic introductory rule; % of equity is the "
                          "'always same share of net worth' variant."),
                )
                if sizing == "Fixed CHF":
                    amount = st.number_input(
                        f"Cash to spend (max {_money(max_cash)})",
                        min_value=0.0, max_value=max_cash,
                        value=min(500.0, max_cash), step=50.0,
                        key="game_buy_amt",
                    )
                elif sizing == "% of cash":
                    pct = st.slider(
                        "% of available cash", min_value=1, max_value=100,
                        value=5, step=1, key="game_buy_pct_cash",
                    )
                    amount = round(max_cash * pct / 100.0, 2)
                    st.caption(
                        f"= **{_money(amount)}** "
                        f"({pct}% × {_money(max_cash)} cash)"
                    )
                else:   # % of equity
                    total_val = float(mtm["total"])
                    pct = st.slider(
                        "% of equity (total value)",
                        min_value=1, max_value=100, value=5, step=1,
                        key="game_buy_pct_eq",
                    )
                    amount = round(
                        min(max_cash, total_val * pct / 100.0), 2
                    )
                    st.caption(
                        f"= **{_money(amount)}** "
                        f"({pct}% × {_money(total_val)} total, "
                        f"capped at {_money(max_cash)} available cash)"
                    )
                if amount > 0 and fill_buy > 0:
                    shares = amount / fill_buy
                    st.caption(
                        f"Last close: `{_money(price)}` as of {as_of[:10]} · "
                        f"fill `{_money(fill_buy)}` (+{SLIPPAGE_BPS} bps) → "
                        f"≈ **{shares:.4f}** shares"
                    )
                buy_note = st.text_input(
                    "Why? (optional note — your thesis for this trade)",
                    key="game_buy_note",
                    placeholder="e.g. RSI oversold + earnings beat",
                )
                if st.button("▶  Buy", type="primary", key="game_buy_btn",
                             disabled=(amount <= 0)):
                    try:
                        out = buy(sym_buy, amount, note=buy_note or None)
                        st.success(
                            f"Bought **{out['qty']:.4f}** {out['symbol']} "
                            f"@ {_money(out['fill_price'])} for "
                            f"{_money(out['spent'])}")
                        st.rerun()
                    except GameError as e:
                        st.error(str(e))

    with sell_col:
        st.markdown(heading("sell", "Sell"))
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
            sell_note = st.text_input(
                "Why? (optional note — your reason for closing/trimming)",
                key="game_sell_note",
                placeholder="e.g. broke 200d SMA, taking profits",
            )
            if st.button("▶  Sell", type="primary", key="game_sell_btn",
                         disabled=(qty_sell <= 0)):
                try:
                    out = sell(sym_sell, qty_sell, note=sell_note or None)
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
    st.markdown(heading("portfolio_chart", "Portfolio value over time"))
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
            y=mtm["starting_cash"], line_dash="dot", line_color=CHART_MUTED,
            annotation_text=f"Starting cash {_money(mtm['starting_cash'])}",
            annotation_position="bottom right",
            annotation_font_color=CHART_MUTED,
        )
        fig.update_layout(
            paper_bgcolor=CHART_BG, plot_bgcolor=CHART_BG,
            font=dict(family=CHART_FONT, size=11, color=CHART_MUTED),
            margin=dict(l=48, r=16, t=16, b=36), height=320,
            legend=dict(bgcolor="rgba(0,0,0,0)",
                        font=dict(size=10, color=CHART_INK)),
            xaxis=dict(gridcolor=CHART_GRID, linecolor=CHART_AXIS),
            yaxis=dict(gridcolor=CHART_GRID, linecolor=CHART_AXIS,
                       tickformat=",.0f"),
        )
        st.plotly_chart(fig, width="stretch")

    # ─────────────────────────────────────────────────────────────────────
    #  Strategy comparison (overlay value history of every active portfolio)
    # ─────────────────────────────────────────────────────────────────────
    if len(portfolios) > 1:
        with st.expander(
            f"{icon('compare')}  Compare strategies ({len(portfolios)})",
            expanded=False,
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
                # Normalise to % return from inception so strategies with
                # different starting cash are comparable on the same axis.
                ret_pct = (c_df["total"] / p["starting_cash"] - 1.0) * 100.0
                cmp_fig.add_trace(go.Scatter(
                    x=c_df["date"], y=ret_pct, mode="lines",
                    name=p["name"] + (" (active)" if is_active else ""),
                    line=dict(
                        color=palette[i % len(palette)],
                        width=2.5 if is_active else 1.5,
                    ),
                ))
            # Overlay the same equal-weight buy-and-hold watchlist baseline
            # used on the single-strategy chart, normalised to % from its
            # own inception. Start from the earliest portfolio creation
            # date so the benchmark spans the full chart range.
            from datetime import date as _date
            bench_syms = sorted(config_symbols) if config_symbols else watchlist
            if any_data and bench_syms:
                earliest = min(
                    _date.fromisoformat(p["created_at"][:10])
                    for p in portfolios
                )
                bench = benchmark_history(
                    bench_syms, starting_cash=10_000.0, start_date=earliest,
                )
                if bench:
                    bb_df = pd.DataFrame(bench)
                    bb_df["date"] = pd.to_datetime(bb_df["date"])
                    bench_pct = (bb_df["value"] / 10_000.0 - 1.0) * 100.0
                    cmp_fig.add_trace(go.Scatter(
                        x=bb_df["date"], y=bench_pct, mode="lines",
                        name=f"Buy-and-hold watchlist ({len(bench_syms)} eq-wt)",
                        line=dict(color="#a78bfa", width=1.5, dash="dot"),
                    ))
            if not any_data:
                st.caption("No trade history on any strategy yet.")
            else:
                cmp_fig.add_hline(
                    y=0.0, line_dash="dot", line_color=CHART_MUTED,
                    annotation_text="Inception baseline (0 %)",
                    annotation_position="bottom right",
                    annotation_font_color=CHART_MUTED,
                )
                cmp_fig.update_layout(
                    paper_bgcolor=CHART_BG, plot_bgcolor=CHART_BG,
                    font=dict(family=CHART_FONT, size=11, color=CHART_MUTED),
                    margin=dict(l=48, r=16, t=16, b=36), height=320,
                    legend=dict(bgcolor="rgba(0,0,0,0)",
                                font=dict(size=10, color=CHART_INK)),
                    xaxis=dict(gridcolor=CHART_GRID, linecolor=CHART_AXIS),
                    yaxis=dict(gridcolor=CHART_GRID, linecolor=CHART_AXIS,
                               ticksuffix=" %", tickformat=".1f"),
                )
                st.plotly_chart(cmp_fig, width="stretch")
                st.caption(
                    "Return % from inception — all strategies start at 0 %, "
                    "so you can compare relative performance directly."
                )

    st.markdown("---")

    # ─────────────────────────────────────────────────────────────────────
    #  Trade history
    # ─────────────────────────────────────────────────────────────────────
    st.markdown(heading("trade_history", "Trade history"))
    from stock_toolkit.game import get_trades, trade_stats
    trades = get_trades()
    if not trades:
        st.info("No trades yet.")
    else:
        t_df = pd.DataFrame(trades)
        if "note" not in t_df.columns:
            t_df["note"] = ""
        t_display = pd.DataFrame({
            "When":       t_df["timestamp"].map(lambda v: v[:19].replace("T", " ")),
            "Side":       t_df["side"].map(str.upper),
            "Symbol":     t_df["symbol"],
            "Shares":     t_df["qty"].map(lambda v: f"{v:.4f}"),
            "Close":      t_df["price"].map(_money),
            "Fill":       t_df["fill_price"].map(_money),
            "Cash Δ":     t_df["cash_delta"].map(_money),
            "Note":       t_df["note"].fillna(""),
        })
        st.dataframe(
            t_display.iloc[::-1].reset_index(drop=True),   # newest first
            width="stretch", hide_index=True,
        )
        # CSV export — newest-first to match the display, includes notes.
        csv_blob = t_display.iloc[::-1].to_csv(index=False).encode("utf-8")
        st.download_button(
            f"{icon('download')}  Download trade history (CSV)",
            data=csv_blob,
            file_name=f"trades_{mtm['name'].replace(' ', '_')}.csv",
            mime="text/csv",
            key="game_trades_csv",
        )

    # ─────────────────────────────────────────────────────────────────────
    #  Outcome stats — win rate, average win/loss, expectancy
    # ─────────────────────────────────────────────────────────────────────
    stats = trade_stats()
    if stats["closed_count"] > 0:
        st.markdown(heading("outcome_stats",
                            "Outcome stats (closed round-trips)"))
        s1, s2, s3, s4, s5 = st.columns(5)
        s1.metric("Closed trades", f"{stats['closed_count']}")
        s2.metric("Win rate",      f"{stats['win_rate'] * 100:.0f}%",
                  delta=f"{stats['wins']} / {stats['losses']} W/L")
        s3.metric("Avg win",       _money(stats["avg_win"]))
        s4.metric("Avg loss",      _money(stats["avg_loss"]))
        s5.metric("Expectancy",    _money(stats["expectancy"]),
                  delta=("positive edge" if stats["expectancy"] > 0
                         else "negative edge"
                         if stats["expectancy"] < 0 else "flat"))
        st.caption(
            f"Realized P/L across all closed round-trips: "
            f"**{_money(stats['realized_pnl'])}**. Expectancy = "
            f"win_rate × avg_win + loss_rate × avg_loss; the expected "
            f"$ outcome of an average trade in this strategy."
        )
    elif trades:
        st.caption(
            "Open the **🎯 Outcome stats** by closing at least one position "
            "(win rate / expectancy require closed round-trips)."
        )

    st.markdown("---")

    # ─────────────────────────────────────────────────────────────────────
    #  History — audit log of mutations on this DB (v2.4.2+)
    # ─────────────────────────────────────────────────────────────────────
    #  Reads from the v2.4.0 audit_log table; surfaces destructive
    #  recovery sources (the snapshot path embedded in note +
    #  the before_json snapshot of full pre-state).
    _render_history(mtm)

    st.markdown("---")

    # ─────────────────────────────────────────────────────────────────────
    #  Settings — current strategy: rename / reset / archive / delete
    # ─────────────────────────────────────────────────────────────────────
    with st.expander(
        f"{icon('settings_strategy')}  Settings — strategy {mtm['name']!r}"
    ):
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


_OP_PREFIX_CHOICES = (
    ("All operations",   None),
    ("Portfolio ops",    "portfolio."),
    ("Trade ops",        "trade."),
    ("System / migrations", "system."),
)


def _render_history(mtm: dict) -> None:
    """v2.4.2 — History expander: every audited mutation on this DB,
    newest first, with click-to-expand detail showing before/after JSON
    and the pre-destructive snapshot path (when present in the note)."""
    import json
    import re
    from pathlib import Path as _Path

    with st.expander(
        f"{icon('audit_history')}  History (audit log)", expanded=False,
    ):
        st.caption(
            "Every mutation on `portfolio.db` since v2.4.0 — user actions "
            "and system events. Destructive ops (delete / reset) embed "
            "the full pre-state in **before_json** and link to the "
            "pre-destructive backup snapshot (v2.4.1+) so they remain "
            "recoverable."
        )

        # Filter controls
        f1, f2, f3 = st.columns([2, 2, 1])
        with f1:
            scope_label = st.selectbox(
                "Scope",
                ("Current strategy only", "All strategies in this DB"),
                index=0, key="game_audit_scope",
                help="'Current strategy only' includes trade rows on the "
                     "active portfolio AND any portfolio-level row "
                     "targeting it (rename, archive, etc.).",
            )
        with f2:
            prefix_labels = [c[0] for c in _OP_PREFIX_CHOICES]
            chosen = st.selectbox(
                "Filter by operation kind",
                prefix_labels, index=0, key="game_audit_prefix",
            )
            op_prefix = next(c[1] for c in _OP_PREFIX_CHOICES
                             if c[0] == chosen)
        with f3:
            limit = st.selectbox(
                "Show",
                (50, 100, 250, 1000), index=0, key="game_audit_limit",
                help="Max rows to load (newest first).",
            )

        portfolio_id = (mtm["id"]
                        if scope_label == "Current strategy only" else None)
        events = get_audit_log(
            portfolio_id=portfolio_id, limit=limit, op_prefix=op_prefix,
        )

        if not events:
            st.info(
                "No audit rows match the current filter. "
                "(Pre-v2.4.0 mutations are unaudited; they don't appear "
                "here even if the underlying rows still exist.)"
            )
            return

        # Compact table — full detail goes in per-row expanders below.
        e_df = pd.DataFrame([
            {
                "When":   e["timestamp"][:19].replace("T", " "),
                "Actor":  e["actor"],
                "Op":     e["op_type"],
                "Target": (f"{e['target_kind']}#{e['target_id']}"
                          if e["target_kind"] else "—"),
                "Note":   (e["note"] or "")[:80] + (
                          "…" if e["note"] and len(e["note"]) > 80 else ""),
            }
            for e in events
        ])
        st.dataframe(e_df, width="stretch", hide_index=True)

        # CSV export — same view, untruncated note column.
        full_df = pd.DataFrame([
            {
                "id":         e["id"],
                "timestamp":  e["timestamp"],
                "actor":      e["actor"],
                "op_type":    e["op_type"],
                "target_kind": e["target_kind"] or "",
                "target_id":  e["target_id"] if e["target_id"] is not None else "",
                "note":       e["note"] or "",
            }
            for e in events
        ])
        st.download_button(
            f"{icon('download')}  Download audit log (CSV)",
            data=full_df.to_csv(index=False).encode("utf-8"),
            file_name=f"audit_{mtm['name'].replace(' ', '_')}.csv",
            mime="text/csv",
            key="game_audit_csv",
        )

        # Per-row detail. Capped at 30 expanders so the page stays
        # responsive on big logs — full data is still in the CSV.
        st.caption(
            f"Showing detail for the {min(len(events), 30)} newest of "
            f"{len(events)} row(s) below. Use the CSV for the full set."
        )
        for e in events[:30]:
            summary = (
                f"`{e['op_type']}`  ·  {e['actor']}  ·  "
                f"{e['timestamp'][:19].replace('T', ' ')}"
            )
            with st.expander(summary, expanded=False):
                st.markdown(
                    f"**Op:** `{e['op_type']}` · **Actor:** {e['actor']} · "
                    f"**Target:** {e['target_kind'] or '—'} "
                    f"id={e['target_id'] if e['target_id'] is not None else '—'}"
                )
                if e["note"]:
                    st.markdown(f"**Note:** {e['note']}")
                    # If the note carries a pre-destructive snapshot
                    # path, make it discoverable in plain text — most
                    # terminal copy-paste flows want the raw path.
                    m = re.search(r"pre_destructive_snapshot=(\S+)", e["note"])
                    if m:
                        snap_path = _Path(m.group(1))
                        exists = snap_path.exists()
                        st.markdown(
                            f"**Pre-destructive snapshot:** `{snap_path}` "
                            f"({'✓ on disk' if exists else '✗ missing'})"
                        )
                        if exists:
                            st.caption(
                                "Restore by copying it back into place, e.g. "
                                f"`cp '{snap_path / 'portfolio.db'}' "
                                f"'{mtm.get('_db_path', 'data/portfolio.db')}'`"
                            )
                if e["before"] is not None:
                    st.markdown("**Before** (pre-state — the recovery source):")
                    st.code(json.dumps(e["before"], indent=2, default=str),
                            language="json")
                if e["after"] is not None:
                    st.markdown("**After**:")
                    st.code(json.dumps(e["after"], indent=2, default=str),
                            language="json")


if __name__ == "__main__":
    render()
