"""
test_engine_rust.py
===================
Offline unit tests for the Rust-engine dispatcher
(``stock_toolkit.collector.engine``).

The dispatcher is the contract surface between ``stock-collect
--engine rust`` and the Rust ``stock-fetcher`` binary. These tests
cover the parts that are easy to get wrong without ever invoking
the real binary:

  - binary discovery: env override → repo layout → PATH → None
  - graceful "binary missing" exit (rc=127, no traceback)
  - source-allowlist rejection (rc=2)
  - argv construction matches the Rust CLI's expected flags

Run:
    python3 tests/test_engine_rust.py
"""

import os
import pathlib
import stat
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT_DIR = pathlib.Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from stock_toolkit.collector import engine  # noqa: E402


def _make_executable(path: pathlib.Path) -> None:
    """Make `path` (a file) executable for the current user."""
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# ─────────────────────────────────────────────────────────────
#  find_rust_binary
# ─────────────────────────────────────────────────────────────

class TestFindBinary(unittest.TestCase):
    """Discovery order: env → repo layout → PATH → None."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        # Wipe the env knob so each test sets it explicitly.
        self.env_patcher = mock.patch.dict(os.environ, {}, clear=False)
        self.env_patcher.start()
        os.environ.pop("STOCK_FETCHER_BIN", None)
        self.addCleanup(self.env_patcher.stop)

    def test_env_override_wins(self):
        bin_path = self.root / "my-fetcher"
        _make_executable(bin_path)
        os.environ["STOCK_FETCHER_BIN"] = str(bin_path)
        found = engine.find_rust_binary()
        self.assertEqual(found, bin_path.resolve())

    def test_env_override_missing_file_returns_none(self):
        os.environ["STOCK_FETCHER_BIN"] = str(self.root / "nope")
        # Even if the repo layout has a binary, the explicit env
        # override is treated as "user told us where it is, full stop".
        with mock.patch.object(engine, "find_rust_binary",
                               wraps=engine.find_rust_binary):
            self.assertIsNone(engine.find_rust_binary())

    def test_repo_layout_discovery(self):
        # Mimic: <root>/pyApi/...  with  <root>/rust-fetcher/target/release/stock-fetcher
        pyApi  = self.root / "pyApi"
        pyApi.mkdir()
        release_dir = self.root / "rust-fetcher" / "target" / "release"
        release_dir.mkdir(parents=True)
        bin_path = release_dir / "stock-fetcher"
        _make_executable(bin_path)

        with mock.patch("stock_toolkit.common.BASE_DIR", pyApi), \
             mock.patch("shutil.which", return_value=None):
            found = engine.find_rust_binary()
        self.assertEqual(found, bin_path)

    def test_path_lookup_fallback(self):
        # No env override, no repo layout — PATH should be consulted.
        pyApi = self.root / "pyApi"
        pyApi.mkdir()
        path_bin = self.root / "elsewhere" / "stock-fetcher"
        path_bin.parent.mkdir()
        _make_executable(path_bin)
        with mock.patch("stock_toolkit.common.BASE_DIR", pyApi), \
             mock.patch("shutil.which", return_value=str(path_bin)):
            found = engine.find_rust_binary()
        self.assertEqual(found, path_bin)

    def test_nothing_found_returns_none(self):
        pyApi = self.root / "pyApi"
        pyApi.mkdir()
        with mock.patch("stock_toolkit.common.BASE_DIR", pyApi), \
             mock.patch("shutil.which", return_value=None):
            self.assertIsNone(engine.find_rust_binary())


# ─────────────────────────────────────────────────────────────
#  unsupported_sources
# ─────────────────────────────────────────────────────────────

class TestUnsupportedSources(unittest.TestCase):
    """Allow-list gate for sources Rust does not yet handle."""

    def test_alphavantage_alone_is_fine(self):
        self.assertEqual(engine.unsupported_sources(["alphavantage"]), [])

    def test_mixed_set_returns_unsupported_only(self):
        bad = engine.unsupported_sources(["alphavantage", "yfinance", "finnhub"])
        self.assertEqual(sorted(bad), ["finnhub", "yfinance"])

    def test_empty_input_returns_empty(self):
        # No --sources filter means "Rust default" — let it through.
        self.assertEqual(engine.unsupported_sources([]), [])
        self.assertEqual(engine.unsupported_sources(None), [])


# ─────────────────────────────────────────────────────────────
#  run_rust
# ─────────────────────────────────────────────────────────────

class TestRunRust(unittest.TestCase):
    """The subprocess dispatcher — covered without spawning processes."""

    def test_missing_binary_exits_127(self):
        # Binary nowhere to be found → friendly error, rc=127, no exception.
        with mock.patch.object(engine, "find_rust_binary", return_value=None):
            rc = engine.run_rust(["alphavantage"], ["AAPL"])
        self.assertEqual(rc, 127)

    def test_unsupported_source_returns_2_without_calling_subprocess(self):
        # If the wrong source slips through, the dispatcher must catch it
        # *before* spawning the binary (binary doesn't know how to refuse
        # — the orchestrator does).
        fake_bin = pathlib.Path("/tmp/fake-stock-fetcher")
        with mock.patch.object(engine, "find_rust_binary", return_value=fake_bin), \
             mock.patch("subprocess.run") as run:
            rc = engine.run_rust(["yfinance"], ["AAPL"])
        self.assertEqual(rc, 2)
        run.assert_not_called()

    def test_argv_shape_passes_sources_and_symbols_csv(self):
        # The Rust CLI uses comma-delimited values for --sources / --symbols.
        fake_bin = pathlib.Path("/tmp/fake-stock-fetcher")
        captured = {}

        def fake_run(argv, check=False):
            captured["argv"] = argv
            class _R: returncode = 0
            return _R()

        with mock.patch.object(engine, "find_rust_binary", return_value=fake_bin), \
             mock.patch("subprocess.run", side_effect=fake_run):
            rc = engine.run_rust(
                ["alphavantage"],
                ["AAPL", "MSFT"],
                db=pathlib.Path("/tmp/x.db"),
            )

        self.assertEqual(rc, 0)
        argv = captured["argv"]
        # First arg is the binary path itself.
        self.assertEqual(argv[0], str(fake_bin))
        # CSV joining is the Rust clap contract — *not* repeated flags.
        self.assertIn("--sources", argv)
        self.assertEqual(argv[argv.index("--sources") + 1], "alphavantage")
        self.assertIn("--symbols", argv)
        self.assertEqual(argv[argv.index("--symbols") + 1], "AAPL,MSFT")
        self.assertIn("--db", argv)
        self.assertEqual(argv[argv.index("--db") + 1], "/tmp/x.db")

    def test_returncode_is_surfaced(self):
        # Rust binary failed (e.g. throttled, network down) → bubble it up.
        fake_bin = pathlib.Path("/tmp/fake-stock-fetcher")

        def fake_run(argv, check=False):
            class _R: returncode = 17
            return _R()

        with mock.patch.object(engine, "find_rust_binary", return_value=fake_bin), \
             mock.patch("subprocess.run", side_effect=fake_run):
            rc = engine.run_rust(["alphavantage"], ["AAPL"])
        self.assertEqual(rc, 17)

    def test_filenotfound_at_exec_time_returns_127(self):
        # Race: binary present at find time, gone at exec time.
        fake_bin = pathlib.Path("/tmp/fake-stock-fetcher")
        with mock.patch.object(engine, "find_rust_binary", return_value=fake_bin), \
             mock.patch("subprocess.run", side_effect=FileNotFoundError):
            rc = engine.run_rust(["alphavantage"], ["AAPL"])
        self.assertEqual(rc, 127)


if __name__ == "__main__":
    runner = unittest.main(verbosity=2, exit=False)
    sys.exit(0 if runner.result.wasSuccessful() else 1)
