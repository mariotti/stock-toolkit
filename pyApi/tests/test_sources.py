"""
test_sources.py
===============
Offline unit tests for the per-API fetchers in stock_toolkit/collector/sources/.

The HTTP layer (safe_get / the yfinance library) is replaced with canned
responses modelled on each API's real shape, so these tests catch
response-parsing regressions without network access or API keys.

Run:
    python3 tests/test_sources.py
"""

import pathlib
import sys
import types
import unittest
from datetime import date, datetime, timezone
from unittest import mock

SCRIPT_DIR = pathlib.Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

import pandas as pd  # noqa: E402

from stock_toolkit.collector import config as cfg  # noqa: E402
from stock_toolkit.collector.sources import (  # noqa: E402
    alphavantage, finnhub, fmp, marketstack, polygon, twelvedata, yfinance,
)


def fresh_state() -> dict:
    return {"date": str(date.today()), "month": str(date.today())[:7],
            "calls": {}, "monthly_calls": {}}


class SourceTestCase(unittest.TestCase):
    """Patches DB/network/rate-limit side effects out of a source module."""

    def neutralise(self, mod, **overrides):
        """Stub the module's collaborator functions; returns the failure log."""
        failures = []
        stubs = {"record_failure":
                 lambda sym, src, reason: failures.append((sym, reason))}
        for name in ("is_suppressed", "_quote_is_fresh", "_live_has_today",
                     "_hourly_bar_is_current", "_hist_has_data"):
            if hasattr(mod, name):
                stubs[name] = lambda *a, **k: False
        if hasattr(mod, "budget_ok"):
            stubs["budget_ok"] = lambda *a, **k: True
        if hasattr(mod, "record_call"):
            stubs["record_call"] = lambda *a, **k: None
        if hasattr(mod, "sleep_for_rate"):
            stubs["sleep_for_rate"] = lambda *a, **k: None
        if hasattr(mod, "time"):
            stubs["time"] = types.SimpleNamespace(
                sleep=lambda s: None,
                time=__import__("time").time,
            )
        stubs.update(overrides)
        for name, val in stubs.items():
            patcher = mock.patch.object(mod, name, val)
            patcher.start()
            self.addCleanup(patcher.stop)
        return failures

    def set_key(self, source, key="test-key"):
        patcher = mock.patch.dict(cfg.API_KEYS, {source: key})
        patcher.start()
        self.addCleanup(patcher.stop)


# ─────────────────────────────────────────────────────────────
#  Finnhub
# ─────────────────────────────────────────────────────────────

class TestFinnhub(SourceTestCase):
    QUOTE = {"c": 150.25, "o": 148.0, "h": 151.0, "l": 147.5,
             "pc": 149.0, "dp": 0.84, "t": 1718000000}

    def setUp(self):
        self.set_key("finnhub")

    def test_quote_parsed_into_row(self):
        self.neutralise(finnhub, safe_get=lambda *a, **k: dict(self.QUOTE))
        rows = finnhub.fetch_finnhub(["AAPL"], fresh_state())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["symbol"], "AAPL")
        self.assertEqual(row["source"], "finnhub")
        self.assertEqual(row["interval"], "1d")
        self.assertEqual(row["close"], 150.25)
        self.assertEqual(row["change_pct"], 0.84)

    def test_403_records_paid_plan_failure(self):
        failures = self.neutralise(finnhub,
                                   safe_get=lambda *a, **k: {"_error": 403})
        rows = finnhub.fetch_finnhub(["ENEL.MI"], fresh_state())
        self.assertEqual(rows, [])
        self.assertIn("paid plan required (403)", failures[0][1])

    def test_empty_response_records_failure(self):
        failures = self.neutralise(finnhub, safe_get=lambda *a, **k: {})
        rows = finnhub.fetch_finnhub(["AAPL"], fresh_state())
        self.assertEqual(rows, [])
        self.assertEqual(failures[0][0], "AAPL")

    def test_paid_tier_adds_candles(self):
        candles = {"s": "ok", "t": [1718000000, 1718086400],
                   "o": [1.0, 2.0], "h": [2.0, 3.0], "l": [0.5, 1.0],
                   "c": [1.5, 2.5], "v": [100, 200]}

        def fake_get(url, params=None, **k):
            return candles if "candle" in url else dict(self.QUOTE)

        self.neutralise(finnhub, safe_get=fake_get)
        with mock.patch.object(cfg, "FINNHUB_PAID", True):
            rows = finnhub.fetch_finnhub(["AAPL"], fresh_state())
        self.assertEqual(len(rows), 3)          # 1 quote + 2 candles
        self.assertEqual(rows[1]["close"], 1.5)
        self.assertEqual(rows[2]["volume"], 200)


# ─────────────────────────────────────────────────────────────
#  Alpha Vantage
# ─────────────────────────────────────────────────────────────

class TestAlphaVantage(SourceTestCase):
    SERIES = {"Time Series (Daily)": {
        "2026-06-10": {"1. open": "100.0", "2. high": "102.0",
                       "3. low": "99.0", "4. close": "101.5", "5. volume": "1000"},
        "2026-06-11": {"1. open": "101.5", "2. high": "103.0",
                       "3. low": "100.0", "4. close": "102.5", "5. volume": "2000"},
    }}

    def setUp(self):
        self.set_key("alphavantage")
        patcher = mock.patch.object(cfg, "ALPHAVANTAGE_PAID", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_daily_series_parsed(self):
        self.neutralise(alphavantage, safe_get=lambda *a, **k: dict(self.SERIES))
        rows = alphavantage.fetch_alphavantage(["AAPL"], fresh_state())
        self.assertEqual(len(rows), 2)
        by_date = {r["timestamp"][:10]: r for r in rows}
        self.assertEqual(by_date["2026-06-11"]["close"], 102.5)
        self.assertEqual(by_date["2026-06-10"]["volume"], 1000)

    def test_rate_limit_note_records_failure(self):
        note = {"Note": "API call frequency is 25 calls per day. Please upgrade."}
        failures = self.neutralise(alphavantage,
                                   safe_get=lambda *a, **k: note)
        rows = alphavantage.fetch_alphavantage(["AAPL"], fresh_state())
        self.assertEqual(rows, [])
        self.assertIn("API call frequency", failures[0][1])

    def test_budget_exhausted_stops_before_calling(self):
        calls = []
        self.neutralise(alphavantage,
                        safe_get=lambda *a, **k: calls.append(1) or {},
                        budget_ok=lambda *a, **k: False)
        rows = alphavantage.fetch_alphavantage(["AAPL", "MSFT"], fresh_state())
        self.assertEqual(rows, [])
        self.assertEqual(calls, [])

    def test_hist_filters_to_requested_range(self):
        self.neutralise(alphavantage, safe_get=lambda *a, **k: dict(self.SERIES))
        rows = alphavantage._hist_alphavantage(
            ["AAPL"], None, date(2026, 6, 11), date(2026, 6, 30), fresh_state())
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["timestamp"].startswith("2026-06-11"))


# ─────────────────────────────────────────────────────────────
#  Massive / Polygon
# ─────────────────────────────────────────────────────────────

class TestPolygon(SourceTestCase):
    BARS = {"status": "OK", "results": [
        {"t": 1718000000000, "o": 1.0, "h": 2.0, "l": 0.5,
         "c": 1.5, "v": 100, "vw": 1.4},
    ]}

    def setUp(self):
        self.set_key("polygon")

    def test_aggs_parsed_with_vwap(self):
        self.neutralise(polygon, safe_get=lambda *a, **k: dict(self.BARS))
        rows = polygon.fetch_polygon(["AAPL"], fresh_state())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["close"], 1.5)
        self.assertEqual(rows[0]["vwap"], 1.4)

    def test_error_status_records_failure(self):
        failures = self.neutralise(polygon,
                                   safe_get=lambda *a, **k: {"status": "ERROR"})
        rows = polygon.fetch_polygon(["AAPL"], fresh_state())
        self.assertEqual(rows, [])
        self.assertEqual(failures[0], ("AAPL", "ERROR"))

    def test_zero_bars_records_failure(self):
        failures = self.neutralise(
            polygon, safe_get=lambda *a, **k: {"status": "OK", "results": []})
        polygon.fetch_polygon(["ENEL.MI"], fresh_state())
        self.assertIn("0 bars", failures[0][1])

    def test_hist_follows_pagination(self):
        page2 = {"status": "OK", "results": [
            {"t": 1718086400000, "o": 2.0, "h": 3.0, "l": 1.0, "c": 2.5, "v": 200}]}
        page1 = dict(self.BARS, next_url="https://api.massive.com/page2")
        responses = iter([page1, page2])
        self.neutralise(polygon, safe_get=lambda *a, **k: next(responses))
        rows = polygon._hist_polygon(["AAPL"], None,
                                     date(2024, 1, 1), date(2024, 12, 31),
                                     fresh_state())
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["close"], 2.5)


# ─────────────────────────────────────────────────────────────
#  FMP
# ─────────────────────────────────────────────────────────────

class TestFMP(SourceTestCase):
    QUOTE = [{"symbol": "AAPL", "open": 148.0, "dayHigh": 151.0,
              "dayLow": 147.5, "price": 150.25, "volume": 1000,
              "changesPercentage": 0.84, "marketCap": 1, "pe": 2,
              "eps": 3, "yearHigh": 4, "yearLow": 5}]
    EOD = [{"date": "2026-06-10", "open": 100.0, "high": 102.0, "low": 99.0,
            "close": 101.5, "volume": 1000, "vwap": 100.8,
            "changePercent": 1.5, "adjClose": 101.5}]

    def setUp(self):
        self.set_key("fmp")

    def _fake_get(self, quote=None, eod=None):
        def fake(url, params=None, **k):
            if url.endswith("/quote"):
                return quote
            return eod
        return fake

    def test_bulk_quote_and_eod_parsed(self):
        self.neutralise(fmp, safe_get=self._fake_get(self.QUOTE, self.EOD))
        rows = fmp.fetch_fmp(["AAPL"], fresh_state())
        self.assertEqual(len(rows), 2)            # 1 quote + 1 EOD bar
        self.assertEqual(rows[0]["close"], 150.25)
        self.assertEqual(rows[1]["timestamp"][:10], "2026-06-10")
        self.assertEqual(rows[1]["vwap"], 100.8)

    def test_legacy_wrapped_format_still_supported(self):
        wrapped = {"historical": self.EOD}
        self.neutralise(fmp, safe_get=self._fake_get([], wrapped))
        rows = fmp.fetch_fmp(["AAPL"], fresh_state())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["close"], 101.5)

    def test_402_records_paid_plan_failure(self):
        failures = self.neutralise(
            fmp, safe_get=self._fake_get([], {"_error": 402}))
        rows = fmp.fetch_fmp(["AAPL"], fresh_state())
        self.assertEqual(rows, [])
        self.assertIn("paid plan required (402)", failures[0][1])

    # ── historical path: _hist_fmp ────────────────────────────────────────

    def _dates(self):
        import datetime
        return datetime.date(2026, 1, 1), datetime.date(2026, 6, 1)

    def test_hist_fmp_parses_list(self):
        self.neutralise(fmp, safe_get=lambda *a, **k: self.EOD)
        d0, d1 = self._dates()
        rows = fmp._hist_fmp(["AAPL"], None, d0, d1, fresh_state())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["close"], 101.5)
        self.assertEqual(rows[0]["source"], "fmp")

    def test_hist_fmp_legacy_wrapped(self):
        self.neutralise(fmp, safe_get=lambda *a, **k: {"historical": self.EOD})
        d0, d1 = self._dates()
        rows = fmp._hist_fmp(["AAPL"], None, d0, d1, fresh_state())
        self.assertEqual(len(rows), 1)

    def test_hist_fmp_402_records_failure(self):
        failures = self.neutralise(fmp, safe_get=lambda *a, **k: {"_error": 402})
        d0, d1 = self._dates()
        rows = fmp._hist_fmp(["AAPL"], None, d0, d1, fresh_state())
        self.assertEqual(rows, [])
        self.assertIn("paid plan required (402)", failures[0][1])

    def test_hist_fmp_message_error(self):
        failures = self.neutralise(fmp,
                                   safe_get=lambda *a, **k: {"message": "bad key"})
        d0, d1 = self._dates()
        rows = fmp._hist_fmp(["AAPL"], None, d0, d1, fresh_state())
        self.assertEqual(rows, [])
        self.assertIn("bad key", failures[0][1])

    def test_hist_fmp_empty_records_failure(self):
        failures = self.neutralise(fmp, safe_get=lambda *a, **k: [])
        d0, d1 = self._dates()
        rows = fmp._hist_fmp(["AAPL"], None, d0, d1, fresh_state())
        self.assertEqual(rows, [])
        self.assertIn("empty response", failures[0][1])


# ─────────────────────────────────────────────────────────────
#  Twelve Data
# ─────────────────────────────────────────────────────────────

class TestTwelveData(SourceTestCase):
    PAYLOAD = {"status": "ok", "values": [
        {"datetime": "2026-06-10", "open": "100.0", "high": "102.0",
         "low": "99.0", "close": "101.5", "volume": "1000"},
    ]}

    def setUp(self):
        self.set_key("twelvedata")

    def test_single_symbol_response_wrapped(self):
        self.neutralise(twelvedata,
                        safe_get=lambda *a, **k: dict(self.PAYLOAD),
                        _hourly_bar_is_current=lambda *a, **k: True)
        rows = twelvedata.fetch_twelvedata(["AAPL"], fresh_state())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "AAPL")
        self.assertEqual(rows[0]["close"], 101.5)
        self.assertEqual(rows[0]["interval"], "1d")   # "1day" stored as "1d"

    def test_multi_symbol_response(self):
        multi = {"AAPL": dict(self.PAYLOAD), "MSFT": dict(self.PAYLOAD)}
        self.neutralise(twelvedata,
                        safe_get=lambda *a, **k: multi,
                        _hourly_bar_is_current=lambda *a, **k: True)
        rows = twelvedata.fetch_twelvedata(["AAPL", "MSFT"], fresh_state())
        self.assertEqual({r["symbol"] for r in rows}, {"AAPL", "MSFT"})

    def test_batch_error_yields_no_rows(self):
        err = {"code": 429, "message": "limit", "status": "error"}
        self.neutralise(twelvedata,
                        safe_get=lambda *a, **k: err,
                        _hourly_bar_is_current=lambda *a, **k: True)
        rows = twelvedata.fetch_twelvedata(["AAPL"], fresh_state())
        self.assertEqual(rows, [])

    def test_per_symbol_error_records_failure(self):
        multi = {"AAPL": dict(self.PAYLOAD),
                 "BAD": {"status": "error", "message": "symbol not found"}}
        failures = self.neutralise(twelvedata,
                                   safe_get=lambda *a, **k: multi,
                                   _hourly_bar_is_current=lambda *a, **k: True)
        rows = twelvedata.fetch_twelvedata(["AAPL", "BAD"], fresh_state())
        self.assertEqual(len(rows), 1)
        self.assertEqual(failures[0][0], "BAD")

    # ── historical path: _hist_twelvedata ─────────────────────────────────

    def _dates(self):
        import datetime
        return datetime.date(2026, 1, 1), datetime.date(2026, 6, 1)

    def test_hist_single_symbol_wrapped(self):
        self.neutralise(twelvedata, safe_get=lambda *a, **k: dict(self.PAYLOAD))
        d0, d1 = self._dates()
        rows = twelvedata._hist_twelvedata(["AAPL"], None, d0, d1, fresh_state())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["close"], 101.5)
        self.assertEqual(rows[0]["interval"], "1d")

    def test_hist_multi_symbol(self):
        multi = {"AAPL": dict(self.PAYLOAD), "MSFT": dict(self.PAYLOAD)}
        self.neutralise(twelvedata, safe_get=lambda *a, **k: multi)
        d0, d1 = self._dates()
        rows = twelvedata._hist_twelvedata(["AAPL", "MSFT"], None, d0, d1,
                                           fresh_state())
        self.assertEqual({r["symbol"] for r in rows}, {"AAPL", "MSFT"})

    def test_hist_batch_error_breaks(self):
        err = {"code": 429, "message": "limit", "status": "error"}
        self.neutralise(twelvedata, safe_get=lambda *a, **k: err)
        d0, d1 = self._dates()
        rows = twelvedata._hist_twelvedata(["AAPL"], None, d0, d1, fresh_state())
        self.assertEqual(rows, [])

    def test_hist_skips_when_already_in_db(self):
        # _hist_has_data True → symbol filtered out → empty, no fetch
        self.neutralise(twelvedata, safe_get=lambda *a, **k: dict(self.PAYLOAD),
                        _hist_has_data=lambda *a, **k: True)
        d0, d1 = self._dates()
        rows = twelvedata._hist_twelvedata(["AAPL"], None, d0, d1, fresh_state())
        self.assertEqual(rows, [])


# ─────────────────────────────────────────────────────────────
#  Marketstack
# ─────────────────────────────────────────────────────────────

class TestMarketstack(SourceTestCase):
    EOD = {"data": [
        {"symbol": "AAPL", "date": "2026-06-10T00:00:00+0000",
         "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.5,
         "volume": 1000, "adj_close": 101.4, "exchange": "XNAS"},
    ]}

    def setUp(self):
        self.set_key("marketstack")

    def test_eod_parsed(self):
        self.neutralise(marketstack, safe_get=lambda *a, **k: dict(self.EOD))
        rows = marketstack.fetch_marketstack(["AAPL"], fresh_state())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["timestamp"][:10], "2026-06-10")
        self.assertEqual(rows[0]["vwap"], 101.4)      # adj_close mapped to vwap

    def test_missing_symbols_record_failure(self):
        failures = self.neutralise(marketstack,
                                   safe_get=lambda *a, **k: dict(self.EOD))
        marketstack.fetch_marketstack(["AAPL", "NOPE"], fresh_state())
        self.assertEqual(failures, [("NOPE", "no data returned")])

    def test_429_exhausts_monthly_budget(self):
        self.neutralise(marketstack, safe_get=lambda *a, **k: {"_error": 429})
        state = fresh_state()
        rows = marketstack.fetch_marketstack(["AAPL"], state)
        self.assertEqual(rows, [])
        self.assertEqual(state["monthly_calls"]["marketstack"],
                         cfg.MONTHLY_LIMITS["marketstack"])

    def test_api_error_yields_no_rows(self):
        err = {"error": {"code": "invalid_access_key", "message": "nope"}}
        self.neutralise(marketstack, safe_get=lambda *a, **k: err)
        self.assertEqual(marketstack.fetch_marketstack(["AAPL"], fresh_state()), [])


# ─────────────────────────────────────────────────────────────
#  yfinance
# ─────────────────────────────────────────────────────────────

class FakeTicker:
    """Mimics yfinance.Ticker: .history() returns OHLCV DataFrames."""

    daily = pd.DataFrame(
        {"Open": [100.0], "High": [102.0], "Low": [99.0],
         "Close": [101.5], "Volume": [1000]},
        index=pd.DatetimeIndex([datetime(2026, 6, 10, tzinfo=timezone.utc)]),
    )
    hourly = pd.DataFrame(
        {"Open": [101.0], "High": [101.8], "Low": [100.9],
         "Close": [101.2], "Volume": [50]},
        index=pd.DatetimeIndex([datetime(2026, 6, 10, 15, tzinfo=timezone.utc)]),
    )

    def __init__(self, sym):
        self.sym = sym

    def history(self, start=None, end=None, period=None, interval="1d"):
        return self.daily if interval == "1d" else self.hourly


class TestYfinance(SourceTestCase):

    def _install_fake_yf(self, ticker_cls=FakeTicker):
        fake = types.ModuleType("yfinance")
        fake.Ticker = ticker_cls
        patcher = mock.patch.dict(sys.modules, {"yfinance": fake})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_daily_and_hourly_bars_parsed(self):
        self._install_fake_yf()
        self.neutralise(yfinance)
        rows = yfinance.fetch_yfinance(["AAPL"])
        self.assertEqual(len(rows), 2)
        daily = [r for r in rows if r["interval"] == "1d"][0]
        hourly = [r for r in rows if r["interval"] == "1h"][0]
        self.assertEqual(daily["close"], 101.5)
        self.assertEqual(hourly["close"], 101.2)

    def test_empty_history_records_failure(self):
        class EmptyTicker(FakeTicker):
            daily  = FakeTicker.daily.iloc[0:0]
            hourly = FakeTicker.hourly.iloc[0:0]

        self._install_fake_yf(EmptyTicker)
        failures = self.neutralise(yfinance)
        rows = yfinance.fetch_yfinance(["DELISTED"])
        self.assertEqual(rows, [])
        self.assertIn("0 bars", failures[0][1])

    def test_exception_records_failure(self):
        class BoomTicker(FakeTicker):
            def history(self, **k):
                raise RuntimeError("rate limited")

        self._install_fake_yf(BoomTicker)
        failures = self.neutralise(yfinance)
        rows = yfinance.fetch_yfinance(["AAPL"])
        self.assertEqual(rows, [])
        self.assertIn("rate limited", failures[0][1])


if __name__ == "__main__":
    runner = unittest.main(verbosity=2, exit=False)
    sys.exit(0 if runner.result.wasSuccessful() else 1)
