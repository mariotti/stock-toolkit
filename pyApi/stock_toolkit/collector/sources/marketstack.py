"""Marketstack fetcher (live only)."""


from .. import config as cfg
from ..config import log
from ..db import (
    make_row, _live_has_today,
)
from ..failures import is_suppressed, record_failure
from ..http import safe_get
from ..state import budget_ok, record_call

# ── 7. Marketstack ────────────────────────────
def fetch_marketstack(symbols: list[str], state: dict) -> list[dict]:
    """
    /eod — end-of-day OHLCV for multiple symbols in one call.
    V2 API (v1 deprecated June 2025). HTTPS available on all plans.
    Free: 100 calls/month. Budget conservatively — only fetch when budget allows.
    """
    key = cfg.API_KEYS["marketstack"]
    if not key:
        return []
    if not budget_ok(state, "marketstack"):
        return []

    # skip suppressed symbols and those already collected today
    pending = [s for s in symbols
               if not is_suppressed(s, "marketstack")
               and not _live_has_today(s, "marketstack")]
    if not pending:
        log.info("[marketstack] all symbols already collected today, skipping")
        return []
    rows = []
    sym_str = ",".join(pending)
    data = safe_get(
        "https://api.marketstack.com/v2/eod",
        params={"access_key": key, "symbols": sym_str, "limit": 100}
    )
    if not data:
        log.warning("[marketstack] no response — check network or API key")
        return []
    if isinstance(data, dict) and data.get("_error") == 429:
        # Monthly limit hit — exhaust the budget so we stop trying this month
        remaining = cfg.MONTHLY_LIMITS["marketstack"] - state["monthly_calls"].get("marketstack", 0)
        if remaining > 0:
            state["monthly_calls"]["marketstack"] = cfg.MONTHLY_LIMITS["marketstack"]
        log.warning(
            f"[marketstack] monthly limit hit (429) — "
            f"skipping for rest of {state.get('month','this month')}"
        )
        return []
    if "error" in data:
        err = data["error"]
        log.warning(f"[marketstack] API error {err.get('code','?')}: "
                    f"{err.get('message','unknown')}")
        return []
    if "data" not in data:
        log.warning(f"[marketstack] unexpected response keys: {list(data.keys())}")
        return []
    record_call(state, "marketstack")   # only count successful calls
    returned_syms = set()
    for bar in data["data"]:
        sym = bar["symbol"].split(".")[0]
        returned_syms.add(sym)
        rows.append(make_row(
            sym, "marketstack",
            bar["date"][:10], "1d",
            bar.get("open"), bar.get("high"), bar.get("low"), bar.get("close"),
            bar.get("volume"), vwap=bar.get("adj_close"),
            extra={"exchange": bar.get("exchange")}
        ))
    # record failures for symbols that returned no data
    for sym in pending:
        if sym not in returned_syms:
            record_failure(sym, "marketstack", "no data returned")
    log.info(f"[marketstack] {len(data['data'])} EOD bars across {len(pending)} symbols")
    return rows




