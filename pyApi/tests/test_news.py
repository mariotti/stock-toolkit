"""
test_news
=========
Offline tests for stock_toolkit.news — the news-sentiment integration.

Covers the deterministic aggregation pipeline (relevance-weighted
average, label bucketing, formatter) without any network. A separate
live test in test_live_apis.py exercises the real Alpha Vantage call
under the RUN_LIVE=1 gate.
"""

import json
import pathlib
import sys
import unittest
from unittest import mock

SCRIPT_DIR = pathlib.Path(__file__).parent
FIXTURE    = SCRIPT_DIR / "fixtures" / "news_sentiment_aapl.json"
sys.path.insert(0, str(SCRIPT_DIR.parent))

from stock_toolkit import news                             # noqa: E402


class TestLabelBucketing(unittest.TestCase):
    """Alpha Vantage's documented thresholds, reproduced locally so a
    score → label lookup doesn't need a re-fetch."""

    def test_each_band(self):
        cases = [
            (-0.40, "Bearish"),
            (-0.20, "Somewhat-Bearish"),
            ( 0.00, "Neutral"),
            ( 0.20, "Somewhat-Bullish"),
            ( 0.50, "Bullish"),
        ]
        for score, expected in cases:
            with self.subTest(score=score):
                self.assertEqual(news.label_for_score(score), expected)


class TestAggregate(unittest.TestCase):
    """Relevance-weighted average against the fixture. Articles with
    relevance=0 must be ignored (they're noise — symbol mentioned
    tangentially)."""

    def setUp(self):
        self.feed = json.loads(FIXTURE.read_text())["feed"]

    def test_returns_expected_shape(self):
        agg = news.aggregate("AAPL", self.feed)
        for k in ("symbol", "articles", "n_articles", "score", "label"):
            self.assertIn(k, agg)

    def test_relevance_zero_articles_excluded(self):
        agg = news.aggregate("AAPL", self.feed)
        # Fixture has 3 articles, the third has relevance=0 — must drop.
        self.assertEqual(agg["n_articles"], 2)

    def test_score_is_relevance_weighted(self):
        # (0.41*0.85 + 0.25*0.70) / (0.85 + 0.70) = 0.6235 / 1.55 ≈ 0.3377
        agg = news.aggregate("AAPL", self.feed)
        self.assertAlmostEqual(agg["score"], 0.3377, places=3)

    def test_label_follows_score(self):
        agg = news.aggregate("AAPL", self.feed)
        # 0.3377 → Somewhat-Bullish (below 0.35 threshold)
        self.assertEqual(agg["label"], "Somewhat-Bullish")

    def test_headlines_sorted_by_relevance(self):
        agg = news.aggregate("AAPL", self.feed)
        rels = [h["relevance"] for h in agg["articles"]]
        self.assertEqual(rels, sorted(rels, reverse=True))

    def test_empty_feed_returns_none_score(self):
        agg = news.aggregate("AAPL", [])
        self.assertEqual(agg["n_articles"], 0)
        self.assertIsNone(agg["score"])
        self.assertEqual(agg["label"], "—")


class TestFetchNewsSentiment(unittest.TestCase):
    """Network-free coverage of the failure paths.
    The happy path is exercised by test_live_apis.py."""

    def setUp(self):
        self.feed = json.loads(FIXTURE.read_text())

    def test_missing_key_returns_empty(self):
        self.assertEqual(news.fetch_news_sentiment(["AAPL"], ""), {})
        self.assertEqual(news.fetch_news_sentiment(["AAPL"], "   "), {})

    def test_empty_symbol_list_returns_empty(self):
        self.assertEqual(news.fetch_news_sentiment([], "fake"), {})

    def test_http_non_200_yields_empty_aggregate(self):
        fake_response = mock.Mock(status_code=429)
        with mock.patch("requests.get", return_value=fake_response):
            out = news.fetch_news_sentiment(["AAPL"], "fake")
        self.assertIn("AAPL", out)
        self.assertEqual(out["AAPL"]["n_articles"], 0)
        self.assertIsNone(out["AAPL"]["score"])

    def test_throttle_response_yields_empty_aggregate(self):
        # AV throttle replies are HTTP 200 with a "Note" key instead of "feed".
        fake = mock.Mock(status_code=200)
        fake.json.return_value = {"Note": "Thank you for using Alpha Vantage..."}
        with mock.patch("requests.get", return_value=fake):
            out = news.fetch_news_sentiment(["AAPL"], "fake")
        self.assertEqual(out["AAPL"]["n_articles"], 0)

    def test_happy_path_via_mocked_response(self):
        fake = mock.Mock(status_code=200)
        fake.json.return_value = self.feed
        with mock.patch("requests.get", return_value=fake):
            out = news.fetch_news_sentiment(["AAPL"], "fake")
        self.assertEqual(out["AAPL"]["n_articles"], 2)
        self.assertEqual(out["AAPL"]["label"], "Somewhat-Bullish")


class TestFormatForPrompt(unittest.TestCase):

    def test_empty_input_empty_output(self):
        self.assertEqual(news.format_for_prompt({}), "")

    def test_renders_header_and_row(self):
        sentiment = {
            "AAPL": news.aggregate(
                "AAPL", json.loads(FIXTURE.read_text())["feed"]
            ),
        }
        out = news.format_for_prompt(sentiment)
        self.assertIn("Symbol", out)
        self.assertIn("AAPL", out)
        self.assertIn("Somewhat-Bullish", out)
        # Header + separator + 1 row + 2 headlines (max_headlines default).
        self.assertGreaterEqual(out.count("\n"), 4)

    def test_long_titles_truncated(self):
        sentiment = {
            "AAPL": {
                "symbol":     "AAPL",
                "n_articles": 1,
                "score":      0.5,
                "label":      "Bullish",
                "articles":   [{"title": "x" * 200, "score": 0.5,
                                "relevance": 1.0}],
            },
        }
        out = news.format_for_prompt(sentiment)
        self.assertIn("…", out)

    def test_zero_articles_renders_coverage_disclaimer(self):
        sentiment = {
            "BMW.DE": {
                "symbol":     "BMW.DE",
                "n_articles": 0,
                "score":      None,
                "label":      "—",
                "articles":   [],
            },
        }
        out = news.format_for_prompt(sentiment)
        self.assertIn("no articles", out)
        self.assertIn("US-biased", out)


if __name__ == "__main__":
    unittest.main()
