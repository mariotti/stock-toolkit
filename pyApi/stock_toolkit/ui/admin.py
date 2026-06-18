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
from stock_toolkit.ui.icons import heading, icon

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
    #  First-run banner — guides new users through the three sections needed
    #  to reach a working install. Disappears once SYMBOLS is non-empty.
    # ─────────────────────────────────────────────────────────────────────────
    if not (cfg.get("SYMBOLS") or "").strip():
        st.info(
            "👋  **First time here?** Three quick steps to get going:\n\n"
            "1. **Watchlist** below — add the tickers you care about "
            "(yfinance works with no key).\n"
            "2. **API Keys** — optional, only if you want extra sources "
            "or the Claude Briefing tab.\n"
            "3. **Settings** — choose which sources the Collect tab can "
            "use, and (optionally) wire up email / Pushover / Slack "
            "notifications.\n\n"
            "All three write to `config.env` and take effect on the "
            "next request — no restart."
        )

    # ─────────────────────────────────────────────────────────────────────────
    #  Watchlist
    # ─────────────────────────────────────────────────────────────────────────
    st.markdown(heading("watchlist", "Watchlist"))
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
            save_clicked = st.button(
                f"{icon('save')}  Save watchlist", type="primary",
            )
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
    #  API keys — let click-to-run users add their own keys without a shell
    # ─────────────────────────────────────────────────────────────────────────
    # (env_key, label, url, hint, expose_current)
    #   expose_current=True  → pre-fill the field with the saved value
    #     so Streamlit's eye toggle can reveal it. Rate-limit-only
    #     blast radius — safe over localhost or HTTPS.
    #   expose_current=False → field always empty; saving requires
    #     re-entering the key. Use for billable keys (Anthropic).
    _KEY_DEFS = [
        ("ALPHAVANTAGE_KEY",  "Alpha Vantage",
         "https://www.alphavantage.co/support/#api-key",
         "25 calls/day free — good EU+US daily bars", True),
        ("FINNHUB_KEY",       "Finnhub",
         "https://finnhub.io/register",
         "60 calls/min free — US real-time quotes", True),
        ("MASSIVE_KEY",       "Massive (Polygon.io)",
         "https://massive.com/dashboard",
         "5 calls/min free — US EOD bars", True),
        ("FMP_KEY",           "Financial Modeling Prep",
         "https://site.financialmodelingprep.com/developer/docs/dashboard",
         "250 calls/day free — US large-caps", True),
        ("TWELVEDATA_KEY",    "Twelve Data",
         "https://twelvedata.com/register",
         "8 credits/min free — US daily + hourly", True),
        ("MARKETSTACK_KEY",   "Marketstack",
         "https://marketstack.com/signup",
         "100 calls/month free — EOD bars incl. EU", True),
        ("ANTHROPIC_API_KEY", "Anthropic (Briefing tab)",
         "https://console.anthropic.com/",
         "Pay-as-you-go — ~$0.01 per briefing on Sonnet", False),
    ]
    with st.expander(f"{icon('api_keys')}  API Keys", expanded=False):
        st.caption(
            "Add free API keys here without dropping to a shell. "
            "yfinance works with no key — only configure the others "
            "if you want their extra data."
        )
        st.caption(
            "Free-tier keys are pre-filled so Streamlit's 👁  toggle "
            "can reveal them. Billable keys (Anthropic) stay blank — "
            "type a new value to set, leave blank to keep the existing "
            "one."
        )
        st.warning(
            "🔒  For paid keys (Anthropic, paid tiers of other "
            "services), **edit `config.env` directly on the host** "
            "rather than typing them into a web form — it avoids "
            "leaking the key into browser history and (if the "
            "dashboard isn't behind HTTPS) into network traffic."
        )

        new_values = {}
        for key_name, label, url, hint, expose in _KEY_DEFS:
            current = (cfg.get(key_name) or "").strip()
            state   = "✅ set" if current else "—  unset"
            if expose:
                placeholder = "Paste your key here" if not current else ""
                tip = (f"Register a key at {url}. Click 👁 to reveal "
                       "the current value. Erase the field and save "
                       "to delete the key.")
            else:
                placeholder = ("Leave blank to keep the current key"
                               if current else "Paste your key here")
                tip = (f"Register a key at {url}. Paid/billable key — "
                       "kept hidden. Leave blank to keep the existing "
                       "value.")
            new_values[key_name] = st.text_input(
                f"{label}  ·  *{hint}*  ·  [{state}]",
                value=current if expose else "",
                type="password",
                key=f"adm_key_{key_name}",
                help=tip,
                placeholder=placeholder,
            )

        if st.button(f"{icon('save')}  Save keys", type="primary",
                     key="adm_save_keys"):
            saved   = []
            cleared = []
            for key_name, _label, _url, _hint, expose in _KEY_DEFS:
                v       = new_values[key_name].strip()
                current = (cfg.get(key_name) or "").strip()
                if expose:
                    # Field showed current; any change (including
                    # erasure) is intentional.
                    if v == current:
                        continue                # no-op
                    update_config_value(key_name, v, CONFIG_PATH)
                    (saved if v else cleared).append(key_name)
                else:
                    # Field always empty; only save what the user
                    # actually typed, blank = keep current.
                    if v:
                        update_config_value(key_name, v, CONFIG_PATH)
                        saved.append(key_name)
            if saved or cleared:
                # Re-read config.env into the live _cfg dict so the
                # new keys take effect immediately — no restart needed.
                from stock_toolkit.ui.helpers import reload_config
                reload_config()
                parts = []
                if saved:
                    parts.append(
                        f"saved {len(saved)} key"
                        f"{'s' if len(saved) != 1 else ''} "
                        f"({', '.join(saved)})"
                    )
                if cleared:
                    parts.append(
                        f"cleared {len(cleared)} key"
                        f"{'s' if len(cleared) != 1 else ''} "
                        f"({', '.join(cleared)})"
                    )
                st.success(
                    "✅  " + " · ".join(parts).capitalize()
                    + ". The dashboard will use them on the next request."
                )
            else:
                st.info("No changes detected.")

    # ─────────────────────────────────────────────────────────────────────────
    #  Settings — paid-tier flags, UI collect sources, notification channels
    # ─────────────────────────────────────────────────────────────────────────
    _ALL_SOURCES = ["yfinance", "alphavantage", "finnhub", "polygon",
                    "fmp", "twelvedata", "marketstack"]

    with st.expander("🛠  Settings", expanded=False):
        st.caption(
            "Everything else `stock-setup` would ask for, edit-in-place. "
            "All changes save to `config.env`."
        )

        # ── paid-tier flags ────────────────────────────────────────────
        st.markdown("**Paid-tier flags** — tick if you have a paid plan.")
        pa1, pa2 = st.columns(2)
        cur_finn_paid = (cfg.get("FINNHUB_PAID") or "false").lower() == "true"
        cur_av_paid   = (cfg.get("ALPHAVANTAGE_PAID") or "false").lower() == "true"
        new_finn_paid = pa1.checkbox(
            "Finnhub paid (EU + candles)",
            value=cur_finn_paid, key="adm_finn_paid",
        )
        new_av_paid = pa2.checkbox(
            "Alpha Vantage paid (full history)",
            value=cur_av_paid, key="adm_av_paid",
        )

        # ── UI collect sources ─────────────────────────────────────────
        st.markdown("**UI collect sources** — which sources the "
                    "*Collect* tab can trigger.")
        cur_ui = [s.strip() for s in
                  (cfg.get("UI_COLLECT_SOURCES") or "yfinance").split(",")
                  if s.strip()]
        new_ui = st.multiselect(
            "Allowed sources", _ALL_SOURCES, default=cur_ui,
            help="Keep conservative to protect daily-call budgets.",
            key="adm_ui_collect",
        )

        # ── notifications ─────────────────────────────────────────────
        st.markdown("**Notifications** — leave blank to disable a channel.")

        st.caption("📧  Email (SMTP)")
        em1, em2 = st.columns(2)
        new_email = em1.text_input(
            "ALERT_EMAIL", value=cfg.get("ALERT_EMAIL", ""),
            placeholder="you@example.com", key="adm_alert_email",
        )
        new_smtp_host = em2.text_input(
            "ALERT_SMTP_HOST", value=cfg.get("ALERT_SMTP_HOST", ""),
            placeholder="smtp.gmail.com", key="adm_smtp_host",
        )
        em3, em4, em5 = st.columns([1, 2, 2])
        new_smtp_port = em3.text_input(
            "Port", value=cfg.get("ALERT_SMTP_PORT", "587"),
            key="adm_smtp_port",
        )
        new_smtp_user = em4.text_input(
            "ALERT_SMTP_USER", value=cfg.get("ALERT_SMTP_USER", ""),
            key="adm_smtp_user",
        )
        new_smtp_pass = em5.text_input(
            "ALERT_SMTP_PASS", value="",
            type="password",
            placeholder=("•••••  set"
                         if (cfg.get("ALERT_SMTP_PASS") or "").strip()
                         else "App-specific password"),
            help="Leave blank to keep the current password.",
            key="adm_smtp_pass",
        )

        st.caption("📱  Pushover")
        pu1, pu2 = st.columns(2)
        new_po_user = pu1.text_input(
            "PUSHOVER_USER_KEY", value=cfg.get("PUSHOVER_USER_KEY", ""),
            type="password", key="adm_po_user",
        )
        new_po_token = pu2.text_input(
            "PUSHOVER_APP_TOKEN", value=cfg.get("PUSHOVER_APP_TOKEN", ""),
            type="password", key="adm_po_token",
        )

        st.caption("💬  Slack")
        new_slack = st.text_input(
            "SLACK_WEBHOOK_URL", value=cfg.get("SLACK_WEBHOOK_URL", ""),
            type="password",
            placeholder="https://hooks.slack.com/services/…",
            key="adm_slack",
        )

        if st.button("✓  Save settings", type="primary",
                     key="adm_save_settings"):
            # Boolean flags
            update_config_value("FINNHUB_PAID",
                                "true" if new_finn_paid else "false",
                                CONFIG_PATH)
            update_config_value("ALPHAVANTAGE_PAID",
                                "true" if new_av_paid else "false",
                                CONFIG_PATH)
            # UI sources — guarantee at least yfinance so the tab works
            update_config_value(
                "UI_COLLECT_SOURCES",
                ",".join(new_ui) if new_ui else "yfinance",
                CONFIG_PATH,
            )
            # Plain-text notification fields
            for k, v in [
                ("ALERT_EMAIL",      new_email),
                ("ALERT_SMTP_HOST",  new_smtp_host),
                ("ALERT_SMTP_PORT",  new_smtp_port),
                ("ALERT_SMTP_USER",  new_smtp_user),
                ("PUSHOVER_USER_KEY",  new_po_user),
                ("PUSHOVER_APP_TOKEN", new_po_token),
                ("SLACK_WEBHOOK_URL",  new_slack),
            ]:
                update_config_value(k, v.strip(), CONFIG_PATH)
            # SMTP password — blank means keep current
            if new_smtp_pass.strip():
                update_config_value(
                    "ALERT_SMTP_PASS", new_smtp_pass, CONFIG_PATH,
                )
            from stock_toolkit.ui.helpers import reload_config
            reload_config()
            st.success(
                "✅  Saved to `config.env`. Takes effect on the next "
                "request — no restart."
            )

    # ─────────────────────────────────────────────────────────────────────────
    #  Collect & bootstrap
    # ─────────────────────────────────────────────────────────────────────────
    st.markdown(heading("collect", "Collect & bootstrap"))

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
    st.markdown(heading("inventory", "Inventory"))
    inv_a, inv_b, inv_c, inv_d = st.columns(4)
    with inv_a:
        summary_clicked = st.button(f"{icon('summary')}  Summary")
    with inv_b:
        check_clicked = st.button("🔍  Check gaps")
    with inv_c:
        fill_dry_clicked = st.button(
            f"{icon('preview')}  Preview gap-fill",
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
    st.markdown(heading("suppressed", "Suppressed (symbol, source) pairs"))
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
