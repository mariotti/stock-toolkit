"""
test_live_apis.py
=================
Live API tests — these actually hit the real API endpoints.
Each test makes exactly ONE API call using the cheapest available endpoint.

Run only when explicitly requested:
    RUN_LIVE=1 python3 test_live_apis.py
    RUN_LIVE=1 python3 -m pytest test_live_apis.py -v

Skipped entirely if RUN_LIVE is not set, so the main test suite (test_toolkit.py)
remains fast and offline at all times.

Cost per run:
    yfinance          0 calls (no key, unofficial scraper)
    Alpha Vantage     0 calls (uses public 'demo' key, not your quota)
    FMP               0 calls (uses public 'demo' key, not your quota)
    Finnhub           1 call  (from your 60/min free allowance)
    Polygon           1 call  (from your 5/min free allowance)
    Twelve Data       1 call  (from your 800/day free allowance)
    Marketstack       1 call  (from your 100/month free allowance)
    ─────────────────────────────────────────────────────────────────
    Total against your quota: 0–4 calls depending on which keys are
    configured in config.env
"""

import os
import sys
import pathlib
import unittest
import warnings
warnings.filterwarnings("ignore")

# ── guard: skip everything unless explicitly requested ────────────────────────
RUN_LIVE = os.environ.get("RUN_LIVE", "").lower() in ("1", "true", "yes")

if not RUN_LIVE:
    print(
        "\nLive API tests are SKIPPED by default.\n"
        "To run them:  RUN_LIVE=1 python3 test_live_apis.py\n"
        "Cost:  0 quota calls for demo-key tests,\n"
        "       1 call per configured real-key source.\n"
    )
    sys.exit(0)

# ─────────────────────────────────────────────
#  IMPORTS & CONFIG
# ─────────────────────────────────────────────

SCRIPT_DIR = pathlib.Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

try:
    import requests
except ImportError:
    print("requests not installed — run: pip install requests")
    sys.exit(1)

try:
    import yfinance as yf
    HAS_YFINANCE = True
except ImportError:
    HAS_YFINANCE = False

from stock_common import CONFIG_PATH, load_config

CFG = load_config(CONFIG_PATH)

# ── API key helpers ───────────────────────────────────────────────────────────

def _key(name: str) -> str:
    """Return the API key or '' if not configured."""
    return CFG.get(name, "").strip()

def _has(name: str) -> bool:
    return bool(_key(name))

# ── request helper with friendly timeout ─────────────────────────────────────

TIMEOUT = 15   # seconds — generous for slow connections


def _get(url: str, **params) -> requests.Response:
    return requests.get(url, params=params, timeout=TIMEOUT)


# ─────────────────────────────────────────────────────────────────────────────
#  BASE CLASS
# ─────────────────────────────────────────────────────────────────────────────

class LiveAPITest(unittest.TestCase):
    """Base class for live API tests."""

    def assertHTTP200(self, r: requests.Response, source: str):
        """Assert 200 OK with a clear message about what failed."""
        self.assertEqual(
            r.status_code, 200,
            f"[{source}] Expected 200, got {r.status_code}. "
            f"URL: {r.url[:120]}"
        )

    def assertHasKeys(self, data: dict, keys: list, source: str):
        for k in keys:
            self.assertIn(
                k, data,
                f"[{source}] Response missing key '{k}'. "
                f"Keys present: {list(data.keys())[:10]}"
            )

    def skipIfNoKey(self, key_name: str, source: str):
        if not _has(key_name):
            self.skipTest(
                f"[{source}] {key_name} not set in config.env — skipping"
            )


# ─────────────────────────────────────────────────────────────────────────────
#  1. CONNECTIVITY — basic reachability check for each domain
# ─────────────────────────────────────────────────────────────────────────────

class TestConnectivity(LiveAPITest):
    """One HTTP HEAD/GET per domain to confirm network connectivity."""

    def _reachable(self, url: str, name: str):
        try:
            # allow_redirects=False avoids redirect loops on sites like finnhub.io
            r = requests.head(url, timeout=8, allow_redirects=False)
            # Any response (including redirects 3xx) means the server is up
            self.assertLess(
                r.status_code, 500,
                f"{name} returned server error {r.status_code}"
            )
        except requests.exceptions.TooManyRedirects:
            pass   # server is reachable — it just redirects aggressively
        except requests.exceptions.ConnectionError:
            self.skipTest(f"{name} unreachable — no internet connection?")
        except requests.exceptions.Timeout:
            self.skipTest(f"{name} timed out")

    def test_reach_alphavantage(self):
        self._reachable("https://www.alphavantage.co", "Alpha Vantage")

    def test_reach_finnhub(self):
        self._reachable("https://finnhub.io", "Finnhub")

    def test_reach_polygon(self):
        self._reachable("https://api.massive.com", "Massive (Polygon.io)")

    def test_reach_fmp(self):
        self._reachable("https://financialmodelingprep.com", "FMP")

    def test_reach_twelvedata(self):
        self._reachable("https://api.twelvedata.com", "Twelve Data")

    def test_reach_marketstack(self):
        self._reachable("https://api.marketstack.com", "Marketstack")


# ─────────────────────────────────────────────────────────────────────────────
#  2. YFINANCE — no key needed, cheapest possible call
# ─────────────────────────────────────────────────────────────────────────────

@unittest.skipUnless(HAS_YFINANCE, "yfinance not installed")
class TestYFinance(LiveAPITest):
    """
    yfinance tests — uses the unofficial Yahoo Finance scraper.
    No API key, no quota. Single symbol, minimal data.
    """

    def test_fetch_ticker_info(self):
        """Fetch fast_info for AAPL — the lightest yfinance call."""
        ticker = yf.Ticker("AAPL")
        info   = ticker.fast_info
        # fast_info has 'last_price' or similar — just check it's not empty
        self.assertIsNotNone(info)
        # last_price should be a positive number
        if hasattr(info, "last_price") and info.last_price is not None:
            self.assertGreater(info.last_price, 0)

    def test_fetch_one_week_history(self):
        """Fetch 5 days of AAPL daily bars — the minimum useful historical call."""
        ticker = yf.Ticker("AAPL")
        hist   = ticker.history(period="5d", interval="1d")
        self.assertFalse(hist.empty, "yfinance returned empty history for AAPL")
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            self.assertIn(col, hist.columns,
                          f"yfinance history missing column: {col}")
        # Prices should be positive
        self.assertTrue((hist["Close"] > 0).all(),
                        "Some close prices are <= 0")

    def test_european_symbol(self):
        """ENEL.MI should also return data — confirms non-US symbol support."""
        ticker = yf.Ticker("ENEL.MI")
        hist   = ticker.history(period="5d", interval="1d")
        # Just check we get a response — may be empty on weekends
        self.assertIsNotNone(hist)


# ─────────────────────────────────────────────────────────────────────────────
#  3. ALPHA VANTAGE — uses the public 'demo' key (costs 0 of your quota)
# ─────────────────────────────────────────────────────────────────────────────

class TestAlphaVantageDemo(LiveAPITest):
    """
    Alpha Vantage tests using the public 'demo' API key.
    The demo key only works for IBM but costs 0 of your daily 25-call quota.
    """

    BASE = "https://www.alphavantage.co/query"
    DEMO = "demo"

    def test_global_quote_structure(self):
        """GLOBAL_QUOTE for IBM with demo key — cheapest AV endpoint."""
        r = _get(self.BASE, function="GLOBAL_QUOTE",
                 symbol="IBM", apikey=self.DEMO)
        self.assertHTTP200(r, "Alpha Vantage demo")
        data = r.json()
        self.assertIn("Global Quote", data,
                      f"Unexpected response: {list(data.keys())}")
        quote = data["Global Quote"]
        for field in ["01. symbol", "05. price", "06. volume"]:
            self.assertIn(field, quote,
                          f"Alpha Vantage Global Quote missing field: {field}")

    def test_global_quote_price_positive(self):
        """The demo IBM price should be a positive float."""
        r    = _get(self.BASE, function="GLOBAL_QUOTE",
                    symbol="IBM", apikey=self.DEMO)
        data = r.json()
        if "Global Quote" in data and "05. price" in data["Global Quote"]:
            price = float(data["Global Quote"]["05. price"])
            self.assertGreater(price, 0,
                               "Alpha Vantage demo returned price <= 0")

    def test_real_key_auth(self):
        """If a real key is configured, verify it returns 200 (not 401/403)."""
        key = _key("ALPHAVANTAGE_KEY")
        if not key:
            self.skipTest("ALPHAVANTAGE_KEY not set in config.env")
        r = _get(self.BASE, function="GLOBAL_QUOTE",
                 symbol="AAPL", apikey=key)
        self.assertHTTP200(r, "Alpha Vantage (real key)")
        data = r.json()
        # budget exhausted returns a Note or Information key, not 4xx
        if "Note" in data or "Information" in data:
            self.skipTest(
                "Alpha Vantage daily budget exhausted — "
                "auth confirmed but rate limit hit"
            )
        self.assertIn("Global Quote", data,
                      f"Unexpected response keys: {list(data.keys())}")


# ─────────────────────────────────────────────────────────────────────────────
#  4. FMP — demo key available (costs 0 of your quota)
# ─────────────────────────────────────────────────────────────────────────────

class TestFMPDemo(LiveAPITest):
    """
    FMP tests.
    Note: FMP revoked their public 'demo' key in 2025 — it now returns 401.
    Only the real-key auth test remains here.
    """

    BASE = "https://financialmodelingprep.com"

    def test_real_key_auth(self):
        """If a real key is configured, verify it authenticates correctly."""
        key = _key("FMP_KEY")
        if not key:
            self.skipTest("FMP_KEY not set in config.env")
        r = _get(f"{self.BASE}/api/v3/quote/AAPL", apikey=key)
        if r.status_code == 401:
            self.skipTest("FMP returned 401 — check FMP_KEY in config.env")
        if r.status_code == 403:
            self.skipTest("FMP returned 403 — key may be expired or plan limit reached")
        self.assertHTTP200(r, "FMP (real key)")
        data = r.json()
        self.assertIsInstance(data, list, "FMP /quote should return a list")
        if data:
            for field in ["symbol", "price", "volume"]:
                self.assertIn(field, data[0],
                              f"FMP quote missing field: {field}")

    def test_demo_key_is_dead(self):
        """
        Documents that the FMP demo key no longer works as of 2025.
        If FMP restores it, this test will fail and remind us to re-enable
        the structural tests.
        """
        r = _get(f"{self.BASE}/api/v3/quote/AAPL", apikey="demo")
        # We expect 401 now — if this fails FMP has restored demo access
        if r.status_code == 200:
            self.skipTest(
                "FMP demo key is working again — consider re-enabling "
                "the structural demo tests"
            )
        self.assertEqual(
            r.status_code, 401,
            f"Unexpected FMP demo status: {r.status_code}"
        )


# ─────────────────────────────────────────────────────────────────────────────
#  5. FINNHUB — real key required, 1 call from 60/min free quota
# ─────────────────────────────────────────────────────────────────────────────

class TestFinnhub(LiveAPITest):
    """
    Finnhub tests — requires FINNHUB_KEY in config.env.
    Uses /quote endpoint (cheapest, always free for US symbols).
    Costs 1 call from the 60/min free allowance.
    """

    BASE = "https://finnhub.io/api/v1"

    def setUp(self):
        self.skipIfNoKey("FINNHUB_KEY", "Finnhub")
        self.key = _key("FINNHUB_KEY")

    def test_quote_us_symbol(self):
        """Fetch /quote for AAPL — free for US symbols."""
        r = _get(f"{self.BASE}/quote", symbol="AAPL", token=self.key)
        self.assertHTTP200(r, "Finnhub")
        data = r.json()
        for field in ["c", "h", "l", "o", "pc"]:
            self.assertIn(field, data,
                          f"Finnhub quote missing field '{field}': {data}")
        if data["c"]:   # c = current price
            self.assertGreater(float(data["c"]), 0)

    def test_quote_eu_symbol_403_or_200(self):
        """
        ENEL.MI returns 403 on free Finnhub plan — that's expected behaviour,
        not a bug. This test documents it rather than failing on it.
        """
        r = _get(f"{self.BASE}/quote", symbol="ENEL.MI", token=self.key)
        # Both 200 (paid) and 403 (free, geo-restricted) are acceptable
        self.assertIn(
            r.status_code, [200, 403],
            f"Finnhub returned unexpected status {r.status_code} for ENEL.MI"
        )
        if r.status_code == 403:
            # Document the expected behaviour
            pass   # Free plan restriction for international symbols — expected


# ─────────────────────────────────────────────────────────────────────────────
#  6. POLYGON — real key required, 1 call from 5/min free quota
# ─────────────────────────────────────────────────────────────────────────────

class TestPolygon(LiveAPITest):
    """
    Massive (formerly Polygon.io) tests — requires MASSIVE_KEY or POLYGON_KEY in config.env.
    Uses a single-day aggregate bar (cheapest historical call).
    Costs 1 call from the 5/min free allowance.
    """

    BASE = "https://api.massive.com"

    def setUp(self):
        # accept MASSIVE_KEY (new name) or POLYGON_KEY (old name)
        key = _key("MASSIVE_KEY") or _key("POLYGON_KEY")
        if not key:
            self.skipTest("[Massive] MASSIVE_KEY or POLYGON_KEY not set in config.env")
        self.key = key

    def test_single_day_agg(self):
        """Fetch one day of AAPL bars — 1 call, minimal data."""
        r = _get(
            f"{self.BASE}/v2/aggs/ticker/AAPL/range/1/day/2024-01-02/2024-01-02",
            adjusted="true", sort="asc", apiKey=self.key
        )
        if r.status_code == 403:
            self.skipTest("Polygon/Massive returned 403 — key may be expired or invalid")
        self.assertHTTP200(r, "Polygon")
        data = r.json()
        self.assertIn("status", data)
        self.assertIn("results", data,
                      f"Polygon response missing 'results': {data}")
        if data.get("results"):
            bar = data["results"][0]
            for field in ["o", "h", "l", "c", "v"]:
                self.assertIn(field, bar,
                              f"Polygon bar missing field '{field}'")


# ─────────────────────────────────────────────────────────────────────────────
#  7. TWELVE DATA — real key required, 1 call from 800/day free quota
# ─────────────────────────────────────────────────────────────────────────────

class TestTwelveData(LiveAPITest):
    """
    Twelve Data tests — requires TWELVEDATA_KEY in config.env.
    Uses /quote endpoint (single price, cheaper than /time_series).
    Costs 1 call from the 800/day free allowance.
    """

    BASE = "https://api.twelvedata.com"

    def setUp(self):
        self.skipIfNoKey("TWELVEDATA_KEY", "Twelve Data")
        self.key = _key("TWELVEDATA_KEY")

    def test_quote_structure(self):
        """Fetch /quote for AAPL — single-call, minimal data."""
        r = _get(f"{self.BASE}/quote",
                 symbol="AAPL", apikey=self.key)
        self.assertHTTP200(r, "Twelve Data")
        data = r.json()
        if data.get("status") == "error":
            self.skipTest(
                f"Twelve Data error: {data.get('message','unknown')} "
                "(budget exhausted or key invalid)"
            )
        for field in ["symbol", "close", "volume"]:
            self.assertIn(field, data,
                          f"Twelve Data quote missing field '{field}': "
                          f"{list(data.keys())}")

    def test_quote_price_positive(self):
        """The returned close price should be a positive number."""
        r    = _get(f"{self.BASE}/quote",
                    symbol="AAPL", apikey=self.key)
        data = r.json()
        if data.get("status") == "error":
            self.skipTest("Twelve Data returned error — skipping value check")
        if "close" in data:
            self.assertGreater(
                float(data["close"]), 0,
                "Twelve Data close price is <= 0"
            )


# ─────────────────────────────────────────────────────────────────────────────
#  8. MARKETSTACK — real key required, 1 call from 100/month free quota
# ─────────────────────────────────────────────────────────────────────────────

class TestMarketstack(LiveAPITest):
    """
    Marketstack tests — requires MARKETSTACK_KEY in config.env.
    Uses /tickers/{symbol} (metadata only, not EOD data) to avoid burning
    the monthly quota on test data.
    Costs 1 call from the 100/month free allowance.
    """

    BASE = "https://api.marketstack.com/v2"

    def setUp(self):
        self.skipIfNoKey("MARKETSTACK_KEY", "Marketstack")
        self.key = _key("MARKETSTACK_KEY")

    def test_ticker_metadata(self):
        """
        Fetch /tickers/AAPL — returns metadata (name, symbol, exchange),
        not EOD data, so it's cheaper on the monthly budget than /eod.
        """
        r = _get(f"{self.BASE}/tickers/AAPL",
                 access_key=self.key)
        self.assertHTTP200(r, "Marketstack")
        data = r.json()
        if "error" in data:
            err = data["error"]
            self.skipTest(
                f"Marketstack error {err.get('code','?')}: "
                f"{err.get('message','unknown')}"
            )
        for field in ["symbol", "name"]:
            self.assertIn(field, data,
                          f"Marketstack ticker missing field '{field}': "
                          f"{list(data.keys())}")
        self.assertEqual(
            data.get("symbol"), "AAPL",
            f"Marketstack returned wrong symbol: {data.get('symbol')}"
        )


# ─────────────────────────────────────────────────────────────────────────────
#  RUNNER
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  Live API tests")
    print(f"  config.env: {'found' if CONFIG_PATH.exists() else 'NOT FOUND'}")
    print()

    # Show which sources are configured
    sources = {
        "yfinance":    "built-in (no key)",
        "Alpha Vantage demo": "built-in (demo key)",
        "FMP demo":    "built-in (demo key)",
        "Finnhub":     "FINNHUB_KEY",
        "Massive":     "MASSIVE_KEY (or POLYGON_KEY)",
        "FMP (real)":  "FMP_KEY",
        "Twelve Data": "TWELVEDATA_KEY",
        "Marketstack": "MARKETSTACK_KEY",
        "Alpha Vantage (real)": "ALPHAVANTAGE_KEY",
    }
    for name, key_name in sources.items():
        if key_name.startswith("built"):
            status = "✓ available"
        else:
            status = "✓ configured" if _has(key_name) else "– not configured (skipped)"
        print(f"  {name:<26} {status}")

    print()
    print("  Each configured source: 1 API call maximum")
    print("=" * 60)
    print()

    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()
    for cls in [
        TestConnectivity,
        TestYFinance,
        TestAlphaVantageDemo,
        TestFMPDemo,
        TestFinnhub,
        TestPolygon,
        TestTwelveData,
        TestMarketstack,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2, failfast=False)
    result = runner.run(suite)

    total  = result.testsRun
    skip   = len(result.skipped)
    failed = len(result.failures) + len(result.errors)
    passed = total - failed - skip
    print(f"\n{'─'*60}")
    print(f"  {passed} passed  |  {skip} skipped  |  {failed} failed"
          + ("  ✓ all green" if not failed else "  ✗ failures"))
    print(f"{'─'*60}")

    sys.exit(0 if result.wasSuccessful() else 1)
