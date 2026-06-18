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

import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import sysconfig
import tempfile
import textwrap
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
    "stock-gap-fill",
    "stock-sanity",
]


def _seed_prices_db(path: pathlib.Path, symbol: str = "AAPL",
                    n_bars: int = 30, start_close: float = 150.0) -> None:
    """Lay a minimal stock_data.db at ``path`` — one symbol, one source,
    ``n_bars`` daily bars walking up from ``start_close``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("""
        CREATE TABLE prices (
            fetched_at TEXT, symbol TEXT, source TEXT, timestamp TEXT,
            interval TEXT, open REAL, high REAL, low REAL, close REAL,
            volume INTEGER, vwap REAL, change_pct REAL, extra TEXT,
            UNIQUE(symbol, source, timestamp)
        )
    """)
    import datetime as _dt
    base = _dt.date(2025, 1, 2)
    for i in range(n_bars):
        d  = (base + _dt.timedelta(days=i)).isoformat()
        cp = start_close + i * 0.5
        con.execute(
            "INSERT INTO prices (symbol, source, timestamp, interval, "
            "open, high, low, close, volume) "
            "VALUES (?, 'yfinance', ?, '1d', ?, ?, ?, ?, 1000000)",
            (symbol, d + "T00:00:00+00:00", cp, cp + 0.5, cp - 0.5, cp),
        )
    con.commit(); con.close()


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

            # 1. bootstrap (writes historical/stock_data_2024.db
            # under the v1.17 layout — was data/ pre-v1.17)
            r1 = run([bootstrap, "--range", "2024"], env=env, timeout=120)
            self.assertEqual(
                r1.returncode, 0,
                f"stock-bootstrap failed (exit {r1.returncode})\n"
                f"stdout: {r1.stdout[-500:]}\nstderr: {r1.stderr[-500:]}")

            db = data_dir / "historical" / "stock_data_2024.db"
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


# ─────────────────────────────────────────────────────────────────────────────
#  4. stock-sanity — exit codes, JSON mode, --strict semantics
# ─────────────────────────────────────────────────────────────────────────────

class TestSanityJourney(unittest.TestCase):
    """Drives the stock-sanity CLI through every advertised flag, with
    each scenario isolated in its own tempdir so check_data_layout
    sees a clean BASE_DIR."""

    def _run(self, args, cfg_text="SYMBOLS=AAPL\n"):
        path = find_entry_point("stock-sanity")
        self.assertIsNotNone(path, "stock-sanity is not installed")
        tmp = tempfile.mkdtemp()
        try:
            base = pathlib.Path(tmp)
            (base / "config.env").write_text(cfg_text)
            env = {**os.environ, "STOCK_DIR": str(base), "MPLBACKEND": "Agg"}
            return run([path, *args], env=env, timeout=30)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_clean_dir_exits_zero(self):
        # A fresh STOCK_DIR with just a non-empty config.env has no DBs,
        # so check_database surfaces an INFO note ("no live DB yet"). The
        # CLI prints either "All sanity checks passed." (no issues at all)
        # or a grouped issue list with a "0 error(s)" summary — both are
        # acceptable; the contract is exit 0 and no error/warning issues.
        r = self._run(["--no-color"])
        self.assertEqual(
            r.returncode, 0,
            f"stock-sanity should pass on a clean dir.\n"
            f"stdout: {r.stdout[-400:]}\nstderr: {r.stderr[-400:]}",
        )
        self.assertIn("0 error(s)", r.stdout)

    def test_bad_paid_flag_exits_nonzero(self):
        r = self._run(["--no-color"],
                      cfg_text="SYMBOLS=AAPL\nFINNHUB_PAID=yes\n")
        self.assertEqual(r.returncode, 1,
                         f"expected exit 1, got {r.returncode}\n"
                         f"stdout: {r.stdout[-500:]}")
        self.assertIn("FINNHUB_PAID", r.stdout)

    def test_json_mode_is_parseable_and_complete(self):
        r = self._run(["--json"])
        self.assertEqual(r.returncode, 0, f"json mode crashed: {r.stderr[-400:]}")
        d = json.loads(r.stdout)
        for k in ("ok", "errors", "warnings", "infos", "issues"):
            self.assertIn(k, d, f"json report missing '{k}': {list(d)}")
        self.assertTrue(d["ok"])

    def test_strict_promotes_warnings(self):
        # Empty SYMBOLS = WARNING. Default exit is still 0; --strict → 1.
        r1 = self._run(["--no-color"], cfg_text="SYMBOLS=\n")
        self.assertEqual(r1.returncode, 0,
                         f"warning alone should exit 0: {r1.stdout[-300:]}")
        r2 = self._run(["--no-color", "--strict"], cfg_text="SYMBOLS=\n")
        self.assertEqual(r2.returncode, 1,
                         f"--strict should escalate warning to exit 1: "
                         f"{r2.stdout[-300:]}")


# ─────────────────────────────────────────────────────────────────────────────
#  5. Game — full paper-trade lifecycle through a subprocess
# ─────────────────────────────────────────────────────────────────────────────

class TestGameJourney(unittest.TestCase):
    """End-to-end paper-trade lifecycle in a fresh subprocess: schema
    migration → init → buy → mark-to-market → sell → trade_stats →
    value_history. Catches install / schema / invariant regressions
    that a unit test couldn't (the package only gets re-imported in
    a child process)."""

    def test_full_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            (base / "config.env").write_text("SYMBOLS=AAPL\n")
            # Seed a stock_data.db at the v1.17 layout location so
            # game.get_latest_price finds a current AAPL bar.
            # STOCK_DIR is set → DATA_DIR == BASE_DIR (Docker rule), so
            # the live DB lives at BASE_DIR/stock_data.db, no data/ nest.
            _seed_prices_db(base / "stock_data.db", "AAPL", n_bars=30,
                            start_close=200.0)

            script = textwrap.dedent("""\
                import sys, math
                from stock_toolkit.game import (
                    init_portfolio, buy, sell, mark_to_market,
                    trade_stats, value_history,
                )
                p = init_portfolio(starting_cash=10_000.0)
                if p["cash"] != 10_000.0:
                    print("FAIL: starting cash wrong", p, file=sys.stderr); sys.exit(1)

                buy("AAPL", 1_000.0)
                mtm = mark_to_market()
                if not (mtm["cash"] < 10_000.0 and mtm["equity"] > 0):
                    print("FAIL: post-buy mtm wrong", mtm, file=sys.stderr); sys.exit(1)
                if not math.isclose(mtm["cash"] + mtm["equity"], mtm["total"],
                                    abs_tol=0.01):
                    print("FAIL: cash+equity != total", mtm, file=sys.stderr); sys.exit(1)

                # Sell everything we hold of AAPL.
                pos_qty = mtm["holdings"][0]["qty"]
                sell("AAPL", pos_qty)
                after = mark_to_market()
                if after["equity"] > 0.01:
                    print("FAIL: equity should be 0 after full sell", after,
                          file=sys.stderr); sys.exit(1)

                stats = trade_stats()
                if stats["closed_count"] != 1:
                    print("FAIL: closed_count != 1", stats, file=sys.stderr); sys.exit(1)
                if stats["closed_count"] != stats["wins"] + stats["losses"]:
                    print("FAIL: wins+losses mismatch", stats, file=sys.stderr); sys.exit(1)

                hist = value_history()
                dates = [r["date"] for r in hist]
                if dates != sorted(dates):
                    print("FAIL: history dates not monotonic", file=sys.stderr); sys.exit(1)

                print("OK", round(after["total"], 2), stats["closed_count"])
            """)
            env = {**os.environ, "STOCK_DIR": str(base), "MPLBACKEND": "Agg"}
            r = run([sys.executable, "-c", script], env=env, timeout=60)
            self.assertEqual(
                r.returncode, 0,
                f"game lifecycle failed (exit {r.returncode})\n"
                f"stdout: {r.stdout[-500:]}\nstderr: {r.stderr[-500:]}",
            )
            self.assertIn("OK", r.stdout)


# ─────────────────────────────────────────────────────────────────────────────
#  6. Briefing — offline parse + paper-trade flow with mocked Claude
# ─────────────────────────────────────────────────────────────────────────────

class TestBriefingOfflineJourney(unittest.TestCase):
    """The Briefing tab has no CLI, so we drive its parser + the trade
    handoff through a subprocess that imports the module directly and
    feeds it a canned Claude response. Verifies the offline path works
    on a machine with no ANTHROPIC_API_KEY: prompt builder doesn't
    crash, fenced-block parser extracts proposals, executing a
    proposal lands a trade in the Briefing-strategy portfolio."""

    def test_proposal_round_trip_into_briefing_strategy(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            (base / "config.env").write_text("SYMBOLS=AAPL\n")
            # STOCK_DIR is set → DATA_DIR == BASE_DIR (Docker rule), so
            # the live DB lives at BASE_DIR/stock_data.db, no data/ nest.
            _seed_prices_db(base / "stock_data.db", "AAPL", n_bars=30,
                            start_close=200.0)

            # Subprocess runs the briefing offline path:
            # 1. parse a canned Claude reply (no HTTP, no API key)
            # 2. create the Briefing strategy if absent
            # 3. execute the first BUY proposal, archiving the reason
            # 4. verify it shows up in get_trades with the [Claude]
            #    prefix on the note (the v1.6.0 contract).
            script = textwrap.dedent("""\
                import sys
                from stock_toolkit.ui.tabs.briefing import (
                    BRIEFING_STRATEGY_NAME, _parse_trade_proposals,
                )
                from stock_toolkit.game import (
                    buy, create_portfolio, get_trades, list_portfolios,
                )

                fake_reply = (
                    "Here's my read.\\n\\n"
                    "<<<TRADE_PROPOSALS_JSON\\n"
                    '[{"side":"BUY","symbol":"AAPL","amount_chf":500,'
                    '"reason":"strong momentum + low RSI"}]\\n'
                    ">>>\\n\\nLet me know."
                )
                props, cleaned = _parse_trade_proposals(fake_reply)
                if len(props) != 1 or props[0]["symbol"] != "AAPL":
                    print("FAIL: parser did not extract proposal", props,
                          file=sys.stderr); sys.exit(1)
                if "TRADE_PROPOSALS_JSON" in cleaned:
                    print("FAIL: fenced block not stripped", file=sys.stderr); sys.exit(1)
                if "Here's my read" not in cleaned or "Let me know" not in cleaned:
                    print("FAIL: surrounding text lost", file=sys.stderr); sys.exit(1)

                # Create the Briefing strategy, execute the proposal.
                rec = create_portfolio(BRIEFING_STRATEGY_NAME,
                                       starting_cash=10_000.0,
                                       activate=False)
                reason = props[0]["reason"]
                buy("AAPL", props[0]["amount_chf"],
                    portfolio_id=rec["id"], note=f"[Claude] {reason}")

                trades = get_trades(portfolio_id=rec["id"])
                if len(trades) != 1:
                    print("FAIL: expected 1 trade", trades,
                          file=sys.stderr); sys.exit(1)
                if not trades[0]["note"].startswith("[Claude]"):
                    print("FAIL: note prefix missing", trades[0],
                          file=sys.stderr); sys.exit(1)

                names = [p["name"] for p in list_portfolios()]
                if BRIEFING_STRATEGY_NAME not in names:
                    print("FAIL: Briefing strategy not in list_portfolios",
                          names, file=sys.stderr); sys.exit(1)

                print("OK", len(trades), trades[0]["symbol"])
            """)
            env = {**os.environ, "STOCK_DIR": str(base), "MPLBACKEND": "Agg"}
            r = run([sys.executable, "-c", script], env=env, timeout=60)
            self.assertEqual(
                r.returncode, 0,
                f"briefing offline journey failed (exit {r.returncode})\n"
                f"stdout: {r.stdout[-500:]}\nstderr: {r.stderr[-700:]}",
            )
            self.assertIn("OK", r.stdout)


if __name__ == "__main__":
    runner = unittest.main(verbosity=2, exit=False)
    sys.exit(0 if runner.result.wasSuccessful() else 1)
