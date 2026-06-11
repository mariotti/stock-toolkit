"""Per-day/per-month API call budgets, persisted across runs."""

import json
from datetime import date

from . import config as cfg
from .config import log

# ─────────────────────────────────────────────
#  STATE — persists daily call counts across runs
# ─────────────────────────────────────────────

def load_state() -> dict:
    today = str(date.today())
    month = today[:7]   # "2026-04"
    if cfg.STATE_PATH.exists():
        with open(cfg.STATE_PATH) as f:
            state = json.load(f)
        # reset daily counters if date changed
        if state.get("date") != today:
            state["date"]  = today
            state["calls"] = {}
        # reset monthly counters if month changed
        if state.get("month") != month:
            state["month"]         = month
            state["monthly_calls"] = {}
        # ensure keys exist for older state files
        state.setdefault("monthly_calls", {})
    else:
        state = {
            "date":          today,
            "month":         month,
            "calls":         {},
            "monthly_calls": {},
        }
    return state

def save_state(state: dict):
    with open(cfg.STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def budget_ok(state: dict, source: str) -> bool:
    # Check daily limit
    daily_limit = cfg.DAILY_LIMITS.get(source)
    if daily_limit is not None:
        used = state["calls"].get(source, 0)
        if used >= daily_limit:
            log.warning(f"[{source}] daily budget exhausted ({used}/{daily_limit}), skipping.")
            return False
    # Check monthly limit
    monthly_limit = cfg.MONTHLY_LIMITS.get(source)
    if monthly_limit is not None:
        used = state["monthly_calls"].get(source, 0)
        if used >= monthly_limit:
            log.warning(
                f"[{source}] monthly budget exhausted "
                f"({used}/{monthly_limit} for {state.get('month','?')}), skipping."
            )
            return False
    return True


def record_call(state: dict, source: str, n: int = 1):
    state["calls"][source] = state["calls"].get(source, 0) + n
    if source in cfg.MONTHLY_LIMITS:
        state["monthly_calls"][source] = state["monthly_calls"].get(source, 0) + n

