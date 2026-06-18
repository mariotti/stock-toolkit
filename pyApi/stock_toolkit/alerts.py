"""
stock_alerts.py
===============
Alert system for conditions evaluated against the latest collected data.
Fires once when a condition transitions from False to True (edge detection).
Suppresses repeated notifications until the condition clears and re-triggers.

Usage examples:
    python3 stock_alerts.py -s AAPL --when "rsi14 < 30"
    python3 stock_alerts.py -s AAPL MSFT --when "rsi14 < 30" --when "price > sma200"
    python3 stock_alerts.py -s AAPL --when "bbands_squeeze" --notify email
    python3 stock_alerts.py -s AAPL --when "price < sma50" --notify pushover
    python3 stock_alerts.py --list-conditions

Cron (every 30 min, market hours Mon-Fri):
    */30 9-17 * * 1-5 /usr/bin/python3 /path/to/stock_alerts.py -s AAPL MSFT ...

Notification config in config.env:
    ALERT_EMAIL=you@example.com
    ALERT_SMTP_HOST=smtp.gmail.com
    ALERT_SMTP_PORT=587
    ALERT_SMTP_USER=you@gmail.com
    ALERT_SMTP_PASS=your_app_password
    PUSHOVER_USER_KEY=your_user_key
    PUSHOVER_APP_TOKEN=your_app_token
    SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...

Install dependencies:
    pip install requests pandas numpy
"""

import argparse
import json
import smtplib
import sqlite3
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────
#  PATHS AND CONFIG
# ─────────────────────────────────────────────

from stock_toolkit.common import (
    CONFIG_PATH, DATA_DIR, HIST_DIR, LIVE_DB, load_config,
)

STATE_PATH = DATA_DIR / ".alerts_state.json"

SOURCE_PRIORITY = [
    "alphavantage", "fmp", "yfinance",
    "finnhub", "twelvedata", "polygon", "marketstack",
]

_cfg = load_config(CONFIG_PATH)


# ─────────────────────────────────────────────
#  DATA LOADING
# ─────────────────────────────────────────────

def discover_dbs() -> list[Path]:
    dbs = []
    if LIVE_DB.exists():
        dbs.append(LIVE_DB)
    if HIST_DIR.exists():
        dbs += sorted(HIST_DIR.glob("*.db"))
    return dbs


def load_series(symbol: str, n_bars: int = 250) -> pd.DataFrame:
    """Load the last n_bars of daily data for indicator computation."""
    dbs = discover_dbs()
    if not dbs:
        return pd.DataFrame()

    frames = []
    for db in dbs:
        try:
            con = sqlite3.connect(db)
            df  = pd.read_sql(
                "SELECT timestamp, source, open, high, low, close, volume "
                "FROM prices WHERE symbol=? AND interval='1d' "
                "ORDER BY timestamp DESC LIMIT ?",
                con, params=[symbol.upper(), n_bars * 2]
            )
            con.close()
            if not df.empty:
                frames.append(df)
        except Exception:
            pass

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="mixed",
                                      utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp", "close"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # source dedup
    prio = {s: i for i, s in enumerate(SOURCE_PRIORITY)}
    df["_p"] = df["source"].map(lambda s: prio.get(s, 99))
    df = df.sort_values("_p").drop_duplicates(subset=["timestamp"], keep="first")
    df = df.drop(columns=["_p"])

    df = df.sort_values("timestamp").tail(n_bars).reset_index(drop=True)
    return df


# ─────────────────────────────────────────────
#  INDICATOR CONTEXT
#  Builds a dict of named values for condition evaluation.
# ─────────────────────────────────────────────

def _rsi(series: pd.Series, w: int) -> pd.Series:
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / w, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / w, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _sma(series: pd.Series, w: int) -> pd.Series:
    return series.rolling(w).mean()


def _ema(series: pd.Series, w: int) -> pd.Series:
    return series.ewm(span=w, adjust=False).mean()


def build_context(df: pd.DataFrame) -> dict:
    """
    Compute all available indicators from the price series.
    Returns a flat dict of name → latest value, suitable for eval().
    """
    if df.empty or len(df) < 2:
        return {}

    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"]
    ctx    = {}

    # ── price ─────────────────────────────────────────────────────────────────
    ctx["price"]         = float(close.iloc[-1])
    ctx["open"]          = float(df["open"].iloc[-1])
    ctx["high_today"]    = float(high.iloc[-1])
    ctx["low_today"]     = float(low.iloc[-1])
    ctx["volume"]        = float(volume.iloc[-1])
    ctx["prev_close"]    = float(close.iloc[-2])
    ctx["change_pct"]    = float((close.iloc[-1] / close.iloc[-2] - 1) * 100)

    # ── moving averages ───────────────────────────────────────────────────────
    for w in [5, 10, 20, 50, 100, 200]:
        s = _sma(close, w)
        ctx[f"sma{w}"] = float(s.iloc[-1]) if not np.isnan(s.iloc[-1]) else None
        e = _ema(close, w)
        ctx[f"ema{w}"] = float(e.iloc[-1]) if not np.isnan(e.iloc[-1]) else None

    # ── RSI ───────────────────────────────────────────────────────────────────
    for w in [7, 9, 14, 21]:
        r = _rsi(close, w)
        ctx[f"rsi{w}"] = float(r.iloc[-1]) if not np.isnan(r.iloc[-1]) else None

    # ── MACD ──────────────────────────────────────────────────────────────────
    macd_line   = _ema(close, 12) - _ema(close, 26)
    signal_line = _ema(macd_line, 9)
    ctx["macd"]        = float(macd_line.iloc[-1])
    ctx["macd_signal"] = float(signal_line.iloc[-1])
    ctx["macd_hist"]   = float(macd_line.iloc[-1] - signal_line.iloc[-1])

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    for w in [20]:
        mid   = _sma(close, w)
        std   = close.rolling(w).std()
        upper = mid + 2 * std
        lower = mid - 2 * std
        bw    = ((upper - lower) / mid * 100).dropna()
        pct_b = (close - lower) / (upper - lower)

        ctx["bbands_upper"]   = float(upper.iloc[-1]) if upper.iloc[-1] is not np.nan else None
        ctx["bbands_mid"]     = float(mid.iloc[-1])
        ctx["bbands_lower"]   = float(lower.iloc[-1]) if lower.iloc[-1] is not np.nan else None
        ctx["bbands_pct_b"]   = float(pct_b.iloc[-1]) if not np.isnan(pct_b.iloc[-1]) else None
        ctx["bbands_bw"]      = float(bw.iloc[-1]) if len(bw) > 0 else None
        # squeeze: bandwidth in bottom 20th percentile
        ctx["bbands_squeeze"] = bool(len(bw) > 20 and bw.iloc[-1] < bw.quantile(0.2))

    # ── 52-week high/low ──────────────────────────────────────────────────────
    tail_52 = close.tail(252)
    ctx["high_52w"] = float(tail_52.max())
    ctx["low_52w"]  = float(tail_52.min())
    ctx["near_52w_high"] = bool(ctx["price"] >= ctx["high_52w"] * 0.95)
    ctx["near_52w_low"]  = bool(ctx["price"] <= ctx["low_52w"]  * 1.05)

    # ── volume spike ─────────────────────────────────────────────────────────
    avg_vol = volume.rolling(20).mean()
    ctx["volume_avg20"]  = float(avg_vol.iloc[-1]) if not np.isnan(avg_vol.iloc[-1]) else None
    ctx["volume_spike"]  = bool(
        ctx["volume_avg20"] is not None and
        ctx["volume"] > ctx["volume_avg20"] * 2
    )

    return ctx


# ─────────────────────────────────────────────
#  CONDITION EVALUATION
# ─────────────────────────────────────────────

SAFE_BUILTINS = {"abs": abs, "round": round, "min": min, "max": max,
                 "True": True, "False": False, "None": None}


def evaluate_condition(condition: str, ctx: dict) -> bool | None:
    """
    Evaluate a condition string against the indicator context.
    Returns True/False, or None if a required indicator is unavailable.

    Examples:
        "rsi14 < 30"
        "price > sma200"
        "bbands_squeeze"
        "rsi14 < 30 and price > sma50"
        "change_pct < -3"
        "volume_spike"
    """
    # check for None values in any referenced indicator
    for key, val in ctx.items():
        if val is None and key in condition:
            return None   # indicator not available (not enough data)

    try:
        result = eval(condition, {"__builtins__": SAFE_BUILTINS}, ctx)
        return bool(result)
    except Exception as e:
        print(f"  [warn] Could not evaluate condition '{condition}': {e}")
        return None


# ─────────────────────────────────────────────
#  STATE MANAGEMENT  (edge detection)
# ─────────────────────────────────────────────

def load_state() -> dict:
    if STATE_PATH.exists():
        with open(STATE_PATH) as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def check_edge(state: dict, key: str, current: bool) -> bool:
    """
    Return True only if condition transitioned from False → True (rising edge).
    Updates state in place.
    """
    prev = state.get(key, {}).get("active", False)
    state[key] = {
        "active":       current,
        "last_checked": datetime.now(timezone.utc).isoformat(),
    }
    if not prev and current:
        state[key]["last_fired"] = datetime.now(timezone.utc).isoformat()
        return True
    return False


# ─────────────────────────────────────────────
#  NOTIFICATIONS
# ─────────────────────────────────────────────

def notify_console(sym: str, condition: str, ctx: dict):
    ts    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    price = ctx.get("price", "?")
    print(f"  ALERT  [{ts}]  {sym}  |  {condition}  |  price={price:.2f}")


def notify_email(sym: str, condition: str, ctx: dict):
    host   = _cfg.get("ALERT_SMTP_HOST", "")
    port   = int(_cfg.get("ALERT_SMTP_PORT", "587"))
    user   = _cfg.get("ALERT_SMTP_USER", "")
    passwd = _cfg.get("ALERT_SMTP_PASS", "")
    to     = _cfg.get("ALERT_EMAIL",     "")

    if not all([host, user, passwd, to]):
        print("  [email] Missing SMTP config in config.env — skipping email.")
        return

    price = ctx.get("price", "?")
    ts    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    body  = (
        f"Alert triggered for {sym}\n\n"
        f"Condition : {condition}\n"
        f"Price     : {price:.2f}\n"
        f"Time      : {ts}\n\n"
        "--- Indicator snapshot ---\n"
    )
    for k, v in sorted(ctx.items()):
        if v is not None:
            body += f"  {k:<20} {v}\n"

    msg          = MIMEText(body)
    msg["Subject"] = f"Stock alert: {sym} — {condition}"
    msg["From"]    = user
    msg["To"]      = to

    try:
        with smtplib.SMTP(host, port) as smtp:
            smtp.starttls()
            smtp.login(user, passwd)
            smtp.send_message(msg)
        print(f"  [email] Sent alert to {to}")
    except Exception as e:
        print(f"  [email] Failed to send: {e}")


def notify_pushover(sym: str, condition: str, ctx: dict):
    import requests as req
    user  = _cfg.get("PUSHOVER_USER_KEY",  "")
    token = _cfg.get("PUSHOVER_APP_TOKEN", "")
    if not user or not token:
        print("  [pushover] Missing PUSHOVER_USER_KEY or PUSHOVER_APP_TOKEN — skipping.")
        return

    price = ctx.get("price", "?")
    try:
        r = req.post("https://api.pushover.net/1/messages.json", data={
            "token":   token,
            "user":    user,
            "title":   f"Stock alert: {sym}",
            "message": f"{condition}\nPrice: {price:.2f}",
        }, timeout=10)
        if r.status_code == 200:
            print(f"  [pushover] Notification sent for {sym}")
        else:
            print(f"  [pushover] Failed ({r.status_code}): {r.text[:100]}")
    except Exception as e:
        print(f"  [pushover] Request failed: {e}")


def notify_slack(sym: str, condition: str, ctx: dict):
    import requests as req
    url   = _cfg.get("SLACK_WEBHOOK_URL", "")
    if not url:
        print("  [slack] Missing SLACK_WEBHOOK_URL — skipping.")
        return

    price = ctx.get("price", "?")
    ts    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    text  = f":bell: *{sym}* — `{condition}` — price: *{price:.2f}* — {ts}"

    try:
        r = req.post(url, json={"text": text}, timeout=10)
        if r.status_code == 200:
            print(f"  [slack] Notification sent for {sym}")
        else:
            print(f"  [slack] Failed ({r.status_code}): {r.text[:100]}")
    except Exception as e:
        print(f"  [slack] Request failed: {e}")


NOTIFIERS = {
    "console":  notify_console,
    "email":    notify_email,
    "pushover": notify_pushover,
    "slack":    notify_slack,
}


def fire_alert(sym: str, condition: str, ctx: dict, channels: list[str]):
    for ch in channels:
        fn = NOTIFIERS.get(ch)
        if fn:
            fn(sym, condition, ctx)
        else:
            print(f"  [warn] Unknown notification channel: {ch}")


# ─────────────────────────────────────────────
#  LIST CONDITIONS HELP
# ─────────────────────────────────────────────

def list_conditions():
    print("""
Available indicator names for use in --when conditions:
════════════════════════════════════════════════════════

Price
  price          Current close price
  open           Today's open
  high_today     Today's high
  low_today      Today's low
  prev_close     Previous close
  change_pct     % change from previous close
  volume         Today's volume

Moving averages  (replace N with 5, 10, 20, 50, 100, or 200)
  smaN           Simple moving average, N-bar
  emaN           Exponential moving average, N-bar

RSI  (replace N with 7, 9, 14, or 21)
  rsiN           RSI value (0–100)

MACD
  macd           MACD line (EMA12 − EMA26)
  macd_signal    Signal line (EMA9 of MACD)
  macd_hist      MACD histogram (macd − macd_signal)

Bollinger Bands  (20-period, ±2σ)
  bbands_upper   Upper band
  bbands_mid     Middle band (SMA20)
  bbands_lower   Lower band
  bbands_pct_b   %B: 0 = at lower band, 1 = at upper band
  bbands_bw      Bandwidth as % of mid
  bbands_squeeze True when bandwidth is in the bottom 20th percentile

Range
  high_52w       52-week high
  low_52w        52-week low
  near_52w_high  True when price is within 5% of 52-week high
  near_52w_low   True when price is within 5% of 52-week low

Volume
  volume_avg20   20-bar average volume
  volume_spike   True when today's volume is more than 2× the 20-bar average

Examples:
  --when "rsi14 < 30"
  --when "rsi14 > 70"
  --when "price > sma200"
  --when "price < sma50"
  --when "bbands_squeeze"
  --when "bbands_pct_b < 0"
  --when "change_pct < -3"
  --when "volume_spike"
  --when "macd_hist > 0 and rsi14 < 60"
  --when "near_52w_high"
  --when "rsi14 < 30 and price > sma200"
""")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Alert system for stock_collector.py data",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""
examples:
  python3 stock_alerts.py -s AAPL --when "rsi14 < 30"
  python3 stock_alerts.py -s AAPL MSFT --when "rsi14 < 30" --when "price > sma200"
  python3 stock_alerts.py -s AAPL --when "bbands_squeeze" --notify pushover
  python3 stock_alerts.py -s AAPL --when "change_pct < -3" --notify console email
  python3 stock_alerts.py --list-conditions
        """
    )

    parser.add_argument("-s", "--symbols", nargs="+", metavar="TICKER")
    parser.add_argument("--when", action="append", dest="conditions",
                        metavar="EXPR",
                        help="Condition to watch (can be repeated)")
    parser.add_argument("--notify", nargs="+",
                        default=["console"],
                        choices=list(NOTIFIERS),
                        metavar="CHANNEL",
                        help="Notification channels: console (default), email, pushover, slack")
    parser.add_argument("--list-conditions", action="store_true",
                        help="List all available indicator names and example conditions")
    parser.add_argument("--dry-run", action="store_true",
                        help="Evaluate conditions and print results without firing alerts "
                             "or updating state")
    parser.add_argument("--status", action="store_true",
                        help="Show current alert state (which conditions are active)")
    parser.add_argument("--reset", action="store_true",
                        help="Clear all alert state (re-enables all edges)")
    parser.add_argument("--bars", type=int, default=250,
                        help="Number of historical bars to load for indicator computation "
                             "(default: 250)")

    args = parser.parse_args()

    # ── utility modes ─────────────────────────────────────────────────────────
    if args.list_conditions:
        list_conditions()
        return

    if args.reset:
        if STATE_PATH.exists():
            STATE_PATH.unlink()
        print("Alert state cleared.")
        return

    if args.status:
        state = load_state()
        if not state:
            print("No alert state on record.")
            return
        rows = []
        for key, val in sorted(state.items()):
            active = "YES" if val.get("active") else "no"
            fired  = val.get("last_fired", "—")[:16]
            rows.append((key, active, fired))
        col_w = [max(len(h), max(len(r[i]) for r in rows))
                 for i, h in enumerate(["Condition key", "Active", "Last fired"])]
        fmt = "  ".join(f"{{:<{w}}}" for w in col_w)
        print(fmt.format("Condition key", "Active", "Last fired"))
        print("  ".join("─" * w for w in col_w))
        for row in rows:
            print(fmt.format(*row))
        return

    # ── require symbols and conditions ────────────────────────────────────────
    if not args.symbols:
        parser.error("Specify symbols with -s, or use --list-conditions / --status / --reset.")
    if not args.conditions:
        parser.error("Specify at least one condition with --when.")

    state    = load_state()
    any_fire = False

    print(f"Checking {len(args.symbols)} symbol(s), "
          f"{len(args.conditions)} condition(s)  "
          f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}]")
    print()

    for sym in args.symbols:
        df = load_series(sym, n_bars=args.bars)
        if df.empty:
            print(f"  {sym}: no data found — run stock_collector.py first.")
            continue

        ctx = build_context(df)
        if not ctx:
            print(f"  {sym}: not enough data to compute indicators.")
            continue

        latest_date = df["timestamp"].iloc[-1].strftime("%Y-%m-%d")
        print(f"  {sym}  (latest bar: {latest_date},  price: {ctx['price']:.2f})")

        for cond in args.conditions:
            result = evaluate_condition(cond, ctx)

            if result is None:
                print(f"    {cond:<40}  ⚠  indicator unavailable (need more data)")
                continue

            state_key = f"{sym}|{cond}"
            is_edge   = check_edge(state, state_key, result)

            status = "TRUE " + ("→ FIRING" if is_edge else "(already active)") \
                     if result else "false"
            print(f"    {cond:<40}  {status}")

            if is_edge and not args.dry_run:
                any_fire = True
                fire_alert(sym, cond, ctx, args.notify)

        print()

    if not args.dry_run:
        save_state(state)

    if not any_fire and not args.dry_run:
        print("No new alerts fired.")


if __name__ == "__main__":
    main()
