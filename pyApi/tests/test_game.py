"""
test_game.py
============
Unit tests for stock_toolkit.game (paper-trading portfolio).

Each test uses a fresh portfolio.db in a tmp dir, plus a tmp stock-data
DB with a deterministic price so buy/sell are predictable.
"""

import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT_DIR = pathlib.Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from stock_toolkit import game                                   # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
#  Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_price_db(path: pathlib.Path, prices: dict) -> None:
    """{symbol: latest_close} → sqlite DB shaped like stock_data.db."""
    con = sqlite3.connect(path)
    con.execute("""
        CREATE TABLE prices (
          fetched_at TEXT, symbol TEXT, source TEXT, timestamp TEXT,
          interval TEXT, open REAL, high REAL, low REAL, close REAL,
          volume INTEGER, vwap REAL, change_pct REAL, extra TEXT,
          UNIQUE(symbol, source, timestamp)
        )
    """)
    for sym, close in prices.items():
        con.execute(
            "INSERT INTO prices (symbol, source, timestamp, interval, close) "
            "VALUES (?, 'yfinance', '2026-06-12T00:00:00+00:00', '1d', ?)",
            (sym, close),
        )
    con.commit(); con.close()


class GameTestCase(unittest.TestCase):
    """Common setup — tmp portfolio DB + tmp price DB."""

    def setUp(self):
        self.tmp     = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.port_db = pathlib.Path(self.tmp.name) / "portfolio.db"
        self.price_db = pathlib.Path(self.tmp.name) / "stock_data.db"
        _make_price_db(self.price_db, {"AAPL": 200.0, "MSFT": 400.0})
        # Patch the price-discovery to use our tmp DB only
        patcher = mock.patch(
            "stock_toolkit.game._discover_data_dbs",
            return_value=[self.price_db],
        )
        patcher.start()
        self.addCleanup(patcher.stop)


# ─────────────────────────────────────────────────────────────────────────────
#  init / reset
# ─────────────────────────────────────────────────────────────────────────────

class TestInitAndReset(GameTestCase):

    def test_init_creates_portfolio(self):
        p = game.init_portfolio(starting_cash=5000.0, db=self.port_db)
        self.assertEqual(p["starting_cash"], 5000.0)
        self.assertEqual(p["cash"],          5000.0)

    def test_init_is_idempotent(self):
        game.init_portfolio(starting_cash=5000.0, db=self.port_db)
        # Burn a trade so cash != starting
        game.buy("AAPL", 500.0, db=self.port_db)
        p = game.init_portfolio(starting_cash=9999.0, db=self.port_db)
        # init never overwrites; cash reflects prior trade, not 9999
        self.assertEqual(p["starting_cash"], 5000.0)
        self.assertLess(p["cash"], 5000.0)

    def test_reset_wipes_trades_and_cash(self):
        game.init_portfolio(starting_cash=5000.0, db=self.port_db)
        game.buy("AAPL", 500.0, db=self.port_db)
        self.assertGreater(len(game.get_trades(db=self.port_db)), 0)

        p = game.reset_portfolio(starting_cash=8000.0, db=self.port_db)
        self.assertEqual(p["starting_cash"], 8000.0)
        self.assertEqual(p["cash"],          8000.0)
        self.assertEqual(game.get_trades(db=self.port_db), [])


# ─────────────────────────────────────────────────────────────────────────────
#  Buy / sell
# ─────────────────────────────────────────────────────────────────────────────

class TestBuy(GameTestCase):

    def setUp(self):
        super().setUp()
        game.init_portfolio(starting_cash=10_000.0, db=self.port_db)

    def test_buy_applies_slippage_premium(self):
        # AAPL = 200, slippage = 10 bps → fill 200.2
        out = game.buy("AAPL", 1002.0, db=self.port_db)
        self.assertAlmostEqual(out["fill_price"], 200.2, places=4)
        self.assertAlmostEqual(out["qty"], 1002.0 / 200.2, places=6)

    def test_buy_reduces_cash_by_full_spend(self):
        game.buy("AAPL", 1500.0, db=self.port_db)
        p = game.get_portfolio(db=self.port_db)
        self.assertAlmostEqual(p["cash"], 10_000.0 - 1500.0, places=4)

    def test_buy_requires_positive_amount(self):
        with self.assertRaises(game.GameError):
            game.buy("AAPL", 0.0, db=self.port_db)
        with self.assertRaises(game.GameError):
            game.buy("AAPL", -100.0, db=self.port_db)

    def test_buy_blocks_insufficient_cash(self):
        with self.assertRaises(game.GameError):
            game.buy("AAPL", 10_001.0, db=self.port_db)

    def test_buy_unknown_symbol_raises(self):
        with self.assertRaises(game.GameError):
            game.buy("NOPE", 500.0, db=self.port_db)


class TestSell(GameTestCase):

    def setUp(self):
        super().setUp()
        game.init_portfolio(starting_cash=10_000.0, db=self.port_db)
        game.buy("AAPL", 2000.0, db=self.port_db)

    def test_sell_partial(self):
        positions_before = game.get_positions(db=self.port_db)
        held = positions_before["AAPL"]["qty"]
        out  = game.sell("AAPL", held / 2, db=self.port_db)
        self.assertAlmostEqual(out["qty"], held / 2, places=6)

        positions_after = game.get_positions(db=self.port_db)
        self.assertAlmostEqual(positions_after["AAPL"]["qty"], held / 2,
                               places=6)

    def test_sell_all_closes_position(self):
        game.sell("AAPL", db=self.port_db)   # qty=None → full
        self.assertNotIn("AAPL", game.get_positions(db=self.port_db))

    def test_sell_more_than_held_raises(self):
        held = game.get_positions(db=self.port_db)["AAPL"]["qty"]
        with self.assertRaises(game.GameError):
            game.sell("AAPL", held * 2, db=self.port_db)

    def test_sell_nonexistent_position_raises(self):
        with self.assertRaises(game.GameError):
            game.sell("MSFT", 1.0, db=self.port_db)


# ─────────────────────────────────────────────────────────────────────────────
#  Positions, cost basis, mark-to-market
# ─────────────────────────────────────────────────────────────────────────────

class TestPositions(GameTestCase):

    def test_weighted_average_cost_basis(self):
        game.init_portfolio(starting_cash=10_000.0, db=self.port_db)
        # Buy at 200.2 (fill), then update price + buy again at higher fill
        game.buy("AAPL", 1000.0, db=self.port_db)
        # Bump close to 300 → fill 300.3
        con = sqlite3.connect(self.price_db)
        con.execute(
            "INSERT OR REPLACE INTO prices (symbol, source, timestamp, "
            "interval, close) VALUES ('AAPL', 'yfinance', "
            "'2026-06-13T00:00:00+00:00', '1d', 300.0)"
        )
        con.commit(); con.close()
        game.buy("AAPL", 1500.0, db=self.port_db)

        pos = game.get_positions(db=self.port_db)["AAPL"]
        # qty_1 = 1000/200.2, qty_2 = 1500/300.3
        # avg = (1000 + 1500) / (qty_1 + qty_2)
        q1 = 1000.0 / 200.2
        q2 = 1500.0 / 300.3
        expected_avg = 2500.0 / (q1 + q2)
        self.assertAlmostEqual(pos["avg_cost"], expected_avg, places=4)

    def test_mark_to_market_totals(self):
        game.init_portfolio(starting_cash=10_000.0, db=self.port_db)
        game.buy("AAPL", 2002.0, db=self.port_db)   # fill 200.2 → 10 shares
        mtm = game.mark_to_market(db=self.port_db)
        # cash = 10000 - 2002 = 7998; equity = 10 shares × 200 = 2000
        self.assertAlmostEqual(mtm["cash"],   7998.0, places=2)
        self.assertAlmostEqual(mtm["equity"], 2000.0, places=2)
        self.assertAlmostEqual(mtm["total"],  9998.0, places=2)
        # Unrealised: 2000 - 2002 = -2 (the slippage premium)
        h = mtm["holdings"][0]
        self.assertAlmostEqual(h["pnl"], -2.0, places=2)


# ─────────────────────────────────────────────────────────────────────────────
#  Multi-portfolio
# ─────────────────────────────────────────────────────────────────────────────

class TestMultiPortfolio(GameTestCase):
    """Each portfolio has its own trades, cash, and reset state."""

    def test_create_sets_active(self):
        a = game.create_portfolio("Aggressive", starting_cash=5000.0,
                                  db=self.port_db)
        self.assertEqual(a["name"], "Aggressive")
        self.assertEqual(game.get_active_portfolio_id(db=self.port_db),
                         a["id"])

    def test_isolation_between_portfolios(self):
        a = game.create_portfolio("A", starting_cash=10_000.0,
                                  db=self.port_db)
        b = game.create_portfolio("B", starting_cash=10_000.0,
                                  db=self.port_db)
        game.set_active_portfolio(a["id"], db=self.port_db)
        game.buy("AAPL", 1000.0, db=self.port_db)
        game.set_active_portfolio(b["id"], db=self.port_db)
        game.buy("MSFT", 2000.0, db=self.port_db)

        self.assertEqual(
            len(game.get_trades(portfolio_id=a["id"], db=self.port_db)), 1)
        self.assertEqual(
            len(game.get_trades(portfolio_id=b["id"], db=self.port_db)), 1)
        self.assertIn("AAPL", game.get_positions(portfolio_id=a["id"],
                                                 db=self.port_db))
        self.assertNotIn("AAPL", game.get_positions(portfolio_id=b["id"],
                                                    db=self.port_db))

    def test_reset_only_active(self):
        a = game.create_portfolio("A", starting_cash=10_000.0,
                                  db=self.port_db)
        b = game.create_portfolio("B", starting_cash=5_000.0,
                                  db=self.port_db)
        game.set_active_portfolio(a["id"], db=self.port_db)
        game.buy("AAPL", 1000.0, db=self.port_db)
        game.set_active_portfolio(b["id"], db=self.port_db)
        game.buy("MSFT", 500.0, db=self.port_db)

        game.reset_portfolio(starting_cash=999.0, db=self.port_db)
        # B was active; A unchanged
        self.assertEqual(
            game.get_portfolio(portfolio_id=a["id"],
                               db=self.port_db)["cash"],
            10_000.0 - 1000.0)
        self.assertEqual(
            game.get_portfolio(portfolio_id=b["id"],
                               db=self.port_db)["cash"],
            999.0)

    def test_duplicate_name_rejected(self):
        game.create_portfolio("X", db=self.port_db)
        with self.assertRaises(game.GameError):
            game.create_portfolio("X", db=self.port_db)

    def test_archive_moves_active(self):
        a = game.create_portfolio("A", db=self.port_db)
        b = game.create_portfolio("B", db=self.port_db)
        game.set_active_portfolio(a["id"], db=self.port_db)
        game.archive_portfolio(a["id"], db=self.port_db)
        self.assertEqual(game.get_active_portfolio_id(db=self.port_db),
                         b["id"])
        self.assertEqual(
            [p["name"] for p in game.list_portfolios(db=self.port_db)],
            ["B"])
        self.assertEqual(
            {p["name"] for p in
             game.list_portfolios(include_archived=True, db=self.port_db)},
            {"A", "B"})

    def test_delete_cascades_trades(self):
        a = game.create_portfolio("A", db=self.port_db)
        game.buy("AAPL", 500.0, db=self.port_db)
        self.assertEqual(
            len(game.get_trades(portfolio_id=a["id"], db=self.port_db)), 1)
        game.delete_portfolio(a["id"], db=self.port_db)
        self.assertEqual(
            [p["id"] for p in game.list_portfolios(db=self.port_db)], [])

    def test_no_active_raises_helpfully(self):
        with self.assertRaises(game.GameError):
            game.buy("AAPL", 500.0, db=self.port_db)


class TestMigrationFromV1(unittest.TestCase):
    """An old single-portfolio DB is migrated transparently to v2."""

    def setUp(self):
        self.tmp     = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.port_db = pathlib.Path(self.tmp.name) / "portfolio.db"
        self.price_db = pathlib.Path(self.tmp.name) / "stock_data.db"
        _make_price_db(self.price_db, {"AAPL": 200.0})
        patcher = mock.patch(
            "stock_toolkit.game._discover_data_dbs",
            return_value=[self.price_db],
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        # Hand-built v1 schema with one portfolio and one trade
        con = sqlite3.connect(self.port_db)
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
            INSERT INTO trades (timestamp, symbol, side, qty, price,
                                fill_price, cash_delta) VALUES
                ('2026-05-02T10:00:00+00:00', 'AAPL', 'buy', 7.4925,
                 200.0, 200.2, -1500.0);
        """)
        con.commit(); con.close()

    def test_migration_preserves_portfolio_state(self):
        p = game.init_portfolio(db=self.port_db)
        self.assertEqual(p["name"], "Default")
        self.assertEqual(p["cash"], 8500.0)
        self.assertEqual(p["starting_cash"], 10000.0)
        self.assertEqual(p["created_at"], "2026-05-01T00:00:00+00:00")

    def test_migration_preserves_trades(self):
        game.init_portfolio(db=self.port_db)
        trades = game.get_trades(db=self.port_db)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["symbol"], "AAPL")
        self.assertAlmostEqual(trades[0]["qty"], 7.4925, places=4)

    def test_migration_drops_old_singular_table(self):
        game.init_portfolio(db=self.port_db)
        con = sqlite3.connect(self.port_db)
        row = con.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='portfolio'"
        ).fetchone()
        con.close()
        self.assertIsNone(row,
                          "old singular 'portfolio' table should be dropped")

    def test_migration_idempotent_on_v2(self):
        game.init_portfolio(db=self.port_db)
        p = game.init_portfolio(db=self.port_db)
        self.assertEqual(p["name"], "Default")


# ─────────────────────────────────────────────────────────────────────────────
#  UI page renders
# ─────────────────────────────────────────────────────────────────────────────

class TestBenchmarkHistory(GameTestCase):
    """Equal-weight buy-and-hold series for chart overlay."""

    def setUp(self):
        super().setUp()
        # Replace the synthetic price DB with a multi-day series we control
        con = sqlite3.connect(self.price_db)
        con.execute("DELETE FROM prices")
        # AAPL: 100 → 120 (+20%)  MSFT: 200 → 200 (flat)
        dates = ["2026-05-01", "2026-05-02", "2026-05-05"]
        for ts in dates:
            con.execute(
                "INSERT INTO prices (symbol, source, timestamp, interval, "
                "close) VALUES ('AAPL', 'yfinance', ?, '1d', "
                f"{100 + (10 if ts > '2026-05-01' else 0) + (10 if ts > '2026-05-02' else 0)})",
                (ts + "T00:00:00+00:00",),
            )
            con.execute(
                "INSERT INTO prices (symbol, source, timestamp, interval, "
                "close) VALUES ('MSFT', 'yfinance', ?, '1d', 200.0)",
                (ts + "T00:00:00+00:00",),
            )
        con.commit(); con.close()

    def test_equal_weight_split(self):
        import datetime
        hist = game.benchmark_history(
            ["AAPL", "MSFT"], starting_cash=10_000.0,
            start_date=datetime.date(2026, 5, 1),
        )
        # Start: half in AAPL @100 (50 sh), half in MSFT @200 (25 sh).
        # Day 1 value = 5000 + 5000 = 10000
        self.assertGreater(len(hist), 0)
        self.assertAlmostEqual(hist[0]["value"], 10_000.0, delta=1.0)

    def test_value_tracks_price_changes(self):
        import datetime
        hist = game.benchmark_history(
            ["AAPL", "MSFT"], starting_cash=10_000.0,
            start_date=datetime.date(2026, 5, 1),
        )
        # AAPL went 100 → 120 (+20%), MSFT flat → portfolio +10%
        final = next(h for h in hist if h["date"] == "2026-05-05")
        self.assertAlmostEqual(final["value"], 11_000.0, delta=20.0)

    def test_empty_symbols_returns_empty(self):
        import datetime
        self.assertEqual(
            game.benchmark_history([], 10_000.0, datetime.date(2026, 5, 1)),
            [],
        )

    def test_no_price_data_returns_empty(self):
        import datetime
        self.assertEqual(
            game.benchmark_history(
                ["NEVER_SEEN"], 10_000.0, datetime.date(2026, 5, 1)),
            [],
        )


class TestGamePageRenders(unittest.TestCase):
    """Same pattern as the admin page test — drive the page shim
    through AppTest, expect zero exceptions."""

    def test_game_page_renders(self):
        from streamlit.testing.v1 import AppTest

        page = (pathlib.Path(__file__).parent.parent
                / "stock_toolkit" / "ui" / "pages" / "02_🎮_Game.py")
        self.assertTrue(page.exists())
        at = AppTest.from_file(str(page), default_timeout=60)
        at.run()
        self.assertEqual([e.value for e in at.exception], [])


class TestStrategyComparisonGuard(GameTestCase):
    """Comparison expander is conditional on >1 portfolio.

    Verifies the underlying value_history() call works per-portfolio
    (which is what the comparison overlay relies on) — UI render is
    covered by TestGamePageRenders."""

    def test_value_history_isolated_per_portfolio(self):
        # Two portfolios, only the first trades. Second must report empty.
        p1 = game.create_portfolio("alpha", 10_000.0, db=self.port_db)
        p2 = game.create_portfolio("beta",  10_000.0, db=self.port_db)
        game.buy("AAPL", 1_000.0, portfolio_id=p1["id"], db=self.port_db)

        h1 = game.value_history(portfolio_id=p1["id"], db=self.port_db)
        h2 = game.value_history(portfolio_id=p2["id"], db=self.port_db)
        self.assertGreater(len(h1), 0)
        self.assertGreater(len(h2), 0)
        # p2 never traded → curve is flat at starting cash; p1's curve
        # must differ at least once thanks to the AAPL buy.
        p2_totals = {round(r["total"], 2) for r in h2}
        self.assertEqual(p2_totals, {10_000.0})
        p1_totals = {round(r["total"], 2) for r in h1}
        self.assertNotEqual(p1_totals, {10_000.0})


if __name__ == "__main__":
    runner = unittest.main(verbosity=2, exit=False)
    sys.exit(0 if runner.result.wasSuccessful() else 1)
