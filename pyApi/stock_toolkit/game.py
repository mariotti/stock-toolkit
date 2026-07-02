"""
stock_toolkit.game
==================
Paper-trading portfolios: virtual cash, fractional-share buy/sell,
mark-to-market against the latest close in your collected
stock_data.db. No real money, no broker API.

State lives in $STOCK_DIR/portfolio.db. v2 schema (since 1.1.0) supports
multiple named strategies; one is "active" at a time. The previous
single-portfolio DB layout is migrated transparently on first open.

Schema (v2 + audit_log since v2.4.0):

  portfolios(id, name UNIQUE, starting_cash, cash, created_at,
             last_reset_at, archived_at)
  trades(id, portfolio_id FK→portfolios, timestamp, symbol, side,
         qty, price, fill_price, cash_delta)
  meta(key, value)        -- meta('active_portfolio_id', '1')
  audit_log(id, timestamp, actor, op_type, target_kind, target_id,
            before_json, after_json, note)
                          -- every user + system mutation, one row each.
                          -- destructive ops (delete/reset) store the
                          -- full pre-state in before_json so the row
                          -- itself is a recovery source.

Pricing model (deliberately simple):
  buy  → latest close × 1.001  (0.1% slippage premium)
  sell → latest close × 0.999  (0.1% slippage discount)
  no commission, no overnight fees, no shorting
"""

import datetime
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from stock_toolkit.analysis import discover_dbs as _discover_data_dbs
from stock_toolkit.common import PORTFOLIO_DB as _PORTFOLIO_DB_PATH

# Public API — frozen from 2.x onwards. Anything not listed here is
# implementation detail (notably _connect, _migrate_to_v2, _resolve_pid,
# _record_trade, _audit) and may change between releases.
__all__ = [
    "GameError",
    "SLIPPAGE_BPS",
    "DEFAULT_PORTFOLIO_DB",
    "DEFAULT_STARTING_CASH",
    # portfolio lifecycle
    "get_active_portfolio_id", "set_active_portfolio",
    "list_portfolios", "create_portfolio", "rename_portfolio",
    "archive_portfolio", "unarchive_portfolio", "delete_portfolio",
    "init_portfolio", "get_portfolio", "reset_portfolio",
    # trades + positions
    "buy", "sell", "get_trades", "get_positions",
    "get_latest_price", "days_since_bar", "STALE_PRICE_DAYS",
    # analytics
    "mark_to_market", "trade_stats", "value_history",
    "benchmark_history", "risk_stats",
    # audit (v2.4.0+)
    "get_audit_log", "get_audit_event",
]

SLIPPAGE_BPS = 10                                # 10 bps = 0.10%
SLIPPAGE     = SLIPPAGE_BPS / 10000.0
DEFAULT_PORTFOLIO_DB = _PORTFOLIO_DB_PATH
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
    cash_delta   REAL    NOT NULL,
    note         TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    actor       TEXT    NOT NULL,
    op_type     TEXT    NOT NULL,
    target_kind TEXT,
    target_id   INTEGER,
    before_json TEXT,
    after_json  TEXT,
    note        TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_ts        ON trades (timestamp);
CREATE INDEX IF NOT EXISTS idx_trades_symbol    ON trades (symbol);
CREATE INDEX IF NOT EXISTS idx_trades_portfolio ON trades (portfolio_id);
CREATE INDEX IF NOT EXISTS idx_audit_ts         ON audit_log (timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_op         ON audit_log (op_type);
CREATE INDEX IF NOT EXISTS idx_audit_target     ON audit_log (target_kind, target_id);
"""


def _migrate_to_v2(con: sqlite3.Connection) -> bool:
    """If the DB is the v1 single-portfolio layout, transform it in place:
    rename portfolio→portfolios with name='Default', add portfolio_id FK
    to trades, mark Default as active. No-op on fresh or already-v2 DBs.

    Returns True if a migration actually ran (the caller writes a
    `system.schema_migrate.v1_to_v2` audit row when so).
    """
    has_new = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='portfolios'"
    ).fetchone() is not None
    if has_new:
        return False

    has_old = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='portfolio'"
    ).fetchone() is not None
    if not has_old:
        return False  # fresh DB; the schema CREATE IF NOT EXISTS will handle it

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
    return True


def _connect(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    migrated_v1 = _migrate_to_v2(con)
    # Pre-2.4.0 DBs lack the audit_log table. Detect *before* applying
    # _NEW_SCHEMA so we can write a single retro-active row noting that
    # audit started at this point (everything before is unaudited).
    audit_pre_existed = con.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='audit_log'"
    ).fetchone() is not None
    con.executescript(_NEW_SCHEMA)
    # v1.7 schema bump: add `note` column to trades if missing. SQLite
    # has no ALTER ... ADD COLUMN IF NOT EXISTS, so do it conditionally.
    cols = {r[1] for r in con.execute("PRAGMA table_info(trades)").fetchall()}
    if "note" not in cols:
        con.execute("ALTER TABLE trades ADD COLUMN note TEXT")
    if migrated_v1:
        _audit(con, actor="system",
               op_type="system.schema_migrate.v1_to_v2",
               note="Legacy single-portfolio DB transformed into v2 "
                    "(portfolios + trades.portfolio_id + meta).")
    if not audit_pre_existed:
        _audit(con, actor="system",
               op_type="system.audit_log.initialised",
               note="audit_log table created (v2.4.0+). Pre-existing rows "
                    "in portfolios / trades pre-date the audit and have "
                    "no history.")
    con.commit()
    return con


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")


# ─────────────────────────────────────────────────────────────────────────────
#  Audit log (v2.4.0+)
# ─────────────────────────────────────────────────────────────────────────────
# Internal write helper. Every mutation in this module calls _audit
# BEFORE its commit() so the audit row is atomic with the change it
# records — half-committed state is impossible.
#
# Destructive ops (delete_portfolio, reset_portfolio) snapshot the
# entire prior row(s) into before_json, so the audit log itself is the
# recovery source even after VACUUM has cleared SQLite's freelist.
#
# Convention for op_type strings:
#   portfolio.create / .rename / .archive / .unarchive / .delete /
#                    .reset / .set_active / .set_cash
#   trade.buy / .sell
#   system.schema_migrate.v1_to_v2
#   system.audit_log.initialised
#   system.auto_create_default
#
# target_kind is "portfolio", "trade", or NULL for system rows.

def _audit(con: sqlite3.Connection, *, actor: str, op_type: str,
           target_kind: str = None, target_id: int = None,
           before: dict | list = None, after: dict | list = None,
           note: str = None) -> int:
    before_json = json.dumps(before, default=str) if before is not None else None
    after_json  = json.dumps(after,  default=str) if after  is not None else None
    cur = con.execute(
        "INSERT INTO audit_log (timestamp, actor, op_type, target_kind, "
        "target_id, before_json, after_json, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (_now(), actor, op_type, target_kind, target_id,
         before_json, after_json, note),
    )
    return cur.lastrowid


def _row_to_dict(row, keys):
    return dict(zip(keys, row)) if row else None


def _pre_destructive_backup_safe(db: Path, *, op_name: str,
                                 target_id: int = None) -> Path | None:
    """Take a pre-destructive snapshot of ``db``. Returns the snapshot
    path on success, or None if disabled / unavailable.

    Failures are logged to stderr but never raised — a failed backup
    must not block the destructive op (the user explicitly asked for
    it). The audit log's ``before_json`` is the second safety net.

    Custom ``db`` paths (tests, non-default DATA_DIR) drop snapshots
    next to the DB itself (``<db.parent>/backups/pre-destructive/``),
    not next to the live PORTFOLIO_DB.
    """
    try:
        from stock_toolkit.backup import pre_destructive_snapshot as _hook
    except Exception as e:                                    # noqa: BLE001
        import sys as _sys
        print(f"[game] pre-destructive backup skipped (import "
              f"failed): {e}", file=_sys.stderr)
        return None
    backups_root = (None if db == _PORTFOLIO_DB_PATH
                    else db.parent / "backups")
    try:
        return _hook(op_name=op_name, target_id=target_id,
                     db_paths=(db,), backups_root=backups_root)
    except Exception as e:                                    # noqa: BLE001
        import sys as _sys
        print(f"[game] pre-destructive backup of {db} failed: {e}",
              file=_sys.stderr)
        return None


_PORTFOLIO_COLS = ("id", "name", "starting_cash", "cash", "created_at",
                   "last_reset_at", "archived_at")
_TRADE_COLS     = ("id", "portfolio_id", "timestamp", "symbol", "side", "qty",
                   "price", "fill_price", "cash_delta", "note")


def _snapshot_portfolio(con: sqlite3.Connection, pid: int) -> dict | None:
    row = con.execute(
        f"SELECT {', '.join(_PORTFOLIO_COLS)} FROM portfolios WHERE id = ?",
        (pid,),
    ).fetchone()
    return _row_to_dict(row, _PORTFOLIO_COLS)


def _snapshot_trades(con: sqlite3.Connection, pid: int) -> list[dict]:
    rows = con.execute(
        f"SELECT {', '.join(_TRADE_COLS)} FROM trades "
        f"WHERE portfolio_id = ? ORDER BY id",
        (pid,),
    ).fetchall()
    return [_row_to_dict(r, _TRADE_COLS) for r in rows]


def get_audit_log(portfolio_id: int = None, limit: int = None,
                  op_prefix: str = None, db: Path = None) -> list[dict]:
    """Read audit rows in reverse-chronological order.

    Filters (any combination):
      portfolio_id — only rows where target_kind='portfolio' AND target_id=pid,
                     OR rows where target_kind='trade' AND the parent
                     portfolio matches.
      op_prefix   — e.g. 'portfolio.' or 'trade.' or 'system.'
      limit       — at most N rows (newest first).
    """
    db = db or DEFAULT_PORTFOLIO_DB
    con = _connect(db)
    where  = []
    params = []
    if op_prefix:
        where.append("op_type LIKE ?")
        params.append(op_prefix + "%")
    if portfolio_id is not None:
        # Match portfolio rows directly + trade rows whose audit entry
        # referenced the trade id but carries the parent portfolio in
        # before/after json. To avoid an expensive JSON scan we only
        # match by target_kind for now; trade-on-portfolio joins land
        # in a follow-on if needed.
        where.append(
            "((target_kind='portfolio' AND target_id=?) OR "
            " (target_kind='trade' AND target_id IN "
            "  (SELECT id FROM trades WHERE portfolio_id=?)))"
        )
        params += [portfolio_id, portfolio_id]
    sql = (
        "SELECT id, timestamp, actor, op_type, target_kind, target_id, "
        "before_json, after_json, note FROM audit_log"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    rows = con.execute(sql, params).fetchall()
    con.close()
    keys = ("id", "timestamp", "actor", "op_type", "target_kind",
            "target_id", "before", "after", "note")
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        d["before"] = json.loads(d["before"]) if d["before"] else None
        d["after"]  = json.loads(d["after"])  if d["after"]  else None
        out.append(d)
    return out


def get_audit_event(audit_id: int, db: Path = None) -> dict | None:
    """Fetch one audit row by id (for a detail view)."""
    db = db or DEFAULT_PORTFOLIO_DB
    con = _connect(db)
    row = con.execute(
        "SELECT id, timestamp, actor, op_type, target_kind, target_id, "
        "before_json, after_json, note FROM audit_log WHERE id = ?",
        (audit_id,),
    ).fetchone()
    con.close()
    if not row:
        return None
    keys = ("id", "timestamp", "actor", "op_type", "target_kind",
            "target_id", "before", "after", "note")
    d = dict(zip(keys, row))
    d["before"] = json.loads(d["before"]) if d["before"] else None
    d["after"]  = json.loads(d["after"])  if d["after"]  else None
    return d


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
    prev = _get_active_id(con)
    if prev == portfolio_id:
        con.close()
        return
    _set_active_id(con, portfolio_id)
    _audit(con, actor="user", op_type="portfolio.set_active",
           target_kind="portfolio", target_id=portfolio_id,
           before={"active_portfolio_id": prev},
           after={"active_portfolio_id": portfolio_id})
    con.commit(); con.close()


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
                     activate: bool = True,
                     _actor: str = "user",
                     _audit_note: str = None) -> dict:
    """Create a new portfolio. Optionally make it the active one.

    Private hooks ``_actor`` and ``_audit_note`` let internal callers
    (e.g. ``init_portfolio``'s auto-create-Default path) override the
    audit row's actor/note. Public callers should leave them as-is.
    """
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
    except sqlite3.IntegrityError as e:
        con.close()
        raise GameError(
            f"A portfolio named {name!r} already exists."
        ) from e
    pid = cur.lastrowid
    prev_active = _get_active_id(con)
    if activate:
        _set_active_id(con, pid)
    after_snapshot = _snapshot_portfolio(con, pid)
    _audit(con, actor=_actor, op_type="portfolio.create",
           target_kind="portfolio", target_id=pid,
           after=after_snapshot, note=_audit_note)
    if activate and prev_active != pid:
        _audit(con, actor="system", op_type="portfolio.set_active",
               target_kind="portfolio", target_id=pid,
               before={"active_portfolio_id": prev_active},
               after={"active_portfolio_id": pid},
               note="activated by create_portfolio(activate=True)")
    con.commit(); con.close()
    return get_portfolio(portfolio_id=pid, db=db)


def rename_portfolio(portfolio_id: int, new_name: str,
                     db: Path = None) -> None:
    new_name = (new_name or "").strip()
    if not new_name:
        raise GameError("New name must be non-empty.")
    db = db or DEFAULT_PORTFOLIO_DB
    con = _connect(db)
    before = _snapshot_portfolio(con, portfolio_id)
    if before is None:
        con.close()
        raise GameError(f"No portfolio with id {portfolio_id}.")
    if before["name"] == new_name:
        con.close()
        return
    try:
        con.execute("UPDATE portfolios SET name = ? WHERE id = ?",
                    (new_name, portfolio_id))
    except sqlite3.IntegrityError as e:
        con.close()
        raise GameError(
            f"A portfolio named {new_name!r} already exists."
        ) from e
    after = _snapshot_portfolio(con, portfolio_id)
    _audit(con, actor="user", op_type="portfolio.rename",
           target_kind="portfolio", target_id=portfolio_id,
           before={"name": before["name"]}, after={"name": after["name"]})
    con.commit(); con.close()


def archive_portfolio(portfolio_id: int, db: Path = None) -> None:
    """Soft-archive a portfolio: it's hidden from `list_portfolios()` by
    default but its trades are preserved. If it was active, the active
    pointer is moved to the next available portfolio (or cleared)."""
    db = db or DEFAULT_PORTFOLIO_DB
    con = _connect(db)
    before = _snapshot_portfolio(con, portfolio_id)
    if before is None:
        con.close()
        raise GameError(f"No portfolio with id {portfolio_id}.")
    con.execute("UPDATE portfolios SET archived_at = ? WHERE id = ?",
                (_now(), portfolio_id))
    after = _snapshot_portfolio(con, portfolio_id)
    _audit(con, actor="user", op_type="portfolio.archive",
           target_kind="portfolio", target_id=portfolio_id,
           before={"archived_at": before["archived_at"]},
           after={"archived_at": after["archived_at"]})
    if _get_active_id(con) == portfolio_id:
        nxt = con.execute(
            "SELECT id FROM portfolios "
            "WHERE archived_at IS NULL AND id != ? ORDER BY id LIMIT 1",
            (portfolio_id,),
        ).fetchone()
        if nxt:
            _set_active_id(con, nxt[0])
            _audit(con, actor="system", op_type="portfolio.set_active",
                   target_kind="portfolio", target_id=nxt[0],
                   before={"active_portfolio_id": portfolio_id},
                   after={"active_portfolio_id": nxt[0]},
                   note="active rolled over after archive")
        else:
            con.execute(
                "DELETE FROM meta WHERE key = 'active_portfolio_id'")
            _audit(con, actor="system", op_type="portfolio.set_active",
                   before={"active_portfolio_id": portfolio_id},
                   after={"active_portfolio_id": None},
                   note="no remaining portfolios; active cleared after archive")
    con.commit(); con.close()


def unarchive_portfolio(portfolio_id: int, db: Path = None) -> None:
    db = db or DEFAULT_PORTFOLIO_DB
    con = _connect(db)
    before = _snapshot_portfolio(con, portfolio_id)
    if before is None:
        con.close()
        raise GameError(f"No portfolio with id {portfolio_id}.")
    if before["archived_at"] is None:
        con.close()
        return
    con.execute("UPDATE portfolios SET archived_at = NULL WHERE id = ?",
                (portfolio_id,))
    _audit(con, actor="user", op_type="portfolio.unarchive",
           target_kind="portfolio", target_id=portfolio_id,
           before={"archived_at": before["archived_at"]},
           after={"archived_at": None})
    con.commit(); con.close()


def delete_portfolio(portfolio_id: int, db: Path = None) -> None:
    """Hard-delete a portfolio and its trades (cascade). Irreversible from
    the live tables — but TWO recovery sources remain:

      1. ``audit_log.before_json`` carries the full portfolio row + all
         its cascaded trades, in the same transaction as the delete.
      2. A pre-destructive snapshot of ``portfolio.db`` is auto-taken
         under ``data/backups/pre-destructive/`` before the delete runs
         (opt-out via ``AUTO_BACKUP_BEFORE_DESTRUCTIVE=false`` in
         config.env). That snapshot is never rotated.
    """
    db = db or DEFAULT_PORTFOLIO_DB
    # Snapshot OUTSIDE the connection so VACUUM INTO sees a stable
    # state and doesn't fight our open transaction.
    snap_path = _pre_destructive_backup_safe(
        db, op_name="delete-portfolio", target_id=portfolio_id)
    con = _connect(db)
    before_p = _snapshot_portfolio(con, portfolio_id)
    if before_p is None:
        con.close()
        raise GameError(f"No portfolio with id {portfolio_id}.")
    before_trades = _snapshot_trades(con, portfolio_id)
    was_active = _get_active_id(con) == portfolio_id
    con.execute("DELETE FROM portfolios WHERE id = ?", (portfolio_id,))
    note = f"deleted {len(before_trades)} trade(s) via FK cascade"
    if snap_path is not None:
        note += f"; pre_destructive_snapshot={snap_path}"
    _audit(con, actor="user", op_type="portfolio.delete",
           target_kind="portfolio", target_id=portfolio_id,
           before={"portfolio": before_p, "trades": before_trades},
           note=note)
    if was_active:
        nxt = con.execute(
            "SELECT id FROM portfolios WHERE archived_at IS NULL "
            "ORDER BY id LIMIT 1"
        ).fetchone()
        if nxt:
            _set_active_id(con, nxt[0])
            _audit(con, actor="system", op_type="portfolio.set_active",
                   target_kind="portfolio", target_id=nxt[0],
                   before={"active_portfolio_id": portfolio_id},
                   after={"active_portfolio_id": nxt[0]},
                   note="active rolled over after delete")
        else:
            con.execute(
                "DELETE FROM meta WHERE key = 'active_portfolio_id'")
            _audit(con, actor="system", op_type="portfolio.set_active",
                   before={"active_portfolio_id": portfolio_id},
                   after={"active_portfolio_id": None},
                   note="no remaining portfolios; active cleared after delete")
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
        _audit(con, actor="system", op_type="portfolio.set_active",
               target_kind="portfolio", target_id=any_existing[0],
               before={"active_portfolio_id": None},
               after={"active_portfolio_id": any_existing[0]},
               note="no active pointer; init_portfolio adopted the "
                    "earliest existing portfolio")
        con.commit(); con.close()
        return get_portfolio(portfolio_id=any_existing[0], db=db)
    con.close()
    # Mark this row as system-actor so the History view can show why
    # a "Default" appeared without an explicit user click — this is the
    # case that bit us earlier (a Default ghost-appeared after a wipe).
    return create_portfolio(
        "Default", starting_cash=starting_cash, db=db,
        _actor="system", _audit_note="auto-created by init_portfolio on "
                                     "first open (no portfolios existed)",
    )


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
    """List trades for ONE portfolio (active by default), in insertion
    order (oldest first).

    The ``id`` is the trades-table row id — monotonic, atomic, and the
    actual ordering source of truth. Two clicks within the same second
    share a timestamp string but always get distinct ids; expose this
    so the UI can distinguish them without faking sub-second precision.
    """
    db = db or DEFAULT_PORTFOLIO_DB
    con = _connect(db)
    pid = _resolve_pid(con, portfolio_id)
    rows = con.execute(
        "SELECT id, timestamp, symbol, side, qty, price, fill_price, "
        "cash_delta, note FROM trades WHERE portfolio_id = ? ORDER BY id",
        (pid,),
    ).fetchall()
    con.close()
    return [
        {"id": r[0], "timestamp": r[1], "symbol": r[2], "side": r[3],
         "qty": r[4], "price": r[5], "fill_price": r[6], "cash_delta": r[7],
         "note": r[8] or ""}
        for r in rows
    ]


def trade_stats(portfolio_id: int = None, db: Path = None) -> dict:
    """Outcome stats for the closed (buy→sell) round-trips of one portfolio.

    Walks the trade log with FIFO matching: every sell consumes shares
    against the running avg cost from prior buys, producing one realized
    P&L event per sell. Open positions don't count.

    Returns: total_trades (rows), n_buys, n_sells, closed_count, wins,
    losses, win_rate (0-1), avg_win, avg_loss (signed), expectancy,
    realized_pnl (sum of realized events).
    """
    trades = get_trades(portfolio_id=portfolio_id, db=db)
    pos = defaultdict(lambda: {"qty": 0.0, "avg_cost": 0.0})
    realized = []
    for t in trades:
        sym, side, qty, fill = (
            t["symbol"], t["side"], t["qty"], t["fill_price"]
        )
        p = pos[sym]
        if side == "buy":
            new_qty   = p["qty"] + qty
            new_total = p["qty"] * p["avg_cost"] + qty * fill
            p["qty"]      = new_qty
            p["avg_cost"] = new_total / new_qty if new_qty > 0 else 0.0
        else:
            sold = min(qty, p["qty"])
            if sold > 0:
                realized.append((fill - p["avg_cost"]) * sold)
                p["qty"] -= sold
                if p["qty"] <= 1e-9:
                    p["qty"], p["avg_cost"] = 0.0, 0.0

    wins   = [r for r in realized if r > 0]
    losses = [r for r in realized if r < 0]
    win_rate  = (len(wins) / len(realized)) if realized else 0.0
    avg_win   = (sum(wins)   / len(wins))   if wins   else 0.0
    avg_loss  = (sum(losses) / len(losses)) if losses else 0.0
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    return {
        "total_trades":  len(trades),
        "n_buys":        sum(1 for t in trades if t["side"] == "buy"),
        "n_sells":       sum(1 for t in trades if t["side"] == "sell"),
        "closed_count":  len(realized),
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      win_rate,
        "avg_win":       avg_win,
        "avg_loss":      avg_loss,
        "expectancy":    expectancy,
        "realized_pnl":  sum(realized),
    }


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
        if (row and row[0] is not None
                and (best[1] is None or row[1] > best[1])):
            best = (float(row[0]), row[1])
    return best


# A daily-collected symbol is at most ~3 days old even across a weekend;
# beyond this its price is stale — likely not in the collection watchlist.
STALE_PRICE_DAYS = 5


def days_since_bar(as_of: str | None) -> int | None:
    """Calendar days between today and an ISO price-bar timestamp, or None
    when there's no usable timestamp. Used to flag stale (uncollected)
    symbols so a paper trade isn't entered on a price that will never move.
    """
    if not as_of:
        return None
    try:
        d = datetime.date.fromisoformat(str(as_of)[:10])
    except ValueError:
        return None
    return (datetime.date.today() - d).days


# ─────────────────────────────────────────────────────────────────────────────
#  Trades — buy / sell / reset
# ─────────────────────────────────────────────────────────────────────────────

def _record_trade(con, pid, *, symbol, side, qty, price, fill_price,
                  cash_delta, note=None):
    cash_before = con.execute(
        "SELECT cash FROM portfolios WHERE id = ?", (pid,)
    ).fetchone()[0]
    cur = con.execute(
        "INSERT INTO trades (portfolio_id, timestamp, symbol, side, qty, "
        "price, fill_price, cash_delta, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (pid, _now(), symbol.upper(), side, float(qty), float(price),
         float(fill_price), float(cash_delta),
         (note or "").strip() or None),
    )
    trade_id = cur.lastrowid
    con.execute("UPDATE portfolios SET cash = cash + ? WHERE id = ?",
                (cash_delta, pid))
    after_trade = con.execute(
        f"SELECT {', '.join(_TRADE_COLS)} FROM trades WHERE id = ?",
        (trade_id,),
    ).fetchone()
    _audit(con, actor="user", op_type=f"trade.{side}",
           target_kind="trade", target_id=trade_id,
           after={"trade": _row_to_dict(after_trade, _TRADE_COLS),
                  "portfolio_id": pid,
                  "cash_before": cash_before,
                  "cash_after":  cash_before + cash_delta})


def buy(symbol: str, cash_amount: float,
        portfolio_id: int = None, db: Path = None,
        note: str = None) -> dict:
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
                  cash_delta=-cash_amount, note=note)
    con.commit(); con.close()
    return {"symbol": symbol.upper(), "qty": qty, "price": price,
            "fill_price": fill_price, "as_of": as_of, "spent": cash_amount}


def sell(symbol: str, qty: float = None,
         portfolio_id: int = None, db: Path = None,
         note: str = None) -> dict:
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
                  price=price, fill_price=fill_price,
                  cash_delta=+proceeds, note=note)
    con.commit(); con.close()
    return {"symbol": symbol.upper(), "qty": qty, "price": price,
            "fill_price": fill_price, "as_of": as_of, "proceeds": proceeds}


def reset_portfolio(starting_cash: float = DEFAULT_STARTING_CASH,
                    portfolio_id: int = None, db: Path = None) -> dict:
    """Wipe all trades for ONE portfolio (the active one by default) and
    reset its cash. Other portfolios are untouched. Destructive — recovery
    sources are the same as ``delete_portfolio``: ``audit_log.before_json``
    carries the full pre-reset state, and a pre-destructive snapshot of
    the DB lands under ``data/backups/pre-destructive/``."""
    db = db or DEFAULT_PORTFOLIO_DB
    # We need the resolved pid for the snapshot subdir name. Open a
    # short-lived read-only connection just for the lookup so VACUUM
    # INTO doesn't fight an open write transaction.
    probe = _connect(db)
    pid = _resolve_pid(probe, portfolio_id)
    probe.close()
    snap_path = _pre_destructive_backup_safe(
        db, op_name="reset-portfolio", target_id=pid)
    con = _connect(db)
    before_p      = _snapshot_portfolio(con, pid)
    before_trades = _snapshot_trades(con, pid)
    ts = _now()
    con.execute("DELETE FROM trades WHERE portfolio_id = ?", (pid,))
    con.execute(
        "UPDATE portfolios SET starting_cash = ?, cash = ?, last_reset_at = ? "
        "WHERE id = ?",
        (starting_cash, starting_cash, ts, pid),
    )
    after_p = _snapshot_portfolio(con, pid)
    note = (f"wiped {len(before_trades)} trade(s); "
            f"starting_cash {before_p['starting_cash']} → {starting_cash}")
    if snap_path is not None:
        note += f"; pre_destructive_snapshot={snap_path}"
    _audit(con, actor="user", op_type="portfolio.reset",
           target_kind="portfolio", target_id=pid,
           before={"portfolio": before_p, "trades": before_trades},
           after={"portfolio": after_p},
           note=note)
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

def benchmark_history(symbols: list, starting_cash: float,
                      start_date: datetime.date) -> list:
    """Equal-weight buy-and-hold over the same period as a portfolio.

    Allocates `starting_cash` evenly across `symbols`, buys each at its
    first available close on/after `start_date`, then marks to each
    day's close from there to today. Returns [{date, value}, ...].
    Empty list if no symbols have any data.

    Symbols with no price data on/after start_date are simply skipped —
    the remaining allocation just sums to less than starting_cash."""
    import pandas as pd

    symbols = [s.upper() for s in symbols if s and isinstance(s, str)]
    if not symbols:
        return []

    closes = {}
    try:
        data_dbs = _discover_data_dbs()
    except Exception:
        data_dbs = []
    for sym in symbols:
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
                    (sym, start_date.isoformat()),
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
        if not s.empty:
            closes[sym] = s.sort_index()
    if not closes:
        return []

    per_symbol = starting_cash / len(closes)
    shares: dict = {}
    for sym, series in closes.items():
        valid = series[series.index >= start_date]
        if valid.empty:
            continue
        shares[sym] = per_symbol / float(valid.iloc[0])
    if not shares:
        return []

    history = []
    cur   = start_date
    today = datetime.date.today()
    while cur <= today:
        value = 0.0
        for sym, qty in shares.items():
            valid = closes[sym][closes[sym].index <= cur]
            if valid.empty:
                continue
            value += qty * float(valid.iloc[-1])
        history.append({"date": cur.isoformat(), "value": value})
        cur += datetime.timedelta(days=1)
    return history


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
                # Load the FULL daily close history (not just >= start): a
                # position whose most recent bar predates the portfolio (stale
                # data — e.g. an EU ticker not collected recently) must still
                # be valued by forward-filling its last known close, exactly
                # like mark_to_market's get_latest_price. Filtering by start
                # dropped such holdings to £0 for the whole curve.
                rows = con.execute(
                    "SELECT timestamp, close FROM prices "
                    "WHERE symbol = ? AND interval = '1d' "
                    "ORDER BY timestamp",
                    (sym,),
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


def risk_stats(portfolio_id: int = None, db: Path = None) -> dict:
    """Risk-adjusted return metrics for one strategy.

    Computed from the daily ``value_history`` curve so they reflect
    actual day-by-day mark-to-market, not just open/close totals.

    Returns CAGR, Sharpe, Sortino (both annualised at 252 trading
    days, risk-free=0), and max drawdown. Empty/insufficient history
    returns zeros (chart won't be drawn anyway).
    """
    import math
    hist = value_history(portfolio_id=portfolio_id, db=db)
    n = len(hist)
    if n < 2:
        return {"cagr": 0.0, "sharpe": 0.0, "sortino": 0.0, "max_dd": 0.0,
                "n_days": n}

    totals = [h["total"] for h in hist]
    start, end = totals[0], totals[-1]
    days = (datetime.date.fromisoformat(hist[-1]["date"])
            - datetime.date.fromisoformat(hist[0]["date"])).days or 1

    cagr = (end / start) ** (365.25 / days) - 1 if start > 0 else 0.0

    # Daily simple returns from the mark-to-market curve.
    rets = []
    for i in range(1, n):
        prev = totals[i - 1]
        if prev > 0:
            rets.append(totals[i] / prev - 1)
    if not rets:
        return {"cagr": cagr, "sharpe": 0.0, "sortino": 0.0,
                "max_dd": 0.0, "n_days": n}

    mean = sum(rets) / len(rets)
    var  = sum((r - mean) ** 2 for r in rets) / len(rets)
    std  = math.sqrt(var)
    sharpe = (mean / std) * math.sqrt(252) if std > 0 else 0.0

    # Sortino: only count negative deviations (downside-only).
    neg = [r for r in rets if r < 0]
    if neg:
        down_var = sum(r ** 2 for r in neg) / len(rets)
        down_std = math.sqrt(down_var)
        sortino  = (mean / down_std) * math.sqrt(252) if down_std > 0 else 0.0
    else:
        # No down days: Sortino is technically infinite. Cap at the
        # Sharpe value so the UI doesn't render ∞.
        sortino = sharpe

    # Max drawdown: largest peak-to-trough decline of the running max.
    peak  = totals[0]
    max_dd = 0.0
    for t in totals:
        peak = max(peak, t)
        if peak > 0:
            dd = (t / peak) - 1   # negative
            max_dd = min(max_dd, dd)
    return {
        "cagr":    cagr * 100,           # %
        "sharpe":  sharpe,
        "sortino": sortino,
        "max_dd":  max_dd * 100,         # % (negative)
        "n_days":  n,
    }
