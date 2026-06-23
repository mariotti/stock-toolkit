"""
test_audit_log.py
=================
Offline unit tests for the v2.4.0 audit log
(``stock_toolkit.game._audit`` write-through + ``get_audit_log``
+ ``get_audit_event`` readers).

What this proves:

  - Every mutation in game.py writes at least one audit row,
    atomic with the change it records (same transaction).
  - Destructive ops (delete_portfolio, reset_portfolio) snapshot
    the full pre-state into before_json — the audit log itself
    is a recovery source.
  - Trade rows audit the new trade id, cash before/after, the
    parent portfolio_id.
  - System ops (schema_migrate, audit_log.initialised,
    auto_create_default) get their own actor='system' rows.
  - The reader functions filter + parse correctly.

Run:
    python3 tests/test_audit_log.py
"""

import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT_DIR = pathlib.Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from stock_toolkit import game  # noqa: E402


# ─────────────────────────────────────────────────────────────
#  Fixtures
# ─────────────────────────────────────────────────────────────

class AuditTestCase(unittest.TestCase):
    """Each test runs against a fresh tmp portfolio.db."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = pathlib.Path(self.tmp.name) / "portfolio.db"

    # convenience reader — raw rows, no JSON decode
    def _all(self):
        con = sqlite3.connect(self.db)
        rows = con.execute(
            "SELECT id, actor, op_type, target_kind, target_id, "
            "before_json, after_json, note FROM audit_log ORDER BY id"
        ).fetchall()
        con.close()
        return rows

    def _ops(self):
        return [r[2] for r in self._all()]


# ─────────────────────────────────────────────────────────────
#  Bootstrap: fresh DB → exactly one system row
# ─────────────────────────────────────────────────────────────

class TestBootstrap(AuditTestCase):

    def test_first_open_records_audit_log_initialised(self):
        # Open by way of any function that calls _connect.
        game.list_portfolios(db=self.db)
        ops = self._ops()
        # One init row, nothing else (no schema migration on a fresh DB).
        self.assertEqual(ops, ["system.audit_log.initialised"])

    def test_second_open_does_not_repeat_init_row(self):
        # Idempotent: re-opening must not append another init marker.
        game.list_portfolios(db=self.db)
        game.list_portfolios(db=self.db)
        ops = self._ops()
        self.assertEqual(ops.count("system.audit_log.initialised"), 1)


# ─────────────────────────────────────────────────────────────
#  Portfolio lifecycle
# ─────────────────────────────────────────────────────────────

class TestPortfolioLifecycle(AuditTestCase):

    def test_create_writes_create_and_set_active(self):
        p = game.create_portfolio("Aggressive", db=self.db)
        # init + create + set_active (since activate=True is default)
        ops = self._ops()
        self.assertIn("portfolio.create", ops)
        self.assertIn("portfolio.set_active", ops)
        # actor=user for the create
        create_row = [r for r in self._all()
                      if r[2] == "portfolio.create"][0]
        self.assertEqual(create_row[1], "user")
        # after_json contains the new portfolio
        after = json.loads(create_row[6])
        self.assertEqual(after["name"], "Aggressive")
        self.assertEqual(after["id"], p["id"])

    def test_create_without_activate_no_set_active_row(self):
        # If we explicitly don't activate, set_active should not fire.
        game.create_portfolio("Other", db=self.db, activate=False)
        ops = self._ops()
        self.assertIn("portfolio.create", ops)
        self.assertNotIn("portfolio.set_active", ops)

    def test_rename_records_before_and_after_name(self):
        p = game.create_portfolio("OldName", db=self.db)
        game.rename_portfolio(p["id"], "NewName", db=self.db)
        rows = [r for r in self._all() if r[2] == "portfolio.rename"]
        self.assertEqual(len(rows), 1)
        before = json.loads(rows[0][5])
        after  = json.loads(rows[0][6])
        self.assertEqual(before["name"], "OldName")
        self.assertEqual(after["name"],  "NewName")

    def test_rename_to_same_name_is_a_noop(self):
        p = game.create_portfolio("Same", db=self.db)
        game.rename_portfolio(p["id"], "Same", db=self.db)
        # No rename row created.
        self.assertNotIn("portfolio.rename", self._ops())

    def test_archive_records_archive_and_active_rollover(self):
        a = game.create_portfolio("A", db=self.db)        # active
        b = game.create_portfolio("B", db=self.db, activate=False)
        # Archive the active one — should roll active over to B
        game.archive_portfolio(a["id"], db=self.db)
        ops = self._ops()
        # archive itself
        self.assertIn("portfolio.archive", ops)
        # plus a system set_active rolling to B
        set_active_rows = [r for r in self._all()
                           if r[2] == "portfolio.set_active"]
        # Two set_active total: one when A was created (activate=True),
        # one when archive rolled active over.
        self.assertEqual(len(set_active_rows), 2)
        # The rollover row is actor=system
        rollover = set_active_rows[-1]
        self.assertEqual(rollover[1], "system")
        self.assertIn("rolled over", (rollover[7] or ""))

    def test_unarchive_writes_audit_row(self):
        p = game.create_portfolio("X", db=self.db)
        game.archive_portfolio(p["id"], db=self.db)
        game.unarchive_portfolio(p["id"], db=self.db)
        self.assertIn("portfolio.unarchive", self._ops())

    def test_unarchive_noop_when_not_archived(self):
        p = game.create_portfolio("X", db=self.db)
        # Already not archived — no row.
        game.unarchive_portfolio(p["id"], db=self.db)
        self.assertNotIn("portfolio.unarchive", self._ops())


# ─────────────────────────────────────────────────────────────
#  Destructive ops — recovery source guarantees
# ─────────────────────────────────────────────────────────────

class TestDestructiveOpsCarryRecoveryData(AuditTestCase):
    """The whole point of v2.4.0: delete + reset stash the pre-state
    inside the audit row so the row itself remains a recovery source
    even after the live rows are gone."""

    def _seed_portfolio_with_one_trade(self):
        # Stub get_latest_price so we don't need a real stock DB.
        with mock.patch.object(game, "get_latest_price",
                               return_value=(100.0, "2026-01-02")):
            p = game.create_portfolio("Seed", db=self.db)
            game.buy("AAPL", 200.0, db=self.db)
        return p["id"]

    def test_delete_audit_carries_full_before_state(self):
        pid = self._seed_portfolio_with_one_trade()
        # Capture live state for comparison
        before_p      = game.get_portfolio(portfolio_id=pid, db=self.db)
        before_trades = game.get_trades(portfolio_id=pid, db=self.db)
        self.assertEqual(len(before_trades), 1)

        game.delete_portfolio(pid, db=self.db)

        del_row = [r for r in self._all()
                   if r[2] == "portfolio.delete"][0]
        before_json = json.loads(del_row[5])
        # Pre-state of the portfolio matches what we held live.
        self.assertEqual(before_json["portfolio"]["id"], pid)
        self.assertEqual(before_json["portfolio"]["name"], "Seed")
        self.assertAlmostEqual(
            before_json["portfolio"]["cash"], before_p["cash"])
        # Pre-state of trades is the full list, not just ids.
        self.assertEqual(len(before_json["trades"]), 1)
        self.assertEqual(before_json["trades"][0]["symbol"], "AAPL")
        # Note records the cascade count.
        self.assertIn("1 trade", del_row[7] or "")

    def test_reset_audit_carries_pre_reset_trades(self):
        pid = self._seed_portfolio_with_one_trade()
        game.reset_portfolio(starting_cash=5000.0,
                             portfolio_id=pid, db=self.db)
        reset_row = [r for r in self._all()
                     if r[2] == "portfolio.reset"][0]
        before_json = json.loads(reset_row[5])
        self.assertEqual(len(before_json["trades"]), 1)
        self.assertEqual(before_json["trades"][0]["symbol"], "AAPL")
        # After-state shows the new starting_cash.
        after_json = json.loads(reset_row[6])
        self.assertEqual(after_json["portfolio"]["starting_cash"], 5000.0)


# ─────────────────────────────────────────────────────────────
#  Trades
# ─────────────────────────────────────────────────────────────

class TestTradeAudit(AuditTestCase):

    def setUp(self):
        super().setUp()
        # Common: stub price + one portfolio.
        self.price_patch = mock.patch.object(
            game, "get_latest_price", return_value=(100.0, "2026-01-02"))
        self.price_patch.start()
        self.addCleanup(self.price_patch.stop)
        self.pid = game.create_portfolio("T", db=self.db)["id"]

    def test_buy_writes_trade_buy_row_with_cash_deltas(self):
        game.buy("AAPL", 250.0, db=self.db)
        rows = [r for r in self._all() if r[2] == "trade.buy"]
        self.assertEqual(len(rows), 1)
        after = json.loads(rows[0][6])
        self.assertEqual(after["trade"]["symbol"], "AAPL")
        self.assertEqual(after["trade"]["side"], "buy")
        self.assertEqual(after["portfolio_id"], self.pid)
        # Cash before/after are recorded for replay/diff.
        self.assertAlmostEqual(after["cash_before"], 10_000.0)
        self.assertAlmostEqual(after["cash_after"],  10_000.0 - 250.0)
        # target_id points at the actual new trade row.
        target_id = rows[0][4]
        live = game.get_trades(db=self.db)[0]
        self.assertEqual(live["symbol"], "AAPL")
        # The id stored in audit must exist as a real trade row.
        con = sqlite3.connect(self.db)
        exists = con.execute(
            "SELECT 1 FROM trades WHERE id = ?", (target_id,)
        ).fetchone()
        con.close()
        self.assertIsNotNone(exists)

    def test_sell_writes_trade_sell_row(self):
        game.buy("AAPL", 250.0, db=self.db)
        game.sell("AAPL", db=self.db)  # close fully
        self.assertIn("trade.sell", self._ops())


# ─────────────────────────────────────────────────────────────
#  init_portfolio system-actor marker
# ─────────────────────────────────────────────────────────────

class TestInitPortfolioMarker(AuditTestCase):
    """The case that bit us: a Default appearing without an explicit
    user click. v2.4.0 marks that creation actor='system' with a note."""

    def test_default_auto_create_is_actor_system(self):
        game.init_portfolio(db=self.db)
        rows = [r for r in self._all()
                if r[2] == "portfolio.create"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "system")
        self.assertIn("auto-created", (rows[0][7] or "").lower())


# ─────────────────────────────────────────────────────────────
#  Reader API
# ─────────────────────────────────────────────────────────────

class TestReaders(AuditTestCase):

    def test_get_audit_log_returns_newest_first(self):
        a = game.create_portfolio("A", db=self.db)
        game.create_portfolio("B", db=self.db, activate=False)
        log = game.get_audit_log(db=self.db)
        # Newest first → portfolio.create for B is more recent than A.
        # First-listed should be 'portfolio.create' for B.
        creates = [e for e in log if e["op_type"] == "portfolio.create"]
        self.assertEqual(creates[0]["after"]["name"], "B")
        self.assertEqual(creates[1]["after"]["name"], "A")
        # Each event has parsed before/after (dict or None), not raw JSON.
        for e in log:
            self.assertTrue(e["before"] is None or isinstance(e["before"], (dict, list)))
            self.assertTrue(e["after"]  is None or isinstance(e["after"],  (dict, list)))

    def test_get_audit_log_op_prefix_filter(self):
        game.create_portfolio("A", db=self.db)
        sys_only = game.get_audit_log(op_prefix="system.", db=self.db)
        for e in sys_only:
            self.assertTrue(e["op_type"].startswith("system."))

    def test_get_audit_log_limit(self):
        for n in range(5):
            game.create_portfolio(f"P{n}", db=self.db, activate=False)
        log = game.get_audit_log(limit=3, db=self.db)
        self.assertEqual(len(log), 3)

    def test_get_audit_event_returns_one_or_none(self):
        game.create_portfolio("A", db=self.db)
        first = game.get_audit_log(db=self.db)[0]
        evt   = game.get_audit_event(first["id"], db=self.db)
        self.assertEqual(evt["id"], first["id"])
        self.assertEqual(evt["op_type"], first["op_type"])
        # Unknown id → None
        self.assertIsNone(game.get_audit_event(999_999, db=self.db))


# ─────────────────────────────────────────────────────────────
#  System: v1 → v2 schema migration leaves a marker row
# ─────────────────────────────────────────────────────────────

class TestV1ToV2MigrationAudit(AuditTestCase):
    """The v1→v2 conversion was previously silent. v2.4.0 logs it
    so anyone inspecting an old DB can see *when* it happened."""

    def setUp(self):
        super().setUp()
        # Hand-build a v1 (single-portfolio) DB so _migrate_to_v2 will fire.
        con = sqlite3.connect(self.db)
        con.executescript("""
            CREATE TABLE portfolio (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                starting_cash REAL NOT NULL, cash REAL NOT NULL,
                created_at TEXT NOT NULL, last_reset_at TEXT NOT NULL
            );
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, symbol TEXT NOT NULL,
                side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
                qty REAL NOT NULL, price REAL NOT NULL,
                fill_price REAL NOT NULL, cash_delta REAL NOT NULL
            );
            INSERT INTO portfolio VALUES
                (1, 10000.0, 8500.0, '2026-05-01T00:00:00+00:00',
                 '2026-05-01T00:00:00+00:00');
        """)
        con.commit(); con.close()

    def test_v1_to_v2_migration_writes_system_marker(self):
        # Any function that opens the DB triggers _connect → _migrate_to_v2.
        game.list_portfolios(db=self.db)
        ops = self._ops()
        # Both system markers appear, once each, in this order:
        #   schema_migrate (the migration that just ran)
        #   audit_log.initialised (the table itself didn't exist before)
        self.assertIn("system.schema_migrate.v1_to_v2", ops)
        self.assertIn("system.audit_log.initialised",   ops)
        self.assertEqual(
            ops.count("system.schema_migrate.v1_to_v2"), 1,
            "migration must only be logged once, not on every reopen")

    def test_subsequent_open_does_not_re_log_migration(self):
        game.list_portfolios(db=self.db)
        game.list_portfolios(db=self.db)
        game.list_portfolios(db=self.db)
        self.assertEqual(
            self._ops().count("system.schema_migrate.v1_to_v2"), 1)


# ─────────────────────────────────────────────────────────────
#  Atomicity — failure mid-op must roll back the audit row too
# ─────────────────────────────────────────────────────────────

class TestAtomicity(AuditTestCase):
    """If the operation fails, the audit row must NOT survive. Same
    transaction, same rollback."""

    def test_duplicate_name_create_does_not_leak_audit_row(self):
        game.create_portfolio("Same", db=self.db)
        with self.assertRaises(game.GameError):
            game.create_portfolio("Same", db=self.db)
        # Only ONE portfolio.create audit row exists.
        creates = [r for r in self._all() if r[2] == "portfolio.create"]
        self.assertEqual(len(creates), 1)


if __name__ == "__main__":
    runner = unittest.main(verbosity=2, exit=False)
    sys.exit(0 if runner.result.wasSuccessful() else 1)
