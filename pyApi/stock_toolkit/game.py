"""
stock_toolkit.game
==================
Paper-trading portfolio: virtual cash, fractional-share buy/sell, mark-
to-market against the latest close in your collected stock_data.db.

This is the "game" — no real money, no broker API, no orders sent
anywhere. It's the bridge between the analytical tools and learning by
doing: read the briefing, place virtual trades, come back in a week or
a month, see how it would have played out.

State lives in $STOCK_DIR/portfolio.db (one portfolio, persistent).
Operations are append-only at the trade level; positions are derived
from the trade log so the audit trail is intact.

Pricing model (deliberately simple for v1):
  buy  → latest close × 1.001   (0.1% slippage premium)
  sell → latest close × 0.999   (0.1% slippage discount)
  no commission, no overnight fees, no shorting
"""

import datetime
import sqlite3
from collections import defaultdict
from pathlib import Path

from stock_toolkit.analysis import discover_dbs as _discover_data_dbs
from stock_toolkit.common import BASE_DIR

SLIPPAGE_BPS = 10                                # 10 bps = 0.10%
SLIPPAGE     = SLIPPAGE_BPS / 10000.0
DEFAULT_PORTFOLIO_DB = BASE_DIR / "portfolio.db"
DEFAULT_STARTING_CASH = 10_000.0


# ─────────────────────────────────────────────────────────────────────────────
#  Schema + connection
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolio (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    starting_cash   REAL    NOT NULL,
    cash            REAL    NOT NULL,
    created_at      TEXT    NOT NULL,
    last_reset_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  TEXT    NOT NULL,
    symbol     TEXT    NOT NULL,
    side       TEXT    NOT NULL CHECK (side IN ('buy', 'sell')),
    qty        REAL    NOT NULL,
    price      REAL    NOT NULL,
    fill_price REAL    NOT NULL,
    cash_delta REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trades_ts     ON trades (timestamp);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades (symbol);
"""


def _connect(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db)
    con.executescript(_SCHEMA)
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")


# ─────────────────────────────────────────────────────────────────────────────
#  Portfolio init / reset
# ─────────────────────────────────────────────────────────────────────────────

def init_portfolio(starting_cash: float = DEFAULT_STARTING_CASH,
                   db: Path = None) -> dict:
    """Create the portfolio row if missing. No-op if it already exists.
    Use `reset_portfolio` to start over with a new balance."""
    db = db or DEFAULT_PORTFOLIO_DB
    con = _connect(db)
    cur = con.execute("SELECT id FROM portfolio WHERE id = 1")
    if cur.fetchone() is None:
        ts = _now()
        con.execute(
            "INSERT INTO portfolio (id, starting_cash, cash, created_at, "
            "last_reset_at) VALUES (1, ?, ?, ?, ?)",
            (starting_cash, starting_cash, ts, ts),
        )
        con.commit()
    con.close()
    return get_portfolio(db)


def reset_portfolio(starting_cash: float = DEFAULT_STARTING_CASH,
                    db: Path = None) -> dict:
    """Wipe all trades and reset cash to `starting_cash`. Irreversible."""
    db = db or DEFAULT_PORTFOLIO_DB
    con = _connect(db)
    ts = _now()
    con.execute("DELETE FROM trades")
    con.execute(
        "INSERT INTO portfolio (id, starting_cash, cash, created_at, last_reset_at) "
        "VALUES (1, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET starting_cash = excluded.starting_cash, "
        "cash = excluded.cash, last_reset_at = excluded.last_reset_at",
        (starting_cash, starting_cash, ts, ts),
    )
    con.commit()
    con.close()
    return get_portfolio(db)


# ─────────────────────────────────────────────────────────────────────────────
#  Read state
# ─────────────────────────────────────────────────────────────────────────────

def get_portfolio(db: Path = None) -> dict:
    db = db or DEFAULT_PORTFOLIO_DB
    con = _connect(db)
    row = con.execute(
        "SELECT starting_cash, cash, created_at, last_reset_at "
        "FROM portfolio WHERE id = 1"
    ).fetchone()
    con.close()
    if row is None:
        return {}
    return {
        "starting_cash": row[0],
        "cash":          row[1],
        "created_at":    row[2],
        "last_reset_at": row[3],
    }


def get_trades(db: Path = None) -> list:
    db = db or DEFAULT_PORTFOLIO_DB
    con = _connect(db)
    rows = con.execute(
        "SELECT timestamp, symbol, side, qty, price, fill_price, cash_delta "
        "FROM trades ORDER BY id"
    ).fetchall()
    con.close()
    return [
        {"timestamp": r[0], "symbol": r[1], "side": r[2], "qty": r[3],
         "price": r[4], "fill_price": r[5], "cash_delta": r[6]}
        for r in rows
    ]


def get_positions(db: Path = None) -> dict:
    """Derived from the trade log.
    Returns {symbol: {qty, avg_cost, total_cost}}.
    Closed positions (qty == 0) are omitted.
    Uses weighted-average cost basis."""
    trades = get_trades(db)
    pos = defaultdict(lambda: {"qty": 0.0, "avg_cost": 0.0, "total_cost": 0.0})
    for t in trades:
        sym = t["symbol"]
        p   = pos[sym]
        if t["side"] == "buy":
            new_qty   = p["qty"] + t["qty"]
            new_total = p["total_cost"] + t["qty"] * t["fill_price"]
            p["qty"]        = new_qty
            p["total_cost"] = new_total
            p["avg_cost"]   = new_total / new_qty if new_qty > 0 else 0.0
        else:  # sell
            # avg_cost unchanged; reduce qty and proportional total_cost
            sold = min(t["qty"], p["qty"])
            p["qty"]       -= sold
            p["total_cost"] -= sold * p["avg_cost"]
            if p["qty"] <= 1e-9:
                p["qty"], p["avg_cost"], p["total_cost"] = 0.0, 0.0, 0.0
    return {s: p for s, p in pos.items() if p["qty"] > 0}


# ─────────────────────────────────────────────────────────────────────────────
#  Pricing — latest close from any data DB the toolkit can read
# ─────────────────────────────────────────────────────────────────────────────

def get_latest_price(symbol: str) -> tuple:
    """Return (price, as_of_iso) — the most recent 1d close across every
    DB discover_dbs() finds, or (None, None) if no data."""
    best = (None, None)
    try:
        dbs = _discover_data_dbs()
    except Exception:
        return best
    for db in dbs:
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        except sqlite3.OperationalError:
            con = sqlite3.connect(db)
        try:
            row = con.execute(
                "SELECT close, timestamp FROM prices "
                "WHERE symbol = ? AND interval = '1d' "
                "ORDER BY timestamp DESC LIMIT 1",
                (symbol.upper(),),
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
        con.close()
        if row and row[0] is not None:
            if best[1] is None or row[1] > best[1]:
                best = (float(row[0]), row[1])
    return best


# ─────────────────────────────────────────────────────────────────────────────
#  Trades — buy / sell
# ─────────────────────────────────────────────────────────────────────────────

class GameError(RuntimeError):
    """Raised on invalid trades (unknown symbol, no price, insufficient
    cash, oversell). UI catches these to render an inline error."""


def _record_trade(con, *, symbol, side, qty, price, fill_price, cash_delta):
    con.execute(
        "INSERT INTO trades (timestamp, symbol, side, qty, price, "
        "fill_price, cash_delta) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (_now(), symbol.upper(), side, float(qty), float(price),
         float(fill_price), float(cash_delta)),
    )
    con.execute("UPDATE portfolio SET cash = cash + ? WHERE id = 1",
                (cash_delta,))


def buy(symbol: str, cash_amount: float, db: Path = None) -> dict:
    """Spend `cash_amount` on fractional shares of `symbol` at the latest
    close × (1 + slippage). Raises GameError on failure."""
    if cash_amount <= 0:
        raise GameError("Buy amount must be positive.")
    price, as_of = get_latest_price(symbol)
    if price is None:
        raise GameError(
            f"No price for {symbol.upper()} in stock_data.db. "
            "Run stock-collect or stock-bootstrap first.")

    fill_price = price * (1 + SLIPPAGE)
    qty        = cash_amount / fill_price

    db = db or DEFAULT_PORTFOLIO_DB
    con = _connect(db)
    cur = con.execute("SELECT cash FROM portfolio WHERE id = 1")
    cash = cur.fetchone()[0]
    if cash_amount > cash + 1e-6:
        con.close()
        raise GameError(
            f"Insufficient cash: have {cash:.2f}, need {cash_amount:.2f}.")
    _record_trade(con, symbol=symbol, side="buy", qty=qty, price=price,
                  fill_price=fill_price, cash_delta=-cash_amount)
    con.commit(); con.close()
    return {"symbol": symbol.upper(), "qty": qty, "price": price,
            "fill_price": fill_price, "as_of": as_of, "spent": cash_amount}


def sell(symbol: str, qty: float = None, db: Path = None) -> dict:
    """Sell `qty` shares of `symbol` at the latest close × (1 - slippage).
    `qty=None` sells the full position. Raises GameError on failure."""
    pos = get_positions(db).get(symbol.upper())
    if not pos:
        raise GameError(f"No open position in {symbol.upper()}.")
    if qty is None:
        qty = pos["qty"]
    if qty <= 0:
        raise GameError("Sell quantity must be positive.")
    if qty > pos["qty"] + 1e-9:
        raise GameError(
            f"Can't sell {qty:.4f} {symbol.upper()} — only hold "
            f"{pos['qty']:.4f}.")

    price, as_of = get_latest_price(symbol)
    if price is None:
        raise GameError(f"No price for {symbol.upper()}.")
    fill_price = price * (1 - SLIPPAGE)
    proceeds   = qty * fill_price

    db = db or DEFAULT_PORTFOLIO_DB
    con = _connect(db)
    _record_trade(con, symbol=symbol, side="sell", qty=qty, price=price,
                  fill_price=fill_price, cash_delta=+proceeds)
    con.commit(); con.close()
    return {"symbol": symbol.upper(), "qty": qty, "price": price,
            "fill_price": fill_price, "as_of": as_of, "proceeds": proceeds}


# ─────────────────────────────────────────────────────────────────────────────
#  Mark-to-market view
# ─────────────────────────────────────────────────────────────────────────────

def mark_to_market(db: Path = None) -> dict:
    """Compute current portfolio value and per-position P/L."""
    p   = get_portfolio(db)
    if not p:
        return {}
    positions = get_positions(db)

    holdings = []
    equity   = 0.0
    for sym, info in positions.items():
        price, as_of = get_latest_price(sym)
        cur_price = price if price is not None else info["avg_cost"]
        value     = info["qty"] * cur_price
        pnl       = value - info["total_cost"]
        pnl_pct   = (cur_price / info["avg_cost"] - 1) * 100 \
                    if info["avg_cost"] > 0 else 0.0
        equity   += value
        holdings.append({
            "symbol":    sym,
            "qty":       info["qty"],
            "avg_cost":  info["avg_cost"],
            "price":     cur_price,
            "as_of":     as_of,
            "value":     value,
            "pnl":       pnl,
            "pnl_pct":   pnl_pct,
        })
    total = p["cash"] + equity
    total_return_pct = (total / p["starting_cash"] - 1) * 100
    return {
        "cash":             p["cash"],
        "equity":           equity,
        "total":            total,
        "starting_cash":    p["starting_cash"],
        "total_pnl":        total - p["starting_cash"],
        "total_return_pct": total_return_pct,
        "holdings":         sorted(holdings,
                                   key=lambda h: h["value"], reverse=True),
        "created_at":       p["created_at"],
        "last_reset_at":    p["last_reset_at"],
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Portfolio value history (for the chart on the Game page)
# ─────────────────────────────────────────────────────────────────────────────

def value_history(db: Path = None) -> list:
    """Daily portfolio value from inception to today. List of
    {date, value, cash, equity} dicts.

    For each trading day in the range:
      holdings(t) from trades up to t × close(t) per symbol  +  cash(t)
    Days without any price data for held symbols are skipped.
    """
    import pandas as pd

    p = get_portfolio(db)
    if not p:
        return []
    trades = get_trades(db)
    start  = datetime.date.fromisoformat(p["created_at"][:10])
    today  = datetime.date.today()

    # Pre-fetch all 1d close series we'll need (symbols ever traded)
    symbols = sorted({t["symbol"] for t in trades})
    closes  = {}
    for sym in symbols:
        try:
            dbs = _discover_data_dbs()
        except Exception:
            dbs = []
        s = pd.Series(dtype=float)
        for d in dbs:
            try:
                con = sqlite3.connect(f"file:{d}?mode=ro", uri=True)
            except sqlite3.OperationalError:
                con = sqlite3.connect(d)
            try:
                rows = con.execute(
                    "SELECT timestamp, close FROM prices "
                    "WHERE symbol = ? AND interval = '1d' "
                    "AND timestamp >= ? ORDER BY timestamp",
                    (sym, start.isoformat()),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            con.close()
            if rows:
                s2 = pd.Series(
                    [r[1] for r in rows],
                    index=pd.to_datetime([r[0][:10] for r in rows]).date,
                )
                s = pd.concat([s, s2[~s2.index.isin(s.index)]])
        closes[sym] = s.sort_index() if not s.empty else s

    # Walk forward day by day, replaying trades, valuing at each day's close
    cash      = p["starting_cash"]
    qty       = defaultdict(float)
    trade_idx = 0
    history   = []
    cur_date  = start
    while cur_date <= today:
        # apply any trades dated on/before cur_date
        while (trade_idx < len(trades)
               and trades[trade_idx]["timestamp"][:10] <= cur_date.isoformat()):
            t = trades[trade_idx]
            if t["side"] == "buy":
                qty[t["symbol"]] += t["qty"]
            else:
                qty[t["symbol"]] -= t["qty"]
            cash += t["cash_delta"]
            trade_idx += 1
        # value at cur_date
        equity = 0.0
        for sym, q in qty.items():
            if q <= 0:
                continue
            series = closes.get(sym)
            if series is None or series.empty:
                continue
            # Last close on/before cur_date
            valid = series[series.index <= cur_date]
            if valid.empty:
                continue
            equity += q * float(valid.iloc[-1])
        history.append({"date": cur_date.isoformat(),
                        "cash": cash, "equity": equity,
                        "total": cash + equity})
        cur_date += datetime.timedelta(days=1)
    return history
