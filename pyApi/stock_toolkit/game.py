"""
stock_toolkit.game
==================
Paper-trading portfolios: virtual cash, fractional-share buy/sell,
mark-to-market against the latest close in your collected
stock_data.db. No real money, no broker API.

State lives in $STOCK_DIR/portfolio.db. v2 schema (since 1.1.0) supports
multiple named strategies; one is "active" at a time. The previous
single-portfolio DB layout is migrated transparently on first open.

Schema (v2):

  portfolios(id, name UNIQUE, starting_cash, cash, created_at,
             last_reset_at, archived_at)
  trades(id, portfolio_id FK→portfolios, timestamp, symbol, side,
         qty, price, fill_price, cash_delta)
  meta(key, value)        -- meta('active_portfolio_id', '1')

Pricing model (deliberately simple):
  buy  → latest close × 1.001  (0.1% slippage premium)
  sell → latest close × 0.999  (0.1% slippage discount)
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
#  Errors
# ─────────────────────────────────────────────────────────────────────────────

class GameError(RuntimeError):
    """Raised on invalid actions (no active portfolio, unknown symbol, no
    price, insufficient cash, oversell, duplicate name). UI catches and
    renders inline."""


# ─────────────────────────────────────────────────────────────────────────────
#  Connection + migration
# ─────────────────────────────────────────────────────────────────────────────

_NEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolios (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL UNIQUE,
    starting_cash REAL    NOT NULL,
    cash          REAL    NOT NULL,
    created_at    TEXT    NOT NULL,
    last_reset_at TEXT    NOT NULL,
    archived_at   TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    timestamp    TEXT    NOT NULL,
    symbol       TEXT    NOT NULL,
    side         TEXT    NOT NULL CHECK (side IN ('buy', 'sell')),
    qty          REAL    NOT NULL,
    price        REAL    NOT NULL,
    fill_price   REAL    NOT NULL,
    cash_delta   REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trades_ts        ON trades (timestamp);
CREATE INDEX IF NOT EXISTS idx_trades_symbol    ON trades (symbol);
CREATE INDEX IF NOT EXISTS idx_trades_portfolio ON trades (portfolio_id);
"""


def _migrate_to_v2(con: sqlite3.Connection) -> None:
    """If the DB is the v1 single-portfolio layout, transform it in place:
    rename portfolio→portfolios with name='Default', add portfolio_id FK
    to trades, mark Default as active. No-op on fresh or already-v2 DBs."""
    has_new = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='portfolios'"
    ).fetchone() is not None
    if has_new:
        return

    has_old = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='portfolio'"
    ).fetchone() is not None
    if not has_old:
        return        # fresh DB; the schema CREATE IF NOT EXISTS will handle it

    # Build the v2 portfolios + meta tables alongside the legacy ones.
    con.executescript("""
        CREATE TABLE portfolios (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL UNIQUE,
            starting_cash REAL    NOT NULL,
            cash          REAL    NOT NULL,
            created_at    TEXT    NOT NULL,
            last_reset_at TEXT    NOT NULL,
            archived_at   TEXT
        );
        CREATE TABLE meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)

    rows = con.execute(
        "SELECT starting_cash, cash, created_at, last_reset_at FROM portfolio"
    ).fetchall()
    for row in rows:
        con.execute(
            "INSERT INTO portfolios (id, name, starting_cash, cash, "
            "created_at, last_reset_at) VALUES (1, 'Default', ?, ?, ?, ?)",
            row,
        )
    if rows:
        con.execute(
            "INSERT INTO meta (key, value) VALUES ('active_portfolio_id', '1')"
        )

    # Re-shape trades to carry portfolio_id (default 1 for legacy rows).
    cols = [r[1] for r in con.execute("PRAGMA table_info(trades)").fetchall()]
    if cols and "portfolio_id" not in cols:
        con.executescript("""
            ALTER TABLE trades RENAME TO trades_old;
            CREATE TABLE trades (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
                timestamp    TEXT    NOT NULL,
                symbol       TEXT    NOT NULL,
                side         TEXT    NOT NULL CHECK (side IN ('buy', 'sell')),
                qty          REAL    NOT NULL,
                price        REAL    NOT NULL,
                fill_price   REAL    NOT NULL,
                cash_delta   REAL    NOT NULL
            );
            INSERT INTO trades
                (portfolio_id, timestamp, symbol, side, qty, price,
                 fill_price, cash_delta)
            SELECT 1, timestamp, symbol, side, qty, price, fill_price,
                   cash_delta FROM trades_old ORDER BY id;
            DROP TABLE trades_old;
        """)

    con.execute("DROP TABLE portfolio")
    con.commit()


def _connect(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    _migrate_to_v2(con)
    con.executescript(_NEW_SCHEMA)
    return con


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")


# ─────────────────────────────────────────────────────────────────────────────
#  Active portfolio resolution
# ─────────────────────────────────────────────────────────────────────────────

def _get_active_id(con: sqlite3.Connection) -> int | None:
    row = con.execute(
        "SELECT value FROM meta WHERE key = 'active_portfolio_id'"
    ).fetchone()
    return int(row[0]) if row else None


def _set_active_id(con: sqlite3.Connection, pid: int) -> None:
    con.execute(
        "INSERT INTO meta (key, value) VALUES ('active_portfolio_id', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(pid),),
    )


def _resolve_pid(con: sqlite3.Connection, portfolio_id: int | None) -> int:
    pid = portfolio_id if portfolio_id is not None else _get_active_id(con)
    if pid is None:
        raise GameError(
            "No active portfolio. Create one with create_portfolio().")
    return pid


def get_active_portfolio_id(db: Path = None) -> int | None:
    db = db or DEFAULT_PORTFOLIO_DB
    con = _connect(db); pid = _get_active_id(con); con.close()
    return pid


def set_active_portfolio(portfolio_id: int, db: Path = None) -> None:
    db = db or DEFAULT_PORTFOLIO_DB
    con = _connect(db)
    if con.execute("SELECT 1 FROM portfolios WHERE id = ?",
                   (portfolio_id,)).fetchone() is None:
        con.close()
        raise GameError(f"No portfolio with id {portfolio_id}.")
    _set_active_id(con, portfolio_id); con.commit(); con.close()


# ─────────────────────────────────────────────────────────────────────────────
#  Portfolio lifecycle — create, list, rename, archive, delete
# ─────────────────────────────────────────────────────────────────────────────

def list_portfolios(include_archived: bool = False, db: Path = None) -> list:
    db = db or DEFAULT_PORTFOLIO_DB
    con = _connect(db)
    where = "" if include_archived else " WHERE archived_at IS NULL"
    rows = con.execute(
        f"SELECT id, name, starting_cash, cash, created_at, last_reset_at, "
        f"archived_at FROM portfolios{where} ORDER BY id"
    ).fetchall()
    con.close()
    keys = ("id", "name", "starting_cash", "cash", "created_at",
            "last_reset_at", "archived_at")
    return [dict(zip(keys, r)) for r in rows]


def create_portfolio(name: str,
                     starting_cash: float = DEFAULT_STARTING_CASH,
                     db: Path = None,
                     activate: bool = True) -> dict:
    """Create a new portfolio. Optionally make it the active one."""
    name = (name or "").strip()
    if not name:
        raise GameError("Portfolio name must be non-empty.")
    if starting_cash <= 0:
        raise GameError("Starting cash must be positive.")
    db = db or DEFAULT_PORTFOLIO_DB
    con = _connect(db)
    ts = _now()
    try:
        cur = con.execute(
            "INSERT INTO portfolios (name, starting_cash, cash, created_at, "
            "last_reset_at) VALUES (?, ?, ?, ?, ?)",
            (name, starting_cash, starting_cash, ts, ts),
        )
    except sqlite3.IntegrityError:
        con.close()
        raise GameError(f"A portfolio named {name!r} already exists.")
    pid = cur.lastrowid
    if activate:
        _set_active_id(con, pid)
    con.commit(); con.close()
    return get_portfolio(portfolio_id=pid, db=db)


def rename_portfolio(portfolio_id: int, new_name: str,
                     db: Path = None) -> None:
    new_name = (new_name or "").strip()
    if not new_name:
        raise GameError("New name must be non-empty.")
    db = db or DEFAULT_PORTFOLIO_DB
    con = _connect(db)
    try:
        con.execute("UPDATE portfolios SET name = ? WHERE id = ?",
                    (new_name, portfolio_id))
    except sqlite3.IntegrityError:
        con.close()
        raise GameError(f"A portfolio named {new_name!r} already exists.")
    con.commit(); con.close()


def archive_portfolio(portfolio_id: int, db: Path = None) -> None:
    """Soft-archive a portfolio: it's hidden from `list_portfolios()` by
    default but its trades are preserved. If it was active, the active
    pointer is moved to the next available portfolio (or cleared)."""
    db = db or DEFAULT_PORTFOLIO_DB
    con = _connect(db)
    con.execute("UPDATE portfolios SET archived_at = ? WHERE id = ?",
                (_now(), portfolio_id))
    if _get_active_id(con) == portfolio_id:
        nxt = con.execute(
            "SELECT id FROM portfolios "
            "WHERE archived_at IS NULL AND id != ? ORDER BY id LIMIT 1",
            (portfolio_id,),
        ).fetchone()
        if nxt:
            _set_active_id(con, nxt[0])
        else:
            con.execute(
                "DELETE FROM meta WHERE key = 'active_portfolio_id'")
    con.commit(); con.close()


def unarchive_portfolio(portfolio_id: int, db: Path = None) -> None:
    db = db or DEFAULT_PORTFOLIO_DB
    con = _connect(db)
    con.execute("UPDATE portfolios SET archived_at = NULL WHERE id = ?",
                (portfolio_id,))
    con.commit(); con.close()


def delete_portfolio(portfolio_id: int, db: Path = None) -> None:
    """Hard-delete a portfolio and its trades (cascade). Irreversible."""
    db = db or DEFAULT_PORTFOLIO_DB
    con = _connect(db)
    con.execute("DELETE FROM portfolios WHERE id = ?", (portfolio_id,))
    if _get_active_id(con) == portfolio_id:
        nxt = con.execute(
            "SELECT id FROM portfolios WHERE archived_at IS NULL "
            "ORDER BY id LIMIT 1"
        ).fetchone()
        if nxt:
            _set_active_id(con, nxt[0])
        else:
            con.execute(
                "DELETE FROM meta WHERE key = 'active_portfolio_id'")
    con.commit(); con.close()


# ─────────────────────────────────────────────────────────────────────────────
#  init_portfolio — backward-compat wrapper
# ─────────────────────────────────────────────────────────────────────────────

def init_portfolio(starting_cash: float = DEFAULT_STARTING_CASH,
                   db: Path = None) -> dict:
    """If no portfolios exist yet, create one named 'Default' and make it
    active. If an active portfolio already exists, return it untouched.
    Idempotent — safe to call on every page load."""
    db = db or DEFAULT_PORTFOLIO_DB
    con = _connect(db)
    pid = _get_active_id(con)
    if pid is not None:
        con.close()
        return get_portfolio(portfolio_id=pid, db=db)
    any_existing = con.execute(
        "SELECT id FROM portfolios WHERE archived_at IS NULL ORDER BY id LIMIT 1"
    ).fetchone()
    if any_existing:
        _set_active_id(con, any_existing[0])
        con.commit(); con.close()
        return get_portfolio(portfolio_id=any_existing[0], db=db)
    con.close()
    return create_portfolio("Default", starting_cash=starting_cash, db=db)


# ─────────────────────────────────────────────────────────────────────────────
#  Read state
# ─────────────────────────────────────────────────────────────────────────────

def get_portfolio(portfolio_id: int = None, db: Path = None) -> dict:
    db = db or DEFAULT_PORTFOLIO_DB
    con = _connect(db)
    pid = _resolve_pid(con, portfolio_id)
    row = con.execute(
        "SELECT id, name, starting_cash, cash, created_at, last_reset_at, "
        "archived_at FROM portfolios WHERE id = ?",
        (pid,),
    ).fetchone()
    con.close()
    if not row:
        return {}
    return {
        "id":            row[0],
        "name":          row[1],
        "starting_cash": row[2],
        "cash":          row[3],
        "created_at":    row[4],
        "last_reset_at": row[5],
        "archived_at":   row[6],
    }


def get_trades(portfolio_id: int = None, db: Path = None) -> list:
    db = db or DEFAULT_PORTFOLIO_DB
    con = _connect(db)
    pid = _resolve_pid(con, portfolio_id)
    rows = con.execute(
        "SELECT timestamp, symbol, side, qty, price, fill_price, cash_delta "
        "FROM trades WHERE portfolio_id = ? ORDER BY id",
        (pid,),
    ).fetchall()
    con.close()
    return [
        {"timestamp": r[0], "symbol": r[1], "side": r[2], "qty": r[3],
         "price": r[4], "fill_price": r[5], "cash_delta": r[6]}
        for r in rows
    ]


def get_positions(portfolio_id: int = None, db: Path = None) -> dict:
    """Derived from the trade log of one portfolio. {symbol: {qty,
    avg_cost, total_cost}}. Closed positions (qty == 0) are omitted."""
    trades = get_trades(portfolio_id=portfolio_id, db=db)
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
        else:
            sold = min(t["qty"], p["qty"])
            p["qty"]       -= sold
            p["total_cost"] -= sold * p["avg_cost"]
            if p["qty"] <= 1e-9:
                p["qty"], p["avg_cost"], p["total_cost"] = 0.0, 0.0, 0.0
    return {s: p for s, p in pos.items() if p["qty"] > 0}


# ─────────────────────────────────────────────────────────────────────────────
#  Pricing
# ─────────────────────────────────────────────────────────────────────────────

def get_latest_price(symbol: str) -> tuple:
    """Most recent 1d close for `symbol` across every discoverable data
    DB, or (None, None) if no data."""
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
#  Trades — buy / sell / reset
# ─────────────────────────────────────────────────────────────────────────────

def _record_trade(con, pid, *, symbol, side, qty, price, fill_price,
                  cash_delta):
    con.execute(
        "INSERT INTO trades (portfolio_id, timestamp, symbol, side, qty, "
        "price, fill_price, cash_delta) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (pid, _now(), symbol.upper(), side, float(qty), float(price),
         float(fill_price), float(cash_delta)),
    )
    con.execute("UPDATE portfolios SET cash = cash + ? WHERE id = ?",
                (cash_delta, pid))


def buy(symbol: str, cash_amount: float,
        portfolio_id: int = None, db: Path = None) -> dict:
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
    pid = _resolve_pid(con, portfolio_id)
    cash = con.execute(
        "SELECT cash FROM portfolios WHERE id = ?", (pid,)
    ).fetchone()[0]
    if cash_amount > cash + 1e-6:
        con.close()
        raise GameError(
            f"Insufficient cash: have {cash:.2f}, need {cash_amount:.2f}.")
    _record_trade(con, pid, symbol=symbol, side="buy", qty=qty,
                  price=price, fill_price=fill_price,
                  cash_delta=-cash_amount)
    con.commit(); con.close()
    return {"symbol": symbol.upper(), "qty": qty, "price": price,
            "fill_price": fill_price, "as_of": as_of, "spent": cash_amount}


def sell(symbol: str, qty: float = None,
         portfolio_id: int = None, db: Path = None) -> dict:
    pos = get_positions(portfolio_id=portfolio_id, db=db).get(symbol.upper())
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
    pid = _resolve_pid(con, portfolio_id)
    _record_trade(con, pid, symbol=symbol, side="sell", qty=qty,
                  price=price, fill_price=fill_price, cash_delta=+proceeds)
    con.commit(); con.close()
    return {"symbol": symbol.upper(), "qty": qty, "price": price,
            "fill_price": fill_price, "as_of": as_of, "proceeds": proceeds}


def reset_portfolio(starting_cash: float = DEFAULT_STARTING_CASH,
                    portfolio_id: int = None, db: Path = None) -> dict:
    """Wipe all trades for ONE portfolio (the active one by default) and
    reset its cash. Other portfolios are untouched."""
    db = db or DEFAULT_PORTFOLIO_DB
    con = _connect(db)
    pid = _resolve_pid(con, portfolio_id)
    ts = _now()
    con.execute("DELETE FROM trades WHERE portfolio_id = ?", (pid,))
    con.execute(
        "UPDATE portfolios SET starting_cash = ?, cash = ?, last_reset_at = ? "
        "WHERE id = ?",
        (starting_cash, starting_cash, ts, pid),
    )
    con.commit(); con.close()
    return get_portfolio(portfolio_id=pid, db=db)


# ─────────────────────────────────────────────────────────────────────────────
#  Mark-to-market view
# ─────────────────────────────────────────────────────────────────────────────

def mark_to_market(portfolio_id: int = None, db: Path = None) -> dict:
    """Current value of one portfolio plus per-position P/L."""
    p = get_portfolio(portfolio_id=portfolio_id, db=db)
    if not p:
        return {}
    positions = get_positions(portfolio_id=p["id"], db=db)
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
            "symbol": sym, "qty": info["qty"], "avg_cost": info["avg_cost"],
            "price":  cur_price, "as_of": as_of, "value": value,
            "pnl":    pnl, "pnl_pct": pnl_pct,
        })
    total = p["cash"] + equity
    total_return_pct = (total / p["starting_cash"] - 1) * 100
    return {
        "id":               p["id"],
        "name":             p["name"],
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
#  Daily value history (chart)
# ─────────────────────────────────────────────────────────────────────────────

def value_history(portfolio_id: int = None, db: Path = None) -> list:
    """Daily total-value series for one portfolio, from inception to today."""
    import pandas as pd

    p = get_portfolio(portfolio_id=portfolio_id, db=db)
    if not p:
        return []
    trades = get_trades(portfolio_id=p["id"], db=db)
    start  = datetime.date.fromisoformat(p["created_at"][:10])
    today  = datetime.date.today()

    symbols = sorted({t["symbol"] for t in trades})
    closes  = {}
    for sym in symbols:
        try:
            data_dbs = _discover_data_dbs()
        except Exception:
            data_dbs = []
        s = pd.Series(dtype=float)
        for d in data_dbs:
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

    cash      = p["starting_cash"]
    qty       = defaultdict(float)
    trade_idx = 0
    history   = []
    cur_date  = start
    while cur_date <= today:
        while (trade_idx < len(trades)
               and trades[trade_idx]["timestamp"][:10] <= cur_date.isoformat()):
            t = trades[trade_idx]
            if t["side"] == "buy":
                qty[t["symbol"]] += t["qty"]
            else:
                qty[t["symbol"]] -= t["qty"]
            cash += t["cash_delta"]
            trade_idx += 1
        equity = 0.0
        for sym, q in qty.items():
            if q <= 0:
                continue
            series = closes.get(sym)
            if series is None or series.empty:
                continue
            valid = series[series.index <= cur_date]
            if valid.empty:
                continue
            equity += q * float(valid.iloc[-1])
        history.append({"date": cur_date.isoformat(),
                        "cash": cash, "equity": equity,
                        "total": cash + equity})
        cur_date += datetime.timedelta(days=1)
    return history
