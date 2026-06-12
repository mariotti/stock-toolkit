"""Collector configuration: config.env values, paths, rate limits, logging."""

import logging
import logging.handlers
from pathlib import Path

from stock_toolkit.common import BASE_DIR, CONFIG_PATH, load_config

# ─────────────────────────────────────────────
#  CONFIG — loaded from config.env, with
#           hardcoded defaults as fallback
# ─────────────────────────────────────────────


_cfg = load_config(CONFIG_PATH)

if _cfg:
    _src = str(CONFIG_PATH)
else:
    _src = "built-in defaults (config.env not found)"

# ── symbols ───────────────────────────────────────────────────────────────────

# SYMBOLS in config.env is a comma-separated list, e.g.:
#   SYMBOLS=AAPL,MSFT,GOOGL,AMZN,TSLA
_sym_raw = _cfg.get("SYMBOLS", "AAPL,MSFT,GOOGL,AMZN,TSLA")
SYMBOLS = [s.strip().upper() for s in _sym_raw.split(",") if s.strip()]

# SYMBOLS_IGNORE — symbols to never collect, even if in config or DB.
# Use this to block bare EU tickers (e.g. ENI, ENEL) that are duplicates
# of the exchange-suffixed versions (ENI.MI, ENEL.MI).
#   SYMBOLS_IGNORE=ENI,ENEL,CSMIB,SAP
_ignore_raw = _cfg.get("SYMBOLS_IGNORE", "")
SYMBOLS_IGNORE = {s.strip().upper() for s in _ignore_raw.split(",") if s.strip()}

# FAILURE_THRESHOLD — stop requesting a (symbol, source) pair after this many
# consecutive failures. Recorded in stock_failures.csv, editable by hand.
FAILURE_THRESHOLD = int(_cfg.get("FAILURE_THRESHOLD", "5"))


# ── per-source symbol aliases ─────────────────────────────────────────────────
# Some APIs name the same instrument differently (e.g. Marketstack wants the
# bare Milan ticker ENEL where everything else wants ENEL.MI). Aliases let
# the watchlist hold ONE canonical symbol while each source is queried with
# the name it understands; returned rows are stored under the canonical name.
#
#   SYMBOL_ALIASES=marketstack:ENEL.MI=ENEL,marketstack:ENI.MI=ENI
#
# Format: comma-separated  source:CANONICAL=ALIAS  entries.

def parse_symbol_aliases(raw: str) -> dict:
    """Parse SYMBOL_ALIASES into {source: {CANONICAL: ALIAS}}."""
    aliases: dict = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry or "=" not in entry:
            continue
        source, _, mapping = entry.partition(":")
        canonical, _, alias = mapping.partition("=")
        source, canonical, alias = (source.strip().lower(),
                                    canonical.strip().upper(),
                                    alias.strip().upper())
        if source and canonical and alias:
            aliases.setdefault(source, {})[canonical] = alias
    return aliases


SYMBOL_ALIASES = parse_symbol_aliases(_cfg.get("SYMBOL_ALIASES", ""))


def aliased_symbols(source: str, symbols: list) -> list:
    """Translate canonical symbols to the names this source understands."""
    amap = SYMBOL_ALIASES.get(source, {})
    return [amap.get(s, s) for s in symbols]


def canonicalize_rows(source: str, rows: list) -> list:
    """Map row symbols back from source aliases to canonical names."""
    amap = SYMBOL_ALIASES.get(source, {})
    if not amap:
        return rows
    rev = {alias: canonical for canonical, alias in amap.items()}
    for row in rows:
        row["symbol"] = rev.get(row["symbol"], row["symbol"])
    return rows


# ── API keys ──────────────────────────────────────────────────────────────────

API_KEYS = {
    "alphavantage": _cfg.get("ALPHAVANTAGE_KEY", ""),
    "finnhub":      _cfg.get("FINNHUB_KEY",      ""),
    "polygon":      _cfg.get("MASSIVE_KEY", "") or _cfg.get("POLYGON_KEY", ""),   # MASSIVE_KEY preferred; POLYGON_KEY accepted for backward compatibility
    "fmp":          _cfg.get("FMP_KEY",           ""),
    "twelvedata":   _cfg.get("TWELVEDATA_KEY",    ""),
    "marketstack":  _cfg.get("MARKETSTACK_KEY",   ""),
}

# ── paid tier flags ───────────────────────────────────────────────────────────

# FINNHUB_PAID=true    → unlocks /stock/candle (OHLCV bars)
# ALPHAVANTAGE_PAID=true → unlocks TIME_SERIES_DAILY_ADJUSTED + full history
FINNHUB_PAID      = _cfg.get("FINNHUB_PAID",      "false").lower() == "true"
ALPHAVANTAGE_PAID = _cfg.get("ALPHAVANTAGE_PAID", "false").lower() == "true"

# ── paths ─────────────────────────────────────────────────────────────────────

OUTPUT_DIR     = Path(_cfg.get("OUTPUT_DIR", str(BASE_DIR)))
DB_PATH        = OUTPUT_DIR / _cfg.get("DB_FILE",      "stock_data.db")
CSV_PATH       = OUTPUT_DIR / _cfg.get("CSV_FILE",     "stock_data.csv")
STATE_PATH     = OUTPUT_DIR / _cfg.get("STATE_FILE",   ".collector_state.json")
LOG_PATH       = OUTPUT_DIR / _cfg.get("LOG_FILE",     "collector.log")
GNUPLOT_DIR    = OUTPUT_DIR / _cfg.get("GNUPLOT_DIR",  "gnuplot-data")
MATPLOTLIB_DIR = OUTPUT_DIR / _cfg.get("MATPLOT_DIR",  "matplot")
HIST_DIR       = OUTPUT_DIR / _cfg.get("HIST_DIR",     "data")
FAILURES_DB_PATH     = OUTPUT_DIR / _cfg.get("FAILURES_DB",     "stock_failures.db")
FAILURES_REPORT_PATH = OUTPUT_DIR / _cfg.get("FAILURES_REPORT", "stock_failures_report.csv")

# ── rate limits (not user-configurable via config.env) ───────────────────────

DAILY_LIMITS = {
    "alphavantage": 25,    # 25 calls / day
    "finnhub":      None,  # 60 calls / minute — no daily cap, handled below
    "polygon":      None,  # 5 calls / minute  — no daily cap
    "fmp":          250,   # 250 calls / day
    "twelvedata":   800,   # 800 calls / day
}

MONTHLY_LIMITS = {
    "marketstack":  100,   # 100 calls / month (free tier)
}

MINUTE_LIMITS = {
    "finnhub":  60,
    "polygon":  5,
}

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=1_000_000, backupCount=3
        ),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)
log.debug(f"Config loaded from: {_src}")

