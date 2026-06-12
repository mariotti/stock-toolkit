"""
stock_toolkit.fundamentals
==========================
Per-symbol valuation snapshot via yfinance (no API key needed) and a
transparent score adjustment derived from it. Used by the Streamlit
Briefing tab (cached wrapper in ui.helpers) and by stock-score
--fundamentals.
"""


def fetch_fundamentals(symbols) -> dict:
    """Return {symbol: {trailing_pe, forward_pe, revenue_growth,
    earnings_growth}} — growth values are fractions (0.166 = +16.6% YoY).
    Missing fields are None; symbols with no data at all are omitted.
    """
    try:
        import yfinance as yf
    except ImportError:
        return {}
    out = {}
    for sym in symbols:
        try:
            info = yf.Ticker(sym).info or {}
        except Exception:
            continue
        row = {
            "trailing_pe":     info.get("trailingPE"),
            "forward_pe":      info.get("forwardPE"),
            "revenue_growth":  info.get("revenueGrowth"),
            "earnings_growth": info.get("earningsGrowth"),
        }
        if any(v is not None for v in row.values()):
            out[sym] = row
    return out


def valuation_adjustment(row: dict) -> tuple[float, str]:
    """Score adjustment (clamped to ±5 points) from the valuation snapshot.

    Rewards cheap forward earnings and growing revenue; penalizes rich
    multiples and shrinking revenue. Deliberately coarse — a tilt, not a
    valuation model.
    """
    pts = 0.0
    reasons = []
    tpe = row.get("trailing_pe")
    fpe = row.get("forward_pe")
    rg  = row.get("revenue_growth")

    if fpe is not None:
        if fpe < 15:
            pts += 2; reasons.append(f"fwd P/E {fpe:.1f}<15 +2")
        elif fpe > 40:
            pts -= 2; reasons.append(f"fwd P/E {fpe:.1f}>40 −2")
    if tpe and fpe and fpe < tpe:
        pts += 1; reasons.append("fwd<trailing P/E (earnings growth priced) +1")
    if rg is not None:
        if rg > 0.10:
            pts += 2; reasons.append(f"revenue {rg*100:+.1f}% YoY +2")
        elif rg < 0:
            pts -= 2; reasons.append(f"revenue {rg*100:+.1f}% YoY −2")

    pts = max(-5.0, min(5.0, pts))
    return pts, "; ".join(reasons) if reasons else "no valuation data"
