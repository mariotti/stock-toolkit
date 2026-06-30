"""
test_backup_cli.py
================
Coverage-focused tests for stock_toolkit/backup.py main() — drives
--list / --dry-run / snapshot+rotate against a temp state dir (the
module's path globals are redirected so the real data/ is untouched).
"""
import contextlib
import io
import os
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("MPLBACKEND", "Agg")
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from stock_toolkit import backup  # noqa: E402


def _make_db(path: pathlib.Path) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    con.execute("INSERT INTO t (v) VALUES ('x'), ('y')")
    con.commit(); con.close()


class TestBackupCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = pathlib.Path(self.tmp.name)
        self.live = base / "stock_data.db"
        _make_db(self.live)
        self.backups = base / "backups"
        patches = [
            mock.patch.object(backup, "BACKUPS_DIR", self.backups),
            mock.patch.object(backup, "DEFAULT_DB_PATHS", [self.live]),
            mock.patch.object(backup, "_JSON_STATE", []),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _main(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            old = sys.argv
            sys.argv = ["stock-backup", *args]
            try:
                backup.main()
            finally:
                sys.argv = old
        return buf.getvalue()

    def test_list_empty(self):
        out = self._main("--list")
        self.assertIn("No snapshots", out)

    def test_dry_run(self):
        out = self._main("--dry-run")
        self.assertIn("dry-run", out)
        self.assertIn("stock_data.db", out)

    def test_snapshot_then_list(self):
        out = self._main()
        self.assertIn("Snapshot", out)
        # the snapshot dir now exists and re-opens as a working DB
        snaps = list(self.backups.glob("*/stock_data.db"))
        self.assertTrue(snaps, "expected a snapshot copy of the DB")
        con = sqlite3.connect(snaps[0])
        n = con.execute("SELECT count(*) FROM t").fetchone()[0]
        con.close()
        self.assertEqual(n, 2)
        # list now shows it
        listing = self._main("--list")
        self.assertIn("snapshot(s)", listing)

    def test_snapshot_with_rotation(self):
        self._main("--reason", "first")
        self._main("--reason", "second")
        out = self._main("--keep", "1")   # rotate down to 1 manual
        self.assertIn("Snapshot", out)
        manual = [s for s in backup.list_snapshots(self.backups)
                  if s["kind"] == "manual"]
        self.assertLessEqual(len(manual), 1)


if __name__ == "__main__":
    unittest.main()
