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

    def test_get_trades_returns_row_id(self):
        # v2.4.4: trades-table id is the ordering source of truth.
        # Two same-symbol buys produce distinct, monotonic ids — the
        # UI surfaces them so back-to-back clicks (which may share a
        # second-precision timestamp) are visibly distinguishable.
        game.buy("AAPL", 100.0, db=self.port_db)
        game.buy("AAPL", 100.0, db=self.port_db)
        trades = game.get_trades(db=self.port_db)
        self.assertEqual(len(trades), 2)
        self.assertIn("id", trades[0])
        self.assertIn("id", trades[1])
        # Monotonic (FIFO order); distinct even if timestamps collide.
        self.assertLess(trades[0]["id"], trades[1]["id"])


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


class TestTradeNotesAndStats(GameTestCase):
    """v1.7 — per-trade notes round-trip + outcome stats math (FIFO matching)."""

    def setUp(self):
        super().setUp()
        game.init_portfolio(starting_cash=10_000.0, db=self.port_db)

    def test_note_round_trip(self):
        game.buy("AAPL", 1_000.0, db=self.port_db,
                 note="RSI oversold, betting on bounce")
        rows = game.get_trades(db=self.port_db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["note"], "RSI oversold, betting on bounce")

    def test_note_defaults_to_empty(self):
        game.buy("AAPL", 1_000.0, db=self.port_db)
        rows = game.get_trades(db=self.port_db)
        self.assertEqual(rows[0]["note"], "")

    def test_stats_zero_with_no_closes(self):
        game.buy("AAPL", 1_000.0, db=self.port_db)
        s = game.trade_stats(db=self.port_db)
        self.assertEqual(s["total_trades"], 1)
        self.assertEqual(s["closed_count"], 0)
        self.assertEqual(s["wins"],   0)
        self.assertEqual(s["losses"], 0)
        self.assertEqual(s["realized_pnl"], 0.0)

    def test_stats_win_and_loss_fifo(self):
        # AAPL: buy 1 @ 200 cost, then a sale at 200 fill ≈ flat slippage hits
        # → not enough to be a win. Force a win via direct trade insert with
        # known fill prices instead.
        import sqlite3
        con = sqlite3.connect(self.port_db)
        # Bypass slippage to set up clean numbers: 2 buys of 10sh @ 100, then
        # sell 10sh @ 150 (win +500), and 10sh @ 80 (loss -200).
        con.execute(
            "INSERT INTO trades (portfolio_id, timestamp, symbol, side, qty, "
            "price, fill_price, cash_delta, note) VALUES "
            "(1, '2026-01-01', 'AAPL', 'buy',  10, 100, 100, -1000, NULL),"
            "(1, '2026-01-02', 'AAPL', 'buy',  10, 100, 100, -1000, NULL),"
            "(1, '2026-01-03', 'AAPL', 'sell', 10, 150, 150,  1500, NULL),"
            "(1, '2026-01-04', 'AAPL', 'sell', 10,  80,  80,   800, NULL)"
        )
        con.commit(); con.close()
        s = game.trade_stats(db=self.port_db)
        self.assertEqual(s["closed_count"], 2)
        self.assertEqual(s["wins"],   1)
        self.assertEqual(s["losses"], 1)
        self.assertAlmostEqual(s["win_rate"], 0.5)
        self.assertAlmostEqual(s["avg_win"],   500.0)
        self.assertAlmostEqual(s["avg_loss"], -200.0)
        self.assertAlmostEqual(s["expectancy"], 150.0)   # 0.5*500 + 0.5*-200
        self.assertAlmostEqual(s["realized_pnl"], 300.0)


class TestRiskStats(GameTestCase):
    """v1.8 — CAGR / Sharpe / Sortino / max DD from the value_history curve."""

    def test_zeros_for_empty_history(self):
        game.init_portfolio(starting_cash=10_000.0, db=self.port_db)
        rs = game.risk_stats(db=self.port_db)
        # No trades and only one day on the curve → all zero.
        self.assertEqual(rs["sharpe"],  0.0)
        self.assertEqual(rs["sortino"], 0.0)
        self.assertEqual(rs["max_dd"],  0.0)

    def test_max_dd_negative_when_drawdown(self):
        # Build a synthetic 4-day curve by inserting trades whose
        # marks-to-market move down then up. Easier: monkeypatch
        # value_history to return a known curve.
        fake = [
            {"date": "2026-01-01", "cash": 0, "equity": 0, "total": 1000.0},
            {"date": "2026-01-02", "cash": 0, "equity": 0, "total": 1200.0},
            {"date": "2026-01-03", "cash": 0, "equity": 0, "total":  900.0},
            {"date": "2026-01-04", "cash": 0, "equity": 0, "total": 1100.0},
        ]
        with mock.patch("stock_toolkit.game.value_history",
                        return_value=fake):
            rs = game.risk_stats()
        # Peak 1200, trough 900 → DD ≈ -25%.
        self.assertAlmostEqual(rs["max_dd"], -25.0, places=2)
        # CAGR is heavily annualised over 3 days so we just sanity-check sign.
        self.assertGreater(rs["cagr"], 0.0)
        # Sortino ≥ 0 here (one down day, ends up).
        self.assertGreaterEqual(rs["sortino"], 0.0)

    def test_sortino_caps_at_sharpe_when_no_down_days(self):
        fake = [
            {"date": "2026-01-01", "cash": 0, "equity": 0, "total": 1000.0},
            {"date": "2026-01-02", "cash": 0, "equity": 0, "total": 1010.0},
            {"date": "2026-01-03", "cash": 0, "equity": 0, "total": 1020.0},
        ]
        with mock.patch("stock_toolkit.game.value_history",
                        return_value=fake):
            rs = game.risk_stats()
        # Only up days → sortino guarded against ∞ by mirroring sharpe.
        self.assertEqual(rs["sortino"], rs["sharpe"])
        self.assertEqual(rs["max_dd"], 0.0)


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


class TestGameEdges(GameTestCase):
    """Error paths + less-trodden helpers (rename/archive/delete/
    benchmark/risk_stats)."""

    def setUp(self):
        super().setUp()
        self.p = game.create_portfolio("Main", starting_cash=10_000.0,
                                       db=self.port_db, activate=True)

    def test_rename_to_empty_raises(self):
        with self.assertRaises(game.GameError):
            game.rename_portfolio(self.p["id"], "", db=self.port_db)

    def test_rename_nonexistent_raises(self):
        with self.assertRaises(game.GameError):
            game.rename_portfolio(99999, "X", db=self.port_db)

    def test_rename_duplicate_raises(self):
        game.create_portfolio("Other", db=self.port_db, activate=False)
        with self.assertRaises(game.GameError):
            game.rename_portfolio(self.p["id"], "Other", db=self.port_db)

    def test_rename_same_name_is_noop(self):
        game.rename_portfolio(self.p["id"], "Main", db=self.port_db)

    def test_archive_and_unarchive(self):
        other = game.create_portfolio("Keep", db=self.port_db, activate=False)
        game.archive_portfolio(self.p["id"], db=self.port_db)
        names = [p["name"] for p in game.list_portfolios(db=self.port_db)]
        self.assertNotIn("Main", names)
        all_names = [p["name"] for p in
                     game.list_portfolios(include_archived=True, db=self.port_db)]
        self.assertIn("Main", all_names)
        game.unarchive_portfolio(self.p["id"], db=self.port_db)
        self.assertIn("Main", [p["name"] for p in
                               game.list_portfolios(db=self.port_db)])
        self.assertTrue(other)

    def test_delete_portfolio(self):
        victim = game.create_portfolio("Doomed", db=self.port_db, activate=False)
        game.delete_portfolio(victim["id"], db=self.port_db)
        ids = [p["id"] for p in
               game.list_portfolios(include_archived=True, db=self.port_db)]
        self.assertNotIn(victim["id"], ids)

    def test_get_portfolio_unknown_returns_empty(self):
        self.assertEqual(game.get_portfolio(portfolio_id=99999,
                                            db=self.port_db), {})

    def test_get_latest_price_unknown_symbol(self):
        price, _ = game.get_latest_price("ZZZZ")
        self.assertIsNone(price)

    def test_benchmark_history(self):
        import datetime
        hist = game.benchmark_history(["AAPL", "MSFT"], 10_000.0,
                                      datetime.date(2020, 1, 1))
        self.assertIsInstance(hist, list)

    def test_benchmark_history_no_symbols(self):
        import datetime
        self.assertEqual(
            game.benchmark_history([], 10_000.0, datetime.date(2020, 1, 1)), [])

    def test_risk_stats_after_trades(self):
        game.buy("AAPL", 1_000.0, portfolio_id=self.p["id"], db=self.port_db)
        game.sell("AAPL", portfolio_id=self.p["id"], db=self.port_db)
        stats = game.risk_stats(portfolio_id=self.p["id"], db=self.port_db)
        self.assertIsInstance(stats, dict)


class TestValueHistoryReconciles(GameTestCase):
    """The value-over-time curve's last point must equal mark_to_market.

    Regression: value_history only loaded price bars dated >= the
    portfolio's creation date, so a holding whose most recent bar predated
    the portfolio (stale data — e.g. an EU ticker not collected recently)
    was valued at 0 for the ENTIRE curve. The graph then read far below the
    headline return. The fixture prices are dated 2026-06-12 while the
    portfolio is created 'now' (later), so this reproduces the trigger.
    """

    def test_stale_priced_holding_valued_and_reconciles(self):
        game.init_portfolio(starting_cash=10_000.0, db=self.port_db)
        game.buy("AAPL", 2_000.0, db=self.port_db)      # priced at 200

        mtm = game.mark_to_market(db=self.port_db)
        vh  = game.value_history(db=self.port_db)
        self.assertTrue(vh, "expected a value history")
        last = vh[-1]

        # the AAPL position must contribute equity, not be zeroed out
        self.assertGreater(last["equity"], 0.0,
                           "stale-priced holding was dropped to £0")
        # and the last curve point must match the headline total
        self.assertAlmostEqual(last["total"], mtm["total"], places=2,
                               msg="value_history last point != mark_to_market")


class TestDaysSinceBar(unittest.TestCase):
    """days_since_bar backs the 'symbol isn't being collected' guard."""

    def test_today_is_zero(self):
        import datetime
        today = datetime.date.today().isoformat() + "T00:00:00+00:00"
        self.assertEqual(game.days_since_bar(today), 0)

    def test_old_bar_is_positive_and_large(self):
        self.assertGreater(game.days_since_bar("2020-01-01T00:00:00+00:00"), 1000)

    def test_none_and_garbage_return_none(self):
        self.assertIsNone(game.days_since_bar(None))
        self.assertIsNone(game.days_since_bar(""))
        self.assertIsNone(game.days_since_bar("not-a-date"))

    def test_threshold_is_a_small_positive_int(self):
        self.assertIsInstance(game.STALE_PRICE_DAYS, int)
        self.assertGreater(game.STALE_PRICE_DAYS, 0)


class TestFeeRoundTrip(GameTestCase):
    """End-to-end fee arithmetic in closed form: buy pays the slippage
    fee, the held value tracks the market exactly, sell pays the fee
    again, and a flat-price round trip costs exactly spend·2s/(1+s) —
    booked honestly as a LOSS in trade_stats, never as breakeven.

    Individual pieces (buy premium, sell discount, mtm totals) have
    their own tests; this one pins the composed business invariant that
    the user-facing numbers hang together across a full trade cycle."""

    START, SPEND, P = 10_000.0, 1_000.0, 200.0   # AAPL fixture close = 200

    def _set_price(self, close, ts="2026-06-13T00:00:00+00:00"):
        con = sqlite3.connect(self.price_db)
        con.execute(
            "INSERT OR REPLACE INTO prices (symbol, source, timestamp, "
            "interval, close) VALUES ('AAPL', 'yfinance', ?, '1d', ?)",
            (ts, close),
        )
        con.commit(); con.close()

    def test_buy_hold_move_sell_matches_closed_form(self):
        s = game.SLIPPAGE
        game.init_portfolio(starting_cash=self.START, db=self.port_db)

        # BUY: fill = P(1+s), and qty·fill == spend to the cent
        out = game.buy("AAPL", self.SPEND, db=self.port_db)
        self.assertAlmostEqual(out["fill_price"], self.P * (1 + s), places=9)
        self.assertAlmostEqual(out["qty"] * out["fill_price"], self.SPEND,
                               places=6)

        # HOLD: marked at market ⇒ equity == spend/(1+s) (down the buy fee)
        mtm = game.mark_to_market(db=self.port_db)
        self.assertAlmostEqual(mtm["equity"], self.SPEND / (1 + s), places=6)

        # MOVE: market +10% ⇒ equity == qty × new market price, exactly
        self._set_price(self.P * 1.10)
        mtm = game.mark_to_market(db=self.port_db)
        self.assertAlmostEqual(mtm["equity"], out["qty"] * self.P * 1.10,
                               places=6)

        # SELL flat (price back at P): fill = P(1−s)
        self._set_price(self.P, ts="2026-06-14T00:00:00+00:00")
        sold = game.sell("AAPL", db=self.port_db)
        self.assertAlmostEqual(sold["fill_price"], self.P * (1 - s), places=9)

        # ROUND TRIP: total == start − spend·2s/(1+s); equity flat at 0
        mtm = game.mark_to_market(db=self.port_db)
        rt_cost = self.SPEND * 2 * s / (1 + s)
        self.assertAlmostEqual(mtm["equity"], 0.0, places=9)
        self.assertAlmostEqual(mtm["total"], self.START - rt_cost, places=6)

        # HONESTY: the flat round trip is a loss of exactly the fees
        st = game.trade_stats(db=self.port_db)
        self.assertEqual((st["closed_count"], st["wins"], st["losses"]),
                         (1, 0, 1))
        self.assertAlmostEqual(st["realized_pnl"], -rt_cost, places=6)


class TestBrokerFees(GameTestCase):
    """v2.6 broker fee models: last-known per-broker schedules applied on
    top of market slippage. 'plain' (the default) charges nothing, so all
    pre-existing behavior — and TestFeeRoundTrip above — is unchanged."""

    def test_trade_fee_components(self):
        # plain: always zero
        self.assertEqual(game.trade_fee("plain", 1000, 5, "AAPL"), 0.0)
        # yuh US: 0.5% + 0.95% FX
        self.assertAlmostEqual(game.trade_fee("yuh", 1000, 5, "AAPL"),
                               5.0 + 9.5, places=9)
        # yuh minimum kicks in on tiny trades (commission max(0.5%, 1))
        self.assertAlmostEqual(game.trade_fee("yuh", 100, 1, "NESN.SW"),
                               1.0, places=9)   # .SW = CHF → no FX
        # ibkr per-share with min ... small qty → the $1 minimum
        self.assertAlmostEqual(game.trade_fee("ibkr", 1000, 10, "AAPL"),
                               1.0 + 0.0003 * 1000, places=9)
        # ibkr max cap: 1% of value on a huge share count
        self.assertAlmostEqual(game.trade_fee("ibkr", 100, 10_000, "AAPL"),
                               0.01 * 100 + 0.0003 * 100, places=9)
        # unknown broker falls back to plain (no fees)
        self.assertEqual(game.trade_fee("nope", 1000, 5, "AAPL"), 0.0)

    def test_fx_only_on_non_chf_symbols(self):
        chf = game.trade_fee("yuh", 1000, 5, "NESN.SW")
        usd = game.trade_fee("yuh", 1000, 5, "AAPL")
        eur = game.trade_fee("yuh", 1000, 5, "ENEL.MI")
        self.assertAlmostEqual(usd - chf, 9.5, places=9)
        self.assertAlmostEqual(eur, usd, places=9)

    def test_create_persists_broker_and_validates(self):
        rec = game.create_portfolio("AtYuh", db=self.port_db, broker="yuh")
        self.assertEqual(rec["broker"], "yuh")
        self.assertEqual(game.mark_to_market(db=self.port_db)["broker"], "yuh")
        with self.assertRaises(game.GameError):
            game.create_portfolio("Bad", db=self.port_db, broker="madeup")

    def test_default_broker_is_plain(self):
        game.init_portfolio(starting_cash=10_000.0, db=self.port_db)
        self.assertEqual(game.get_portfolio(db=self.port_db)["broker"],
                         "plain")

    def test_migration_adds_columns(self):
        # _connect on a fresh DB (created by init above patterns) must
        # leave broker/fee columns present
        game.init_portfolio(starting_cash=1_000.0, db=self.port_db)
        con = sqlite3.connect(self.port_db)
        pf = {r[1] for r in con.execute("PRAGMA table_info(portfolios)")}
        tr = {r[1] for r in con.execute("PRAGMA table_info(trades)")}
        con.close()
        self.assertIn("broker", pf)
        self.assertIn("fee", tr)

    def test_yuh_round_trip_costs_real_money(self):
        """Flat-price round trip on a US symbol at Yuh: both legs pay
        0.5% commission + 0.95% FX on top of 2×10 bps slippage — ≈3%,
        not the plain broker's ≈0.2%. Invariants must still hold."""
        game.create_portfolio("Yuh", starting_cash=10_000.0,
                              db=self.port_db, broker="yuh")
        out = game.buy("AAPL", 1_000.0, db=self.port_db)   # AAPL = 200
        self.assertGreater(out["fee"], 14.0)               # ~14.3 CHF
        # invariant: qty × all-in fill == spend, to the cent
        self.assertAlmostEqual(out["qty"] * out["fill_price"], 1_000.0,
                               places=6)

        sold = game.sell("AAPL", db=self.port_db)
        self.assertGreater(sold["fee"], 13.0)
        self.assertAlmostEqual(sold["qty"] * sold["fill_price"],
                               sold["proceeds"], places=6)

        mtm = game.mark_to_market(db=self.port_db)
        loss = 10_000.0 - mtm["total"]
        self.assertGreater(loss, 25.0,   "Yuh round trip must cost ~3%")
        self.assertLess(loss, 40.0)
        # honesty: booked as a fee loss, and fees are on the trade rows
        st = game.trade_stats(db=self.port_db)
        self.assertEqual(st["losses"], 1)
        trades = game.get_trades(db=self.port_db)
        self.assertTrue(all(t["fee"] > 0 for t in trades))


if __name__ == "__main__":
    runner = unittest.main(verbosity=2, exit=False)
    sys.exit(0 if runner.result.wasSuccessful() else 1)
