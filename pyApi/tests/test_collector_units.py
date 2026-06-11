"""
test_collector_units.py
=======================
Offline unit tests for the collector's plumbing modules:

  - state.py      — call-budget accounting and state persistence
  - http.py       — safe_get error handling and rate pacing
  - historical.py — range parsing and the per-source orchestrator

Run:
    python3 tests/test_collector_units.py
"""

import pathlib
import sys
import tempfile
import unittest
from datetime import date
from unittest import mock

SCRIPT_DIR = pathlib.Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from stock_toolkit.collector import config as cfg                # noqa: E402
from stock_toolkit.collector import historical, http, state      # noqa: E402


def fresh_state() -> dict:
    return {"date": str(date.today()), "month": str(date.today())[:7],
            "calls": {}, "monthly_calls": {}}


# ─────────────────────────────────────────────────────────────
#  state.py
# ─────────────────────────────────────────────────────────────

class TestStatePersistence(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(
            cfg, "STATE_PATH", pathlib.Path(self.tmp.name) / "state.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_fresh_state_when_no_file(self):
        st = state.load_state()
        self.assertEqual(st["date"], str(date.today()))
        self.assertEqual(st["calls"], {})
        self.assertEqual(st["monthly_calls"], {})

    def test_roundtrip(self):
        st = state.load_state()
        st["calls"]["fmp"] = 7
        state.save_state(st)
        self.assertEqual(state.load_state()["calls"]["fmp"], 7)

    def test_daily_counters_reset_on_new_day(self):
        st = fresh_state()
        st["date"]  = "2020-01-01"          # stale date, same month logic aside
        st["calls"] = {"fmp": 99}
        state.save_state(st)
        reloaded = state.load_state()
        self.assertEqual(reloaded["date"], str(date.today()))
        self.assertEqual(reloaded["calls"], {})

    def test_monthly_counters_reset_on_new_month(self):
        st = fresh_state()
        st["month"]         = "2020-01"
        st["monthly_calls"] = {"marketstack": 99}
        state.save_state(st)
        self.assertEqual(state.load_state()["monthly_calls"], {})

    def test_monthly_counters_survive_same_month(self):
        st = fresh_state()
        st["monthly_calls"] = {"marketstack": 5}
        state.save_state(st)
        self.assertEqual(state.load_state()["monthly_calls"]["marketstack"], 5)


class TestBudget(unittest.TestCase):

    def test_within_daily_budget(self):
        st = fresh_state()
        st["calls"]["alphavantage"] = cfg.DAILY_LIMITS["alphavantage"] - 1
        self.assertTrue(state.budget_ok(st, "alphavantage"))

    def test_daily_budget_exhausted(self):
        st = fresh_state()
        st["calls"]["alphavantage"] = cfg.DAILY_LIMITS["alphavantage"]
        self.assertFalse(state.budget_ok(st, "alphavantage"))

    def test_monthly_budget_exhausted(self):
        st = fresh_state()
        st["monthly_calls"]["marketstack"] = cfg.MONTHLY_LIMITS["marketstack"]
        self.assertFalse(state.budget_ok(st, "marketstack"))

    def test_unlimited_source_always_ok(self):
        self.assertTrue(state.budget_ok(fresh_state(), "yfinance"))

    def test_record_call_increments_daily(self):
        st = fresh_state()
        state.record_call(st, "fmp")
        state.record_call(st, "fmp", 3)
        self.assertEqual(st["calls"]["fmp"], 4)
        self.assertNotIn("fmp", st["monthly_calls"])   # fmp has no monthly cap

    def test_record_call_tracks_monthly_for_capped_sources(self):
        st = fresh_state()
        state.record_call(st, "marketstack")
        self.assertEqual(st["monthly_calls"]["marketstack"], 1)


# ─────────────────────────────────────────────────────────────
#  http.py
# ─────────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise http.requests.exceptions.HTTPError(f"{self.status_code}")

    def close(self):
        pass


class TestSafeGet(unittest.TestCase):

    def _get_with(self, response_or_exc):
        def fake_get(*a, **k):
            if isinstance(response_or_exc, Exception):
                raise response_or_exc
            return response_or_exc
        with mock.patch.object(http.requests, "get", fake_get):
            return http.safe_get("https://example.test/endpoint")

    def test_ok_returns_json(self):
        out = self._get_with(FakeResponse(200, {"a": 1}))
        self.assertEqual(out, {"a": 1})

    def test_402_maps_to_error_dict(self):
        self.assertEqual(self._get_with(FakeResponse(402))["_error"], 402)

    def test_403_maps_to_error_dict(self):
        self.assertEqual(self._get_with(FakeResponse(403)), {"_error": 403})

    def test_429_maps_to_error_dict(self):
        self.assertEqual(self._get_with(FakeResponse(429))["_error"], 429)

    def test_500_returns_none(self):
        self.assertIsNone(self._get_with(FakeResponse(500)))

    def test_network_exception_returns_none(self):
        self.assertIsNone(self._get_with(ConnectionError("unreachable")))

    def test_sleep_for_rate_uses_minute_limit(self):
        slept = []
        with mock.patch.object(http.time, "sleep", slept.append):
            http.sleep_for_rate("polygon")     # 5/min → 12.1 s
            http.sleep_for_rate("yfinance")    # no minute limit → no sleep
        self.assertEqual(len(slept), 1)
        self.assertAlmostEqual(slept[0], 60 / cfg.MINUTE_LIMITS["polygon"] + 0.1)


# ─────────────────────────────────────────────────────────────
#  historical.py
# ─────────────────────────────────────────────────────────────

class TestParseHistoricalArg(unittest.TestCase):

    def test_single_year(self):
        self.assertEqual(historical.parse_historical_arg("2024"),
                         (date(2024, 1, 1), date(2024, 12, 31), "2024"))

    def test_year_range(self):
        self.assertEqual(historical.parse_historical_arg("2000-2015"),
                         (date(2000, 1, 1), date(2015, 12, 31), "2000-2015"))

    def test_reversed_range_is_normalised(self):
        self.assertEqual(historical.parse_historical_arg("2015-2000"),
                         (date(2000, 1, 1), date(2015, 12, 31), "2000-2015"))

    def test_all_keyword(self):
        date_from, date_to, suffix = historical.parse_historical_arg("all")
        self.assertEqual(date_from, date(1970, 1, 1))
        self.assertEqual(date_to, date.today())
        self.assertEqual(suffix, "all")

    def test_invalid_values_raise(self):
        for bad in ("20x4", "199", "2000-15", "2020-2021-2022", ""):
            with self.assertRaises(ValueError, msg=bad):
                historical.parse_historical_arg(bad)


class TestRunHistorical(unittest.TestCase):
    """run_historical wires range → per-source fetchers → dedicated DB."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(
            cfg, "HIST_DIR", pathlib.Path(self.tmp.name) / "data")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_orchestrator_collects_and_persists(self):
        calls = []

        def fake_fetcher(name):
            def fetch(symbols, db_path, date_from, date_to, st):
                calls.append((name, tuple(symbols), date_from, date_to))
                return []
            return fetch

        inserted = []
        with mock.patch.multiple(
                historical,
                _hist_yfinance=fake_fetcher("yfinance"),
                _hist_alphavantage=fake_fetcher("alphavantage"),
                _hist_finnhub=fake_fetcher("finnhub"),
                _hist_polygon=fake_fetcher("polygon"),
                _hist_fmp=fake_fetcher("fmp"),
                _hist_twelvedata=fake_fetcher("twelvedata"),
                db_insert_rows=lambda rows, db_path=None:
                    inserted.append((len(rows), db_path)) or 0):
            db_path = historical.run_historical(["AAPL"], "2024", fresh_state())

        self.assertEqual(db_path.name, "stock_data_2024.db")
        self.assertTrue(db_path.exists(), "schema DB should be created")
        self.assertEqual(len(calls), 6, "all six historical fetchers run")
        self.assertEqual(calls[0][2:], (date(2024, 1, 1), date(2024, 12, 31)))
        self.assertEqual(inserted[0][1], db_path)

    def test_invalid_range_raises_before_any_io(self):
        with self.assertRaises(ValueError):
            historical.run_historical(["AAPL"], "not-a-year", fresh_state())
        self.assertFalse((pathlib.Path(self.tmp.name) / "data").exists())


if __name__ == "__main__":
    runner = unittest.main(verbosity=2, exit=False)
    sys.exit(0 if runner.result.wasSuccessful() else 1)
