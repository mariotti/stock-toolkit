"""Collect tab."""

import subprocess
import sys

import pandas as pd
import streamlit as st

from stock_toolkit.common import BASE_DIR
from stock_toolkit.ui.helpers import (
    _cfg, get_all_symbols,
)


def render(selected_symbols, date_from_str, date_to_str):

    st.markdown("### 📥  Data Collection")
    st.markdown(
        "Run the data collector on demand — useful for adding a new symbol "
        "or refreshing data outside the cron schedule. "
        "Background cron jobs continue to run independently."
    )

    # ── allowed sources from config ───────────────────────────────────────────
    _raw_sources = _cfg.get("UI_COLLECT_SOURCES", "yfinance")
    ALLOWED_SOURCES = [s.strip() for s in _raw_sources.split(",") if s.strip()]
    ALL_SOURCES = ["yfinance", "alphavantage", "finnhub", "polygon",
                   "fmp", "twelvedata", "marketstack"]

    # ── layout ────────────────────────────────────────────────────────────────
    col_sym, col_src = st.columns([2, 3])

    with col_sym:
        new_symbol = st.text_input(
            "Add / collect symbol",
            placeholder="e.g. NVDA or RACE.MI",
            help=(
                "Leave blank to collect all symbols already in config.env and the DB. "
                "Enter a ticker to collect that symbol specifically — "
                "once collected it will be picked up by the cron jobs automatically."
            ),
            key="collect_symbol_input"
        ).strip().upper()

    with col_src:
        selected_sources = st.multiselect(
            "Sources",
            options=ALLOWED_SOURCES,
            default=ALLOWED_SOURCES,
            help=(
                "Sources available here are controlled by UI_COLLECT_SOURCES in config.env. "
                "Default: yfinance only (no API key needed, no rate limits)."
            ),
            key="collect_sources_select"
        )

    # note if sources are restricted
    locked_out = [s for s in ALL_SOURCES if s not in ALLOWED_SOURCES]
    if locked_out:
        st.caption(
            f"🔒  {', '.join(locked_out)} are disabled for the UI. "
            "To enable them add them to `UI_COLLECT_SOURCES` in config.env."
        )

    # ── run button ────────────────────────────────────────────────────────────
    st.markdown("---")
    run_col, info_col = st.columns([1, 3])

    with run_col:
        run_clicked = st.button(
            "▶  Run collection",
            type="primary",
            disabled=not selected_sources,
            key="collect_run_btn"
        )

    with info_col:
        if not selected_sources:
            st.warning("Select at least one source.")
        else:
            cmd_preview = ["python3", "-m", "stock_toolkit.collector"]
            if new_symbol:
                cmd_preview += ["-s", new_symbol]
            cmd_preview += ["--sources"] + selected_sources
            st.code(" ".join(cmd_preview), language="bash")

    # ── execute ───────────────────────────────────────────────────────────────
    if run_clicked and selected_sources:
        cmd = [sys.executable, "-m", "stock_toolkit.collector"]
        if new_symbol:
            cmd += ["-s", new_symbol]
        cmd += ["--sources"] + selected_sources

        label = f"Collecting {'`' + new_symbol + '`' if new_symbol else 'all symbols'} via {', '.join(selected_sources)}…"

        with st.spinner(label):
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    cwd=str(BASE_DIR),
                    timeout=300,   # 5 min hard limit
                )
                stdout = result.stdout.strip()
                stderr = result.stderr.strip()
                combined = "\n".join(filter(None, [stdout, stderr]))
            except subprocess.TimeoutExpired:
                combined = "⏱  Collection timed out after 5 minutes."
                result = None
            except Exception as e:
                combined = f"Error launching collector: {e}"
                result = None

        # ── results ───────────────────────────────────────────────────────
        if result is not None and result.returncode == 0:
            st.success("✅  Collection finished successfully.")
            get_all_symbols.clear()   # invalidate cache → sidebar refreshes on next rerun
        elif result is not None:
            st.error(f"❌  Collector exited with code {result.returncode}.")
        # else: timeout/exception already shown

        with st.expander("📋  Collector output", expanded=True):
            if combined:
                st.code(combined, language=None)
            else:
                st.write("_(no output)_")

        # refresh symbol count hint
        if new_symbol and result is not None and result.returncode == 0:
            st.info(
                f"**{new_symbol}** has been collected and added to the database. "
                "It will now be included in all future cron collection runs automatically."
            )

    # ── currently tracked symbols ─────────────────────────────────────────────
    st.markdown("---")
    st.markdown("**Currently tracked symbols**")

    try:
        import sqlite3 as _sq3
        _db = BASE_DIR / _cfg.get("DB_FILE", "stock_data.db")
        if _db.exists():
            _con = _sq3.connect(_db)
            _syms = _con.execute(
                "SELECT symbol, COUNT(*) as n, MIN(timestamp) as first, "
                "MAX(timestamp) as last FROM prices WHERE interval='1d' "
                "GROUP BY symbol ORDER BY symbol"
            ).fetchall()
            _con.close()
            if _syms:
                _df = pd.DataFrame(_syms, columns=["Symbol", "Bars", "First", "Last"])
                _df["In config"] = _df["Symbol"].apply(
                    lambda s: "✅" if s in selected_symbols else "—"
                )
                st.dataframe(_df, hide_index=True)
            else:
                st.write("No daily data found yet. Run the collector first.")
        else:
            st.write("Database not found. Run the collector first.")
    except Exception as e:
        st.warning(f"Could not read database: {e}")
