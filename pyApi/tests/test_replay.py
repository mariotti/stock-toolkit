"""
test_replay.py
==============
Unit tests for stock_toolkit.replay — the time-travel betting engine
behind the ⏪ Replay page. Deterministic tmp price DB, no network.
"""

import pathlib
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

SCRIPT_DIR = pathlib.Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from stock_toolkit import replay as rp                           # noqa: E402


def _make_price_db(path: pathlib.Path, series: dict) -> None:
    """{symbol: [(iso_date, close), ...]} → sqlite shaped like
    stock_data.db."""
    con = sqlite3.connect(path)
    con.execute("""
        CREATE TABLE prices (
          fetched_at TEXT, symbol TEXT, source TEXT, timestamp TEXT,
          interval TEXT, open REAL, high REAL, low REAL, close REAL,
          volume INTEGER, vwap REAL, change_pct REAL, extra TEXT,
          UNIQUE(symbol, source, timestamp)
        )
    """)
    for sym, bars in series.items():
        for d, close in bars:
            con.execute(
                "INSERT INTO prices (symbol, source, timestamp, interval, "
                "close) VALUES (?, 'yfinance', ?, '1d', ?)",
                (sym, f"{d}T00:00:00+00:00", close),
            )
    con.commit(); con.close()


def _days(n, start=date(2024, 1, 1)):
    """n consecutive weekdays as iso strings."""
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


class ReplayTestCase(unittest.TestCase):
    """Tmp price DB: RISE climbs 100,101,102,…; FLAT stays at 50;
    LATE starts 10 bars in; GAP misses one bar mid-series."""

    N = rp.MIN_HISTORY + 20

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db = pathlib.Path(self.tmp.name) / "stock_data.db"
        days = _days(self.N)
        gap = list(enumerate(days))
        del gap[30]                                   # GAP misses day 30
        _make_price_db(db, {
            "RISE": [(d, 100.0 + k) for k, d in enumerate(days)],
            "FLAT": [(d, 50.0) for d in days],
            "LATE": [(d, 10.0 + k) for k, d in enumerate(days[10:])],
            "GAP":  [(d, 200.0 + k) for k, d in gap],
        })
        p = mock.patch("stock_toolkit.score.discover_dbs",
                       return_value=[db])
        p.start()
        self.addCleanup(p.stop)
        self.panel = rp.load_panel(["RISE", "FLAT", "LATE", "GAP"])

    def test_panel_shape_ffill_and_leading_nan(self):
        self.assertEqual(self.panel.shape, (self.N, 4))
        # GAP's missing bar is forward-filled with the previous close…
        self.assertAlmostEqual(self.panel["GAP"].iloc[30],
                               self.panel["GAP"].iloc[29])
        # …but LATE keeps NaN before it starts trading (no back-fill).
        self.assertTrue(self.panel["LATE"].iloc[:10].isna().all())
        self.assertFalse(self.panel["LATE"].iloc[10:].isna().any())

    def test_playable_positions_bounds(self):
        first, last = rp.playable_positions(self.panel)
        self.assertEqual(first, rp.MIN_HISTORY)
        self.assertEqual(last, self.N - 2)
        short = self.panel.iloc[: rp.MIN_HISTORY + 1]
        f2, l2 = rp.playable_positions(short)
        self.assertGreater(f2, l2, "too-short panel must be unplayable")

    def test_window_never_leaks_the_future(self):
        i = rp.MIN_HISTORY
        win = rp.window(self.panel, "RISE", i)
        self.assertEqual(win.index[-1], self.panel.index[i])
        self.assertLessEqual(len(win), rp.LOOKBACK)
        self.assertNotIn(self.panel.index[i + 1], win.index)

    def test_resolve_single_hit_miss_push(self):
        i = rp.MIN_HISTORY
        up = rp.resolve_single(self.panel, "RISE", i, "up", 1000.0)
        self.assertEqual(up["outcome"], "hit")
        # RISE at i is 100+i → next-day move is 1/(100+i) percent
        expected = 1.0 / (100.0 + i) * 100
        self.assertAlmostEqual(up["ret_pct"], expected)
        self.assertAlmostEqual(up["gain"], 1000.0 * expected / 100)
        down = rp.resolve_single(self.panel, "RISE", i, "down", 1000.0)
        self.assertEqual(down["outcome"], "miss")
        self.assertAlmostEqual(down["gain"], -up["gain"])
        push = rp.resolve_single(self.panel, "FLAT", i, "up", 1000.0)
        self.assertEqual(push["outcome"], "push")
        self.assertEqual(push["gain"], 0.0)

    def test_resolve_single_none_at_last_bar(self):
        self.assertIsNone(
            rp.resolve_single(self.panel, "RISE", self.N - 1, "up", 100.0))

    def test_resolve_portfolio_weights_and_cash(self):
        i = rp.MIN_HISTORY
        r_rise = rp.next_return(self.panel, "RISE", i)
        full = rp.resolve_portfolio(self.panel, {"RISE": 100}, i)
        half = rp.resolve_portfolio(self.panel, {"RISE": 50}, i)
        self.assertAlmostEqual(full["ret_pct"], r_rise)
        # un-allocated pot is cash at 0% → exactly half the move
        self.assertAlmostEqual(half["ret_pct"], r_rise / 2)
        mixed = rp.resolve_portfolio(
            self.panel, {"RISE": 50, "FLAT": 50}, i)
        self.assertAlmostEqual(mixed["ret_pct"], r_rise / 2)

    def test_portfolio_before_symbol_exists_scores_zero(self):
        # At position 5 LATE has no bars yet → its slice contributes 0.
        r = rp.resolve_portfolio(self.panel, {"LATE": 100}, 5)
        self.assertAlmostEqual(r["ret_pct"], 0.0)

    def test_equal_weight_return_is_mean_of_tradable(self):
        i = rp.MIN_HISTORY
        rets = [rp.next_return(self.panel, s, i)
                for s in self.panel.columns]
        rets = [r for r in rets if r is not None]
        self.assertAlmostEqual(rp.equal_weight_return(self.panel, i),
                               sum(rets) / len(rets))

    def test_single_summary_counts(self):
        i = rp.MIN_HISTORY
        rounds = [
            rp.resolve_single(self.panel, "RISE", i, "up", 100.0),
            rp.resolve_single(self.panel, "RISE", i + 1, "down", 100.0),
            rp.resolve_single(self.panel, "FLAT", i, "up", 100.0),
        ]
        s = rp.single_summary(rounds)
        self.assertEqual((s["rounds"], s["hits"], s["misses"],
                          s["pushes"]), (3, 1, 1, 1))
        self.assertAlmostEqual(s["hit_rate"], 0.5)   # pushes not counted
        self.assertAlmostEqual(
            s["total_gain"], sum(r["gain"] for r in rounds))

    def test_indicators_on_short_series(self):
        # 1 bar → price only; everything else None, nothing raises.
        win = rp.window(self.panel, "LATE", 10)
        ind = rp.indicators(win)
        self.assertEqual(ind["price"], 10.0)
        for k in ("chg_pct", "rsi14", "pct_b", "sma20", "sma50"):
            self.assertIsNone(ind[k], k)


if __name__ == "__main__":
    unittest.main(verbosity=2)
