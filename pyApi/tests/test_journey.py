"""
test_journey.py
===============
End-to-end user-journey tests: subprocess invocations of the actual
console entry points. Complements the unit tests by verifying that the
*user-facing surface* works — pyproject.toml entry points resolve, CLIs
parse args, exit codes are sane, and the install→bootstrap→score path
holds together as a through-path.

A fake `yfinance` module under tests/journey_yfinance/ is injected via
PYTHONPATH so the pipeline test runs offline and deterministically.

Run:
    python3 tests/test_journey.py
"""

import os
import pathlib
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import unittest


HERE       = pathlib.Path(__file__).parent
FAKE_YF    = HERE / "journey_yfinance"

ENTRY_POINTS = [
    "stock-collect",
    "stock-analyse",
    "stock-score",
    "stock-backtest",
    "stock-alerts",
    "stock-inventory",
    "stock-setup",
    "stock-ui",
    "stock-bootstrap",
]


def find_entry_point(name: str) -> str | None:
    """Locate an installed console script — prefer the current venv's bin."""
    scripts_dir = pathlib.Path(sysconfig.get_path("scripts"))
    candidate   = scripts_dir / name
    if candidate.exists():
        return str(candidate)
    return shutil.which(name)


def run(cmd, env=None, timeout=60):
    """subprocess.run with sane defaults — capture output, never raise."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  1. Every console script is installed and responds to --help
# ─────────────────────────────────────────────────────────────────────────────

class TestEntryPointsRespondToHelp(unittest.TestCase):
    """Catches packaging regressions at the CLI layer.

    Complementary to TestPackageDistribution (which checks importability
    at the Python level): this verifies the pyproject.toml console
    scripts actually got installed and that each CLI shim doesn't crash
    on the simplest possible invocation."""

    def test_all_entry_points_installed_and_respond_to_help(self):
        for name in ENTRY_POINTS:
            with self.subTest(entry_point=name):
                path = find_entry_point(name)
                self.assertIsNotNone(
                    path, f"console script {name!r} not installed in current env")
                result = run([path, "--help"], timeout=30)
                # stock-ui passes --help through to `streamlit run`, which
                # produces its own (sometimes non-zero) exit on --help; treat
                # absence of a Python traceback as success there.
                if name == "stock-ui":
                    self.assertNotIn(
                        "Traceback", result.stderr,
                        f"{name} crashed on import:\n{result.stderr[-500:]}")
                    continue
                self.assertEqual(
                    result.returncode, 0,
                    f"{name} --help exited {result.returncode}\n"
                    f"stderr (last 500): {result.stderr[-500:]}")


# ─────────────────────────────────────────────────────────────────────────────
#  2. stock-setup --non-interactive produces a valid config.env
# ─────────────────────────────────────────────────────────────────────────────

class TestSetupWizardNonInteractive(unittest.TestCase):
    """The first command a new user runs.

    Wizards are usually 0% coverage because they're interactive; the
    non-interactive flag exists precisely to make this testable. We
    assert that config.env lands in $STOCK_DIR and parses through the
    same loader the rest of the toolkit uses."""

    def test_non_interactive_writes_parseable_config(self):
        path = find_entry_point("stock-setup")
        self.assertIsNotNone(path)
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "STOCK_DIR": tmp, "MPLBACKEND": "Agg"}
            result = run([path, "--non-interactive"], env=env, timeout=30)
            self.assertEqual(
                result.returncode, 0,
                f"stock-setup --non-interactive failed\n"
                f"stdout: {result.stdout[-500:]}\nstderr: {result.stderr[-500:]}")

            cfg = pathlib.Path(tmp) / "config.env"
            self.assertTrue(cfg.exists(), f"expected {cfg} to be written")

            # Round-trips through the canonical config loader
            from stock_toolkit.common import load_config
            parsed = load_config(cfg)
            self.assertIn(
                "SYMBOLS", parsed,
                f"config.env missing SYMBOLS:\n{cfg.read_text()[:300]}")


# ─────────────────────────────────────────────────────────────────────────────
#  3. Bootstrap → score: the through-path the QUICKSTART promises
# ─────────────────────────────────────────────────────────────────────────────

class TestBootstrapThenScore(unittest.TestCase):
    """The journey doc says: stock-setup → stock-bootstrap → stock-ui.
    This verifies that subset works end-to-end as separate processes,
    with a fake yfinance injected via PYTHONPATH so no network is hit."""

    def test_bootstrap_writes_data_then_score_ranks(self):
        bootstrap = find_entry_point("stock-bootstrap")
        score     = find_entry_point("stock-score")
        self.assertIsNotNone(bootstrap)
        self.assertIsNotNone(score)

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = pathlib.Path(tmp)
            (data_dir / "config.env").write_text("SYMBOLS=AAPL,MSFT,GOOGL\n")

            # PYTHONPATH = fake-yfinance dir first → real yfinance never loaded
            env = {
                **os.environ,
                "STOCK_DIR":  str(data_dir),
                "PYTHONPATH": f"{FAKE_YF}{os.pathsep}{os.environ.get('PYTHONPATH','')}",
                "MPLBACKEND": "Agg",
            }

            # 1. bootstrap (writes data/stock_data_2024.db)
            r1 = run([bootstrap, "--range", "2024"], env=env, timeout=120)
            self.assertEqual(
                r1.returncode, 0,
                f"stock-bootstrap failed (exit {r1.returncode})\n"
                f"stdout: {r1.stdout[-500:]}\nstderr: {r1.stderr[-500:]}")

            db = data_dir / "data" / "stock_data_2024.db"
            self.assertTrue(db.exists(), f"expected {db} to exist")

            # 2. score reads the historical DB and ranks
            r2 = run([score, "--horizon", "quarter"], env=env, timeout=120)
            self.assertEqual(
                r2.returncode, 0,
                f"stock-score failed (exit {r2.returncode})\n"
                f"stdout: {r2.stdout[-500:]}\nstderr: {r2.stderr[-500:]}")

            # 3. output mentions at least one of our seeded symbols
            self.assertTrue(
                any(sym in r2.stdout for sym in ("AAPL", "MSFT", "GOOGL")),
                f"score output didn't mention any expected symbol:\n"
                f"{r2.stdout[-800:]}")


if __name__ == "__main__":
    runner = unittest.main(verbosity=2, exit=False)
    sys.exit(0 if runner.result.wasSuccessful() else 1)
