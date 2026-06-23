"""
test_backup.py
==============
Offline unit tests for ``stock_toolkit.backup`` (v2.4.1):

  - snapshot() round-trip: source DBs reopen from the snapshot
    with identical schema + rows.
  - manifest.json captures the right entries with the right
    method (VACUUM INTO vs copy).
  - list_snapshots() distinguishes 'manual' vs 'pre-destructive'.
  - rotate() trims only manual snapshots beyond `keep`; never
    touches pre-destructive snapshots.
  - auto_snapshot_enabled() honours the config opt-out.
  - Pre-destructive auto-snapshot fires from
    game.delete_portfolio() / game.reset_portfolio(), and the
    destructive op's audit row links the snapshot path.
  - Custom ``db`` paths drop their snapshots next to the DB.
  - A backup failure inside game.py is logged but does NOT block
    the destructive op (the audit log is the second safety net).

Run:
    python3 tests/test_backup.py
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

from stock_toolkit import backup, game  # noqa: E402


# ─────────────────────────────────────────────────────────────
#  Fixtures
# ─────────────────────────────────────────────────────────────

class _TmpDataDir:
    """Stand up a small ``data/`` tree for one test."""

    def __init__(self):
        self.tmp  = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.port = self.root / "portfolio.db"
        self.live = self.root / "stock_data.db"
        self.fail = self.root / "stock_failures.db"
        self.collector_json = self.root / ".collector_state.json"
        self.alerts_json    = self.root / ".alerts_state.json"
        self.backups        = self.root / "backups"

    def seed(self):
        # Each DB gets a minimal table + one row so a roundtrip is verifiable.
        for path in (self.port, self.live, self.fail):
            con = sqlite3.connect(path)
            con.execute("CREATE TABLE marker (k TEXT PRIMARY KEY, v TEXT)")
            con.execute("INSERT INTO marker VALUES (?, ?)",
                        (path.stem, "ok"))
            con.commit(); con.close()
        self.collector_json.write_text('{"calls": {"yfinance": 7}}')
        self.alerts_json.write_text('{"abc123": true}')

    def cleanup(self):
        self.tmp.cleanup()


class BackupTestCase(unittest.TestCase):

    def setUp(self):
        self.t = _TmpDataDir()
        self.addCleanup(self.t.cleanup)
        self.t.seed()


# ─────────────────────────────────────────────────────────────
#  snapshot() round-trip
# ─────────────────────────────────────────────────────────────

class TestSnapshotRoundtrip(BackupTestCase):

    def test_snapshot_creates_consistent_copies(self):
        dest = backup.snapshot(
            reason="unit-test",
            db_paths=(self.t.port, self.t.live, self.t.fail),
            json_paths=(self.t.collector_json, self.t.alerts_json),
            dest_root=self.t.backups,
        )
        # Both DBs and both JSON files landed.
        for name in ("portfolio.db", "stock_data.db", "stock_failures.db",
                     ".collector_state.json", ".alerts_state.json",
                     "manifest.json"):
            self.assertTrue((dest / name).exists(), f"missing {name}")
        # DB roundtrip — open the snapshot and check the rows.
        for src in (self.t.port, self.t.live, self.t.fail):
            con = sqlite3.connect(dest / src.name)
            row = con.execute(
                "SELECT k, v FROM marker").fetchone()
            con.close()
            self.assertEqual(row, (src.stem, "ok"))
        # JSON roundtrip
        self.assertEqual(
            json.loads((dest / ".collector_state.json").read_text()),
            {"calls": {"yfinance": 7}},
        )

    def test_manifest_records_method_per_entry(self):
        dest = backup.snapshot(
            reason="check-methods",
            db_paths=(self.t.port,),
            json_paths=(self.t.collector_json,),
            dest_root=self.t.backups,
        )
        m = json.loads((dest / "manifest.json").read_text())
        methods = {e["name"]: e["method"] for e in m["entries"]}
        self.assertEqual(methods["portfolio.db"], "VACUUM INTO")
        self.assertEqual(methods[".collector_state.json"], "copy")
        self.assertEqual(m["reason"], "check-methods")
        self.assertGreater(m["total_bytes"], 0)

    def test_missing_source_files_are_silently_skipped(self):
        # Reasonable behaviour: e.g. .alerts_state.json doesn't exist
        # in a fresh install until the first alert fires.
        absent = self.t.root / "does-not-exist.db"
        dest = backup.snapshot(
            reason="partial",
            db_paths=(self.t.port, absent),
            json_paths=(),
            dest_root=self.t.backups,
        )
        m = json.loads((dest / "manifest.json").read_text())
        names = [e["name"] for e in m["entries"]]
        self.assertEqual(names, ["portfolio.db"])

    def test_same_minute_snapshots_get_suffix(self):
        # Two snapshots issued the same minute share the timestamp slug
        # — second one must land at <slug>-2, not clobber the first.
        a = backup.snapshot(reason="a",
                            db_paths=(self.t.port,), json_paths=(),
                            dest_root=self.t.backups,
                            subdir="2026-06-23-1900")
        b = backup.snapshot(reason="b",
                            db_paths=(self.t.port,), json_paths=(),
                            dest_root=self.t.backups,
                            subdir="2026-06-23-1900")
        self.assertNotEqual(a, b)
        self.assertTrue(a.exists())
        self.assertTrue(b.exists())


# ─────────────────────────────────────────────────────────────
#  list_snapshots()
# ─────────────────────────────────────────────────────────────

class TestListSnapshots(BackupTestCase):

    def test_lists_manual_and_pre_destructive_separately(self):
        backup.snapshot(reason="manual-1",
                        db_paths=(self.t.port,), json_paths=(),
                        dest_root=self.t.backups,
                        subdir="2026-06-23-1900")
        backup.snapshot(reason="pre-delete",
                        db_paths=(self.t.port,), json_paths=(),
                        dest_root=self.t.backups / "pre-destructive",
                        subdir="2026-06-23-1905-pre-delete")

        snaps = backup.list_snapshots(self.t.backups)
        self.assertEqual(len(snaps), 2)
        kinds = {s["kind"] for s in snaps}
        self.assertEqual(kinds, {"manual", "pre-destructive"})

    def test_returns_empty_when_no_backups_dir(self):
        self.assertEqual(backup.list_snapshots(self.t.backups), [])

    def test_sorted_newest_first(self):
        backup.snapshot(reason="old",
                        db_paths=(self.t.port,), json_paths=(),
                        dest_root=self.t.backups,
                        subdir="2026-01-01-0000")
        backup.snapshot(reason="new",
                        db_paths=(self.t.port,), json_paths=(),
                        dest_root=self.t.backups,
                        subdir="2027-01-01-0000")
        snaps = backup.list_snapshots(self.t.backups)
        # Manifest timestamps reflect "now", so both have nearly equal
        # created_at. Make the order deterministic by patching one of
        # the manifests instead.
        d_old = next(s for s in snaps if s["reason"] == "old")
        m = json.loads((d_old["dir"] / "manifest.json").read_text())
        m["created_at"] = "2025-01-01T00:00:00+00:00"
        (d_old["dir"] / "manifest.json").write_text(json.dumps(m))
        snaps = backup.list_snapshots(self.t.backups)
        self.assertEqual(snaps[0]["reason"], "new")
        self.assertEqual(snaps[1]["reason"], "old")


# ─────────────────────────────────────────────────────────────
#  rotate()
# ─────────────────────────────────────────────────────────────

class TestRotation(BackupTestCase):

    def _make_n_manual(self, n: int):
        for i in range(n):
            d = backup.snapshot(reason=f"m{i}",
                                db_paths=(self.t.port,), json_paths=(),
                                dest_root=self.t.backups,
                                subdir=f"snap-{i:03d}")
            # Vary manifest created_at so order is deterministic.
            m = json.loads((d / "manifest.json").read_text())
            m["created_at"] = f"2026-01-{i + 1:02d}T00:00:00+00:00"
            (d / "manifest.json").write_text(json.dumps(m))

    def test_keep_n_removes_older_manuals(self):
        self._make_n_manual(5)
        removed = backup.rotate(keep=3, dest_root=self.t.backups)
        self.assertEqual(len(removed), 2)
        remaining = backup.list_snapshots(self.t.backups)
        self.assertEqual(len(remaining), 3)
        # The newest 3 survive (m4, m3, m2).
        survivors = {s["reason"] for s in remaining}
        self.assertEqual(survivors, {"m4", "m3", "m2"})

    def test_rotation_never_touches_pre_destructive(self):
        # Some manuals + one pre-destructive
        self._make_n_manual(5)
        backup.snapshot(
            reason="pre-delete-portfolio-99",
            db_paths=(self.t.port,), json_paths=(),
            dest_root=self.t.backups / "pre-destructive",
            subdir="2026-06-23-1900-pre-delete",
        )
        backup.rotate(keep=1, dest_root=self.t.backups)
        snaps = backup.list_snapshots(self.t.backups)
        kinds = [s["kind"] for s in snaps]
        # 1 manual (kept) + 1 pre-destructive (untouched)
        self.assertEqual(kinds.count("manual"), 1)
        self.assertEqual(kinds.count("pre-destructive"), 1)

    def test_keep_zero_drops_all_manuals(self):
        self._make_n_manual(3)
        backup.rotate(keep=0, dest_root=self.t.backups)
        snaps = backup.list_snapshots(self.t.backups)
        self.assertEqual(snaps, [])

    def test_negative_keep_raises(self):
        with self.assertRaises(ValueError):
            backup.rotate(keep=-1, dest_root=self.t.backups)


# ─────────────────────────────────────────────────────────────
#  Config opt-out
# ─────────────────────────────────────────────────────────────

class TestAutoSnapshotConfigToggle(unittest.TestCase):

    def _run_with_cfg(self, cfg_dict, expected):
        with mock.patch.object(backup, "load_config",
                               return_value=cfg_dict):
            self.assertEqual(backup.auto_snapshot_enabled(), expected)

    def test_default_is_enabled(self):
        self._run_with_cfg({}, True)

    def test_false_disables(self):
        for val in ("false", "FALSE", "0", "no", "off"):
            with self.subTest(val=val):
                self._run_with_cfg(
                    {"AUTO_BACKUP_BEFORE_DESTRUCTIVE": val}, False)

    def test_true_keeps_enabled(self):
        for val in ("true", "TRUE", "1", "yes", "on"):
            with self.subTest(val=val):
                self._run_with_cfg(
                    {"AUTO_BACKUP_BEFORE_DESTRUCTIVE": val}, True)


# ─────────────────────────────────────────────────────────────
#  game.py integration — destructive ops trigger auto-snapshot
# ─────────────────────────────────────────────────────────────

class TestPreDestructiveHook(BackupTestCase):
    """Top-level promise: delete_portfolio + reset_portfolio land a
    snapshot of the DB next to it under backups/pre-destructive/."""

    def setUp(self):
        super().setUp()
        # Stub get_latest_price so buy/sell don't need a stock DB.
        self.price_patch = mock.patch.object(
            game, "get_latest_price", return_value=(100.0, "2026-01-02"))
        self.price_patch.start()
        self.addCleanup(self.price_patch.stop)

    def _backups_root(self):
        return self.t.root / "backups"

    def test_delete_portfolio_triggers_pre_destructive_snapshot(self):
        p = game.create_portfolio("X", db=self.t.port)
        game.buy("AAPL", 250.0, db=self.t.port)
        game.delete_portfolio(p["id"], db=self.t.port)

        pre = list((self._backups_root() / "pre-destructive").iterdir())
        self.assertEqual(len(pre), 1, f"expected 1 pre-destructive snap, got {pre}")
        self.assertIn("pre-delete-portfolio", pre[0].name)
        # The snapshotted DB still has the portfolio we just deleted.
        snap_db = pre[0] / "portfolio.db"
        self.assertTrue(snap_db.exists())
        con = sqlite3.connect(snap_db)
        rows = con.execute(
            "SELECT name FROM portfolios WHERE id = ?", (p["id"],)
        ).fetchall()
        con.close()
        self.assertEqual(rows, [("X",)])

    def test_delete_audit_row_references_snapshot_path(self):
        p = game.create_portfolio("Y", db=self.t.port)
        game.delete_portfolio(p["id"], db=self.t.port)
        # The audit row's note should embed the snapshot path.
        rows = game.get_audit_log(op_prefix="portfolio.delete",
                                  db=self.t.port)
        self.assertEqual(len(rows), 1)
        self.assertIn("pre_destructive_snapshot=", rows[0]["note"] or "")
        # And the path actually exists on disk.
        path_str = rows[0]["note"].split("pre_destructive_snapshot=")[-1]
        self.assertTrue(pathlib.Path(path_str).exists())

    def test_reset_portfolio_triggers_pre_destructive_snapshot(self):
        p = game.create_portfolio("R", db=self.t.port)
        game.buy("AAPL", 500.0, db=self.t.port)
        game.reset_portfolio(starting_cash=10_000.0,
                             portfolio_id=p["id"], db=self.t.port)
        pre = list((self._backups_root() / "pre-destructive").iterdir())
        self.assertEqual(len(pre), 1)
        self.assertIn("pre-reset-portfolio", pre[0].name)

    def test_opt_out_skips_snapshot(self):
        with mock.patch.object(backup, "load_config",
                               return_value={"AUTO_BACKUP_BEFORE_DESTRUCTIVE": "false"}):
            p = game.create_portfolio("Off", db=self.t.port)
            game.delete_portfolio(p["id"], db=self.t.port)
        # No pre-destructive dir at all (or empty).
        pre_dir = self._backups_root() / "pre-destructive"
        if pre_dir.exists():
            self.assertEqual(list(pre_dir.iterdir()), [])
        # Audit row's note must NOT contain a snapshot path.
        rows = game.get_audit_log(op_prefix="portfolio.delete",
                                  db=self.t.port)
        self.assertEqual(len(rows), 1)
        self.assertNotIn("pre_destructive_snapshot=", rows[0]["note"] or "")

    def test_backup_failure_does_not_block_destructive_op(self):
        # Force the snapshot to raise. The portfolio MUST still be
        # deleted (the user explicitly asked for it; the audit log is
        # the secondary safety net).
        p = game.create_portfolio("Z", db=self.t.port)
        with mock.patch.object(backup, "snapshot",
                               side_effect=RuntimeError("disk full")):
            game.delete_portfolio(p["id"], db=self.t.port)
        # Portfolio is gone.
        live = game.list_portfolios(include_archived=True, db=self.t.port)
        self.assertEqual([row["id"] for row in live if row["id"] == p["id"]], [])
        # Audit row exists and contains the full before_json (recovery
        # source #2 in action).
        rows = game.get_audit_log(op_prefix="portfolio.delete",
                                  db=self.t.port)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["before"]["portfolio"]["name"], "Z")


if __name__ == "__main__":
    runner = unittest.main(verbosity=2, exit=False)
    sys.exit(0 if runner.result.wasSuccessful() else 1)
