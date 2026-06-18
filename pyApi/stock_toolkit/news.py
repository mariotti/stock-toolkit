"""
stock_toolkit.news
==================
Per-symbol news-sentiment snapshot via Alpha Vantage's ``NEWS_SENTIMENT``
endpoint. The sentiment score is **pre-computed by Alpha Vantage** — this
module fetches it, aggregates per-ticker, and formats a compact text
block. The Briefing tab feeds the block into the Claude prompt; the LLM
never computes a sentiment number itself.

Free-tier notes:
- Alpha Vantage's NEWS_SENTIMENT shares the 25-calls/day daily quota
  with every other AV endpoint.
- Coverage is **US-heavy**. European / Swiss tickers (ENEL.MI, BMW.DE,
  DOCM.SW, …) often return zero articles; the empty result is surfaced
  honestly so Claude can read "no news available" rather than making
  one up.
- Missing key, throttled response, or HTTP error → empty dict, no
  exception. Matches ``fundamentals.fetch_fundamentals`` shape.
"""

from __future__ import annotations

# Public API — stable from 2.x.
__all__ = [
    "fetch_news_sentiment",
    "aggregate",
    "format_for_prompt",
    "label_for_score",
]


# AlphaVantage labels: -1 → very bearish, 0 → neutral, +1 → very bullish.
# Documented thresholds are -0.35 / -0.15 / +0.15 / +0.35; reproduced
# here so we apply the same bucketing without re-querying.
_LABEL_THRESHOLDS = (
    (-0.35, "Bearish"),
    (-0.15, "Somewhat-Bearish"),
    ( 0.15, "Neutral"),
    ( 0.35, "Somewhat-Bullish"),
    ( 1.01, "Bullish"),
)


def label_for_score(score: float) -> str:
    """Bucket a -1..+1 sentiment score into Alpha Vantage's five labels."""
    for ceiling, name in _LABEL_THRESHOLDS:
        if score < ceiling:
            return name
    return "Bullish"


def fetch_news_sentiment(
    symbols,
    api_key: str,
    *,
    limit: int = 50,
    timeout: int = 15,
) -> dict:
    """Fetch up to ``limit`` recent articles per symbol from Alpha Vantage.

    Returns ``{symbol: {"articles": [...], "score": float | None,
    "label": str, "n_articles": int}}``. Symbols with no articles or no
    coverage get an entry with ``n_articles=0`` and ``score=None`` —
    not omitted, so the briefing block can honestly say "no news".

    Failures (missing key, network, non-200 response, throttle) return
    ``{}``. Never raises.
    """
    if not (api_key or "").strip() or not symbols:
        return {}

    try:
        import requests
    except ImportError:
        return {}

    out: dict = {}
    for sym in symbols:
        try:
            r = requests.get(
                "https://www.alphavantage.co/query",
                params={
                    "function": "NEWS_SENTIMENT",
                    "tickers":  sym,
                    "limit":    limit,
                    "apikey":   api_key,
                },
                timeout=timeout,
            )
        except Exception:
            out[sym] = aggregate(sym, [])
            continue
        if r.status_code != 200:
            out[sym] = aggregate(sym, [])
            continue
        try:
            data = r.json()
        except Exception:
            out[sym] = aggregate(sym, [])
            continue
        # AV throttle responses come back HTTP 200 with a "Note" or
        # "Information" key instead of a "feed" — treat as empty.
        if not isinstance(data, dict) or "feed" not in data:
            out[sym] = aggregate(sym, [])
            continue
        out[sym] = aggregate(sym, data.get("feed") or [])
    return out


def aggregate(symbol: str, feed: list) -> dict:
    """Reduce one ticker's article feed to a single per-symbol record.

    Each article may carry ``ticker_sentiment`` — a list of
    ``{ticker, relevance_score, ticker_sentiment_score, ticker_sentiment_label}``.
    We weight the per-article ticker sentiment by relevance, average, and
    re-label via ``label_for_score`` so the bucketing is reproducible.
    Articles without a ticker-specific score fall back to the article's
    overall ``overall_sentiment_score``.
    """
    weighted_sum = 0.0
    weight_total = 0.0
    headlines    = []
    for article in feed:
        title = (article.get("title") or "").strip()
        # Find the per-ticker score for this symbol, if present.
        per_ticker = None
        for t in article.get("ticker_sentiment") or []:
            if (t.get("ticker") or "").upper() == symbol.upper():
                per_ticker = t
                break
        if per_ticker is not None:
            try:
                score = float(per_ticker.get("ticker_sentiment_score"))
                rel   = float(per_ticker.get("relevance_score") or 0.0)
            except (TypeError, ValueError):
                continue
        else:
            try:
                score = float(article.get("overall_sentiment_score"))
                rel   = 1.0
            except (TypeError, ValueError):
                continue
        # Skip relevance-0 articles — they're noise the API attached
        # because the symbol was mentioned tangentially.
        if rel <= 0:
            continue
        weighted_sum += score * rel
        weight_total += rel
        if title:
            headlines.append({"title": title, "score": score, "relevance": rel})

    if weight_total <= 0:
        return {
            "symbol": symbol,
            "articles": [],
            "n_articles": 0,
            "score": None,
            "label": "—",
        }

    avg = weighted_sum / weight_total
    # Sort headlines by relevance for prompt surfacing (top 3 by default
    # in format_for_prompt).
    headlines.sort(key=lambda h: h["relevance"], reverse=True)
    return {
        "symbol":     symbol,
        "articles":   headlines,
        "n_articles": len(headlines),
        "score":      round(avg, 4),
        "label":      label_for_score(avg),
    }


def format_for_prompt(
    sentiment: dict,
    *,
    max_headlines: int = 3,
) -> str:
    """Render a sentiment dict (keyed by symbol) into the briefing
    prompt block.

    Layout:

        Symbol     Score    Label              Articles
        ─────────────────────────────────────────────────
        AAPL       +0.21    Somewhat-Bullish   18
          - "Apple beats Q3 earnings expectations"
          - "Services revenue hits record high"
          - …

    Empty input → empty string (caller skips the section).
    """
    if not sentiment:
        return ""

    lines = [
        "Symbol     Score    Label              Articles",
        "─" * 60,
    ]
    # If every symbol came back empty, the issue is almost certainly
    # a throttle (HTTP 200 with a 'Note' / 'Information' key) rather
    # than per-ticker coverage. Surface that honestly so Claude doesn't
    # blame US bias for what's really a rate limit.
    all_empty = all(
        (row.get("n_articles") or 0) == 0 for row in sentiment.values()
    )
    for sym, row in sentiment.items():
        score_str = f"{row['score']:+.2f}" if row.get("score") is not None else "  n/a"
        label_str = (row.get("label") or "—")[:18]
        n         = row.get("n_articles", 0)
        lines.append(
            f"{sym:<10} {score_str:>7}  {label_str:<18} {n}"
        )
        if not n:
            if all_empty:
                lines.append(
                    "    (no articles for any symbol — Alpha Vantage "
                    "likely throttled; the 25-call/day free budget is "
                    "shared with stock-collect.)"
                )
            else:
                lines.append(
                    "    (no articles — Alpha Vantage NEWS_SENTIMENT "
                    "free tier is US-biased; non-US tickers often "
                    "return empty even when the ticker is recognised.)"
                )
            continue
        for h in row.get("articles", [])[:max_headlines]:
            title = h["title"].replace("\n", " ").strip()
            if len(title) > 90:
                title = title[:87] + "…"
            lines.append(f'    - "{title}"')
    return "\n".join(lines)
