"""
Admin page — backend commands in the UI.

Exposes the operations that were previously CLI-only: edit your
watchlist, trigger collection or historical backfill, view DB
inventory, and inspect the failure tracker. Implementation reuses the
CLI entry points via subprocess so behaviour matches whatever you'd
get from the shell — no duplicated logic.

Kept as a normal Python module so it's testable (the `pages/` shim
that wires it into Streamlit's sidebar nav can't be imported by a
normal `from ... import` because of the emoji/digit filename).
"""

import os
import sqlite3
import subprocess
import sys

import streamlit as st

from stock_toolkit.common import (
    BASE_DIR, CONFIG_PATH, load_config, update_config_value,
)

# Source presets matching the tiered crontab.demo / launchd / docker schedule
_COLLECT_TIERS = {
    "Morning (yfinance only)":
        ["yfinance"],
    "Midday (yfinance + Finnhub)":
        ["yfinance", "finnhub"],
    "EOD (full sweep — all configured sources)":
        ["yfinance", "alphavantage", "polygon", "fmp",
         "twelvedata", "marketstack"],
}


def _parse_csv(raw: str) -> list:
    """Split a comma-separated value into a clean uppercase symbol list."""
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def _run(cmd: list, label: str, timeout: int = 600) -> tuple:
    """Run a CLI command; return (success, combined_output)."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=str(BASE_DIR), timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"⏱  {label} timed out after {timeout}s."
    except Exception as e:
        return False, f"Error launching {label}: {e}"
    text = "\n".join(filter(None,
                            [result.stdout.strip(), result.stderr.strip()]))
    return result.returncode == 0, text or "(no output)"


def render():
    st.set_page_config(page_title="Stock Toolkit — Admin", page_icon="⚙️",
                       layout="wide")
    st.title("⚙️ Admin")
    st.caption(
        "Backend operations: edit your watchlist, trigger collection, "
        "inspect the database. Every action here is the same code path "
        "as the matching CLI command."
    )

    cfg = load_config(CONFIG_PATH)

    # ─────────────────────────────────────────────────────────────────────────
    #  Watchlist
    # ─────────────────────────────────────────────────────────────────────────
    st.markdown("### 📋  Watchlist")
    if not CONFIG_PATH.exists():
        st.warning(
            f"`config.env` not found at `{CONFIG_PATH}`. Run "
            "`stock-setup` (or `docker compose run --rm ui stock-setup`) "
            "first to create one."
        )
    else:
        col_sym, col_ign = st.columns(2)
        with col_sym:
            sym_raw = st.text_area(
                "SYMBOLS",
                value=cfg.get("SYMBOLS", ""),
                height=120,
                help="Comma-separated tickers. Use exchange suffixes for "
                     "non-US: ENEL.MI (Milan), DOCM.SW (Swiss), SAP.DE.",
                key="adm_symbols",
            )
        with col_ign:
            ign_raw = st.text_area(
                "SYMBOLS_IGNORE (blocked tickers)",
                value=cfg.get("SYMBOLS_IGNORE", ""),
                height=120,
                help="Blocks bare EU tickers that duplicate suffixed ones, "
                     "e.g. ENI vs ENI.MI.",
                key="adm_ignore",
            )

        new_syms = _parse_csv(sym_raw)
        old_syms = _parse_csv(cfg.get("SYMBOLS", ""))
        added    = [s for s in new_syms if s not in old_syms]
        removed  = [s for s in old_syms if s not in new_syms]

        save_col, info_col = st.columns([1, 3])
        with save_col:
            save_clicked = st.button("💾  Save watchlist", type="primary")
        with info_col:
            if added:
                st.info(f"Will add: `{', '.join(added)}`")
            if removed:
                st.info(f"Will remove: `{', '.join(removed)}`")

        if save_clicked:
            update_config_value("SYMBOLS",        ",".join(new_syms),
                                CONFIG_PATH)
            update_config_value("SYMBOLS_IGNORE", ",".join(_parse_csv(ign_raw)),
                                CONFIG_PATH)
            st.success("✅  Saved to `config.env`.")
            if added:
                st.markdown(
                    "**Tip:** new tickers have no history yet — run a "
                    "bootstrap below to backfill them."
                )

    # ─────────────────────────────────────────────────────────────────────────
    #  Collect & bootstrap
    # ─────────────────────────────────────────────────────────────────────────
    st.markdown("### 📥  Collect & bootstrap")

    tier_col, sym_col = st.columns([2, 2])
    with tier_col:
        tier = st.selectbox(
            "Collection tier",
            list(_COLLECT_TIERS),
            help="Matches the scheduled jobs in crontab.demo / launchd / "
                 "the docker collector service.",
            key="adm_tier",
        )
    with sym_col:
        scope = st.text_input(
            "Symbol(s), optional",
            help="Comma-separated. Leave blank to collect the whole "
                 "watchlist.",
            key="adm_collect_sym",
        )

    btn_col_a, btn_col_b, _ = st.columns([1, 1, 2])
    with btn_col_a:
        collect_clicked = st.button("▶  Run collection now")
    with btn_col_b:
        bootstrap_clicked = st.button(
            "🌱  Bootstrap full history",
            help="One-time `stock-bootstrap` — pulls all available history "
                 "via yfinance for the listed symbols (or watchlist)."
        )

    if collect_clicked:
        cmd = [sys.executable, "-m", "stock_toolkit.collector",
               "--sources", *_COLLECT_TIERS[tier]]
        if scope.strip():
            cmd += ["-s", *_parse_csv(scope)]
        with st.spinner(f"Running {tier.lower()}…"):
            ok, out = _run(cmd, "collection", timeout=900)
        (st.success if ok else st.error)(
            f"{'✅' if ok else '❌'}  Collection {'finished' if ok else 'failed'}.")
        with st.expander("📋  Output", expanded=not ok):
            st.code(out, language=None)

    if bootstrap_clicked:
        cmd = [sys.executable, "-m", "stock_toolkit.bootstrap"]
        if scope.strip():
            cmd += ["-s", *_parse_csv(scope)]
        with st.spinner("Bootstrapping historical data via yfinance…"):
            ok, out = _run(cmd, "bootstrap", timeout=1800)
        (st.success if ok else st.error)(
            f"{'✅' if ok else '❌'}  Bootstrap {'finished' if ok else 'failed'}.")
        with st.expander("📋  Output", expanded=not ok):
            st.code(out, language=None)

    # ─────────────────────────────────────────────────────────────────────────
    #  Inventory
    # ─────────────────────────────────────────────────────────────────────────
    st.markdown("### 🗂  Inventory")
    inv_a, inv_b, inv_c, inv_d = st.columns(4)
    with inv_a:
        summary_clicked = st.button("📊  Summary")
    with inv_b:
        check_clicked = st.button("🔍  Check gaps")
    with inv_c:
        fill_dry_clicked = st.button(
            "🧪  Preview gap-fill",
            help="Show which date ranges would be re-fetched from yfinance, "
                 "without writing to the DB.",
        )
    with inv_d:
        fill_clicked = st.button(
            "🩹  Fill gaps",
            type="primary",
            help="Re-fetch missing date ranges from yfinance and insert them "
                 "into the live DB. Holiday-style short gaps are skipped "
                 "automatically (yfinance returns nothing).",
        )

    if summary_clicked:
        ok, out = _run(
            [sys.executable, "-m", "stock_toolkit.inventory", "--summary"],
            "inventory --summary", timeout=60)
        with st.expander("📋  Inventory summary", expanded=True):
            st.code(out, language=None)
    if check_clicked:
        ok, out = _run(
            [sys.executable, "-m", "stock_toolkit.inventory", "--check"],
            "inventory --check", timeout=120)
        with st.expander("📋  Consistency check", expanded=True):
            st.code(out, language=None)
    if fill_dry_clicked:
        ok, out = _run(
            [sys.executable, "-m", "stock_toolkit.gap_fill", "--dry-run"],
            "gap-fill --dry-run", timeout=60)
        with st.expander("📋  Gap-fill preview", expanded=True):
            st.code(out, language=None)
    if fill_clicked:
        with st.spinner("Fetching missing date ranges via yfinance…"):
            ok, out = _run(
                [sys.executable, "-m", "stock_toolkit.gap_fill"],
                "gap-fill", timeout=900)
        (st.success if ok else st.error)(
            f"{'✅' if ok else '❌'}  Gap-fill {'finished' if ok else 'failed'}.")
        with st.expander("📋  Gap-fill output", expanded=True):
            st.code(out, language=None)

    # ─────────────────────────────────────────────────────────────────────────
    #  Failure tracker
    # ─────────────────────────────────────────────────────────────────────────
    st.markdown("### ⚠️  Suppressed (symbol, source) pairs")
    failures_db = BASE_DIR / "stock_failures.db"
    threshold = int(cfg.get("FAILURE_THRESHOLD", "5"))
    if not failures_db.exists():
        st.info("No `stock_failures.db` yet — nothing has failed enough "
                "times to be tracked.")
    else:
        try:
            con = sqlite3.connect(failures_db)
            # Schema: (symbol, source, reason, hits, first_seen, last_seen)
            # Suppression is implicit — hits >= FAILURE_THRESHOLD blocks
            # the (symbol, source) pair from future fetches.
            rows = con.execute(
                "SELECT symbol, source, hits, reason, last_seen "
                "FROM failures ORDER BY hits DESC, symbol"
            ).fetchall()
            con.close()
        except Exception as e:
            st.error(f"Could not read failure tracker: {e}")
            rows = []
        if not rows:
            st.success("No tracked failures — every (symbol, source) pair "
                       "is healthy.")
        else:
            import pandas as pd
            df = pd.DataFrame(
                rows,
                columns=["Symbol", "Source", "Hits", "Reason", "Last seen"],
            )
            df["Suppressed"] = df["Hits"].ge(threshold).map(
                {True: "🚫", False: ""})
            n_suppressed = int(df["Hits"].ge(threshold).sum())
            st.dataframe(
                df[["Suppressed", "Symbol", "Source", "Hits",
                    "Reason", "Last seen"]],
                width="stretch", hide_index=True,
            )
            st.caption(
                f"🚫 = suppressed (hits ≥ FAILURE_THRESHOLD={threshold}). "
                f"{n_suppressed}/{len(rows)} pair(s) currently suppressed. "
                "Clear from the command line: `sqlite3 stock_failures.db "
                "\"DELETE FROM failures WHERE symbol='X' AND source='Y'\"`"
            )

    # ─────────────────────────────────────────────────────────────────────────
    #  Footer — data dir + Docker hint
    # ─────────────────────────────────────────────────────────────────────────
    st.markdown("---")
    st.caption(
        f"Data directory (`$STOCK_DIR`): `{BASE_DIR}`  ·  "
        f"Config: `{CONFIG_PATH.name}` "
        f"{'(exists)' if CONFIG_PATH.exists() else '(missing — run stock-setup)'}"
    )
    if os.environ.get("STREAMLIT_SERVER_ADDRESS") == "0.0.0.0":
        st.caption(
            "🐳  Running in Docker. Restart the collector container after "
            "saving the watchlist: `docker compose restart collector`."
        )
