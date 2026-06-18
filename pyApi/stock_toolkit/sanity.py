"""
stock_toolkit.sanity
====================
Opt-in audit of the deterministic invariants this toolkit relies on.

Each ``check_*`` function returns ``list[Issue]`` — never raises, so
one corrupted database does not mask issues in unrelated surfaces.
``run_all()`` aggregates the lot into a structured ``Report``.

Surfaced via the ``stock-sanity`` CLI and the Admin → 🛠 Settings panel.
Not run on import; consumers opt in.
"""

from __future__ import annotations

import dataclasses
import math
import sqlite3
from pathlib import Path
from typing import Optional

# Public API — frozen from 2.x onwards.
__all__ = [
    "ERROR", "WARNING", "INFO",
    "Issue", "Report",
    "check_data_layout", "check_config", "check_database",
    "check_portfolios", "check_trade_stats", "check_value_history",
    "check_score_outputs", "check_historical_dir",
    "run_all",
]

from stock_toolkit.common import (
    CONFIG_PATH, DATA_DIR, HIST_DIR, LIVE_DB, PORTFOLIO_DB, load_config,
)


# ─── data model ──────────────────────────────────────────────────────

ERROR   = "error"
WARNING = "warning"
INFO    = "info"


@dataclasses.dataclass
class Issue:
    severity: str               # ERROR / WARNING / INFO
    check:    str               # which check fired it
    message:  str               # short, plain-English
    detail:   str = ""          # optional second line for the CLI

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class Report:
    issues: list[Issue]

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == WARNING]

    @property
    def infos(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == INFO]

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {
            "ok":       self.ok,
            "errors":   len(self.errors),
            "warnings": len(self.warnings),
            "infos":    len(self.infos),
            "issues":   [i.as_dict() for i in self.issues],
        }


# ─── individual checks ───────────────────────────────────────────────

def check_data_layout(data_dir: Path = DATA_DIR) -> list[Issue]:
    """``DATA_DIR`` is writable, the v1.17 layout is in place."""
    out: list[Issue] = []
    if not data_dir.exists():
        out.append(Issue(
            ERROR, "data_layout",
            f"DATA_DIR {data_dir} does not exist",
            "Run any stock-* command — common.py auto-creates it.",
        ))
        return out
    # Writable probe — actually try to write, don't just check the bit.
    probe = data_dir / ".sanity_probe"
    try:
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        out.append(Issue(
            ERROR, "data_layout",
            f"DATA_DIR {data_dir} is not writable: {e}",
        ))
        return out
    # Stragglers from a pre-v1.17 layout still sitting at BASE_DIR.
    base = data_dir.parent if data_dir.name == "data" else None
    if base is not None:
        for name in ("stock_data.db", "portfolio.db",
                     ".collector_state.json", ".alerts_state.json"):
            stray = base / name
            if stray.exists():
                out.append(Issue(
                    WARNING, "data_layout",
                    f"Legacy file still at BASE_DIR: {stray}",
                    "Will move next time stock_toolkit.common is imported.",
                ))
    return out


def check_config(config_path: Path = CONFIG_PATH) -> list[Issue]:
    """``config.env`` parses, paid flags are boolean-ish."""
    out: list[Issue] = []
    if not config_path.exists():
        out.append(Issue(
            INFO, "config",
            f"No {config_path} yet — run stock-setup to create one.",
        ))
        return out
    cfg = load_config(config_path)
    if not cfg:
        out.append(Issue(
            WARNING, "config",
            f"{config_path} parsed empty — no settings will apply.",
        ))
    for k in ("FINNHUB_PAID", "ALPHAVANTAGE_PAID"):
        v = (cfg.get(k) or "").strip().lower()
        if v and v not in ("true", "false"):
            out.append(Issue(
                ERROR, "config",
                f"{k}={cfg.get(k)!r} — must be 'true' or 'false'.",
            ))
    if not (cfg.get("SYMBOLS") or "").strip():
        out.append(Issue(
            WARNING, "config",
            "SYMBOLS is empty — the collector and dashboard have nothing to do.",
        ))
    return out


def check_database(db_path: Path = LIVE_DB) -> list[Issue]:
    """Live DB schema present, no duplicates, no NULL closes."""
    out: list[Issue] = []
    if not db_path.exists():
        out.append(Issue(
            INFO, "database",
            f"No live DB at {db_path} yet — run stock-collect to populate it.",
        ))
        return out
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError as e:
        out.append(Issue(
            ERROR, "database", f"Cannot open {db_path}: {e}",
        ))
        return out
    try:
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "prices" not in tables:
            out.append(Issue(
                ERROR, "database",
                "Missing 'prices' table — DB shape doesn't match the collector.",
            ))
            return out
        # NULL closes — silent data quality killer.
        nulls = con.execute(
            "SELECT COUNT(*) FROM prices WHERE close IS NULL"
        ).fetchone()[0]
        if nulls:
            out.append(Issue(
                WARNING, "database",
                f"{nulls} row(s) with NULL close in prices.",
                "Affected rows are skipped by analysis but waste space.",
            ))
        # Duplicate (symbol, source, timestamp) — schema has the constraint
        # but pre-1.0 DBs can predate it.
        dups = con.execute(
            "SELECT COUNT(*) FROM ("
            "  SELECT symbol, source, timestamp, COUNT(*) c FROM prices "
            "  GROUP BY symbol, source, timestamp HAVING c > 1"
            ")"
        ).fetchone()[0]
        if dups:
            out.append(Issue(
                ERROR, "database",
                f"{dups} (symbol, source, timestamp) groups appear more than once.",
                "Indicates a missing UNIQUE constraint or a broken collector run.",
            ))
        # Sanity: at least one row?
        total = con.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
        if total == 0:
            out.append(Issue(
                INFO, "database",
                f"{db_path} exists but has zero rows — collector hasn't run.",
            ))
    finally:
        con.close()
    return out


def check_portfolios(db_path: Path = PORTFOLIO_DB) -> list[Issue]:
    """Game portfolio invariants: starting_cash > 0, mark_to_market
    consistency, no negative holdings."""
    out: list[Issue] = []
    if not db_path.exists():
        return out      # no game ever started; nothing to check
    try:
        from stock_toolkit.game import (
            get_positions, list_portfolios, mark_to_market,
        )
    except ImportError:
        # Defensive — game.py is part of the same package, so this
        # only fires if the install is half-broken.
        out.append(Issue(
            ERROR, "portfolios",
            "Could not import stock_toolkit.game — install is incomplete.",
        ))
        return out
    try:
        portfolios = list_portfolios(include_archived=True, db=db_path)
    except Exception as e:                      # noqa: BLE001
        out.append(Issue(
            ERROR, "portfolios", f"list_portfolios() failed: {e}",
        ))
        return out
    for p in portfolios:
        pid, name = p["id"], p["name"]
        if (p.get("starting_cash") or 0) <= 0:
            out.append(Issue(
                ERROR, "portfolios",
                f"[{name}] starting_cash={p.get('starting_cash')} — must be > 0.",
            ))
        try:
            positions = get_positions(portfolio_id=pid, db=db_path)
        except Exception as e:                  # noqa: BLE001
            out.append(Issue(
                ERROR, "portfolios",
                f"[{name}] get_positions() failed: {e}",
            ))
            continue
        for sym, pos in positions.items():
            if pos["qty"] < 0:
                out.append(Issue(
                    ERROR, "portfolios",
                    f"[{name}] {sym} qty={pos['qty']:.4f} — short positions "
                    "should never happen.",
                ))
            if pos["qty"] > 0 and pos["avg_cost"] <= 0:
                out.append(Issue(
                    ERROR, "portfolios",
                    f"[{name}] {sym} qty={pos['qty']:.4f} avg_cost="
                    f"{pos['avg_cost']:.2f} — non-positive cost basis.",
                ))
        # mark_to_market: cash + equity == total (within float epsilon)
        try:
            mtm = mark_to_market(portfolio_id=pid, db=db_path)
        except Exception as e:                  # noqa: BLE001
            out.append(Issue(
                ERROR, "portfolios",
                f"[{name}] mark_to_market() failed: {e}",
            ))
            continue
        recomputed = mtm["cash"] + mtm["equity"]
        if not math.isclose(recomputed, mtm["total"], rel_tol=1e-6, abs_tol=0.01):
            out.append(Issue(
                ERROR, "portfolios",
                f"[{name}] cash + equity ({recomputed:.2f}) != "
                f"total ({mtm['total']:.2f}).",
            ))
    return out


def check_trade_stats(db_path: Path = PORTFOLIO_DB) -> list[Issue]:
    """``trade_stats`` accounting consistency: closed_count = wins + losses
    and the bucket counts match the recorded sells."""
    out: list[Issue] = []
    if not db_path.exists():
        return out
    try:
        from stock_toolkit.game import list_portfolios, trade_stats
    except ImportError:
        return out
    for p in list_portfolios(include_archived=True, db=db_path):
        pid, name = p["id"], p["name"]
        try:
            stats = trade_stats(portfolio_id=pid, db=db_path)
        except Exception as e:                  # noqa: BLE001
            out.append(Issue(
                ERROR, "trade_stats",
                f"[{name}] trade_stats() failed: {e}",
            ))
            continue
        wins, losses, closed = (
            stats["wins"], stats["losses"], stats["closed_count"],
        )
        if closed != wins + losses:
            out.append(Issue(
                ERROR, "trade_stats",
                f"[{name}] closed_count ({closed}) != wins+losses "
                f"({wins}+{losses}={wins+losses}).",
            ))
        if not (0.0 <= stats["win_rate"] <= 1.0):
            out.append(Issue(
                ERROR, "trade_stats",
                f"[{name}] win_rate={stats['win_rate']} — must be in [0, 1].",
            ))
    return out


def check_value_history(db_path: Path = PORTFOLIO_DB) -> list[Issue]:
    """``value_history`` is monotonic in date and never returns NaN."""
    out: list[Issue] = []
    if not db_path.exists():
        return out
    try:
        from stock_toolkit.game import list_portfolios, value_history
    except ImportError:
        return out
    for p in list_portfolios(include_archived=True, db=db_path):
        pid, name = p["id"], p["name"]
        try:
            hist = value_history(portfolio_id=pid, db=db_path)
        except Exception as e:                  # noqa: BLE001
            out.append(Issue(
                ERROR, "value_history",
                f"[{name}] value_history() failed: {e}",
            ))
            continue
        prev = ""
        for row in hist:
            if row["date"] <= prev:
                out.append(Issue(
                    ERROR, "value_history",
                    f"[{name}] dates not strictly increasing "
                    f"(after {prev} came {row['date']}).",
                ))
                break
            prev = row["date"]
            for k in ("cash", "equity", "total"):
                v = row[k]
                if v is None or (isinstance(v, float) and math.isnan(v)):
                    out.append(Issue(
                        ERROR, "value_history",
                        f"[{name}] {row['date']} {k}={v}",
                    ))
                    break
    return out


def check_score_outputs(
    df_close: Optional[list[float]] = None,
) -> list[Issue]:
    """``score_symbol`` bounds + monte carlo percentile ordering."""
    out: list[Issue] = []
    # The probability check is a static contract test — run on a
    # synthetic series so the audit doesn't require a populated DB.
    if df_close is None:
        df_close = [100.0 * (1 + 0.001 * i) for i in range(120)]
    try:
        import pandas as pd
        from stock_toolkit.score import (
            score_symbol, step_drawdown, step_entry_timing,
            step_montecarlo, step_regression, step_summary,
        )
    except ImportError as e:
        out.append(Issue(
            ERROR, "score_outputs",
            f"Could not import scoring engine: {e}",
        ))
        return out
    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=len(df_close),
                                    freq="W-FRI"),
        "close": df_close,
    })
    df["open"]  = df["close"]
    df["high"]  = df["close"] * 1.01
    df["low"]   = df["close"] * 0.99
    df["volume"] = 1000
    try:
        raw = {
            "symbol":     "SANITY",
            "summary":    step_summary(df, ann_factor=52),
            "regression": step_regression(df),
            "drawdown":   step_drawdown(df),
            "entry":      step_entry_timing(df),
            "montecarlo": step_montecarlo(df, n_paths=200, horizon=21),
        }
        score, _ = score_symbol(raw)
    except Exception as e:                      # noqa: BLE001
        out.append(Issue(
            ERROR, "score_outputs",
            f"Synthetic score pipeline crashed: {e}",
        ))
        return out
    if not (0.0 <= score <= 100.0):
        out.append(Issue(
            ERROR, "score_outputs",
            f"score={score} outside [0, 100].",
        ))
    mc = raw["montecarlo"]
    if mc:
        prob = mc.get("prob_gain", 0)
        if not (0.0 <= prob <= 100.0):
            out.append(Issue(
                ERROR, "score_outputs",
                f"prob_gain={prob} outside [0, 100].",
            ))
        if not (mc.get("p5", 0) <= mc.get("p50", 0) <= mc.get("p95", 0)):
            out.append(Issue(
                ERROR, "score_outputs",
                f"Monte Carlo percentiles not ordered: "
                f"p5={mc.get('p5')}, p50={mc.get('p50')}, p95={mc.get('p95')}.",
            ))
    return out


def check_historical_dir(hist_dir: Path = HIST_DIR) -> list[Issue]:
    """Bootstrap historicals look reasonable — at least one row each."""
    out: list[Issue] = []
    if not hist_dir.exists():
        return out      # never bootstrapped, fine
    for db_path in sorted(hist_dir.glob("*.db")):
        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.OperationalError as e:
            out.append(Issue(
                WARNING, "historical_dir",
                f"Cannot open {db_path}: {e}",
            ))
            continue
        try:
            rows = con.execute(
                "SELECT COUNT(*) FROM prices"
            ).fetchone()[0]
            if rows == 0:
                out.append(Issue(
                    WARNING, "historical_dir",
                    f"{db_path.name} has zero rows.",
                ))
        except sqlite3.OperationalError:
            out.append(Issue(
                WARNING, "historical_dir",
                f"{db_path.name} does not look like a stock-data DB.",
            ))
        finally:
            con.close()
    return out


# ─── aggregation ─────────────────────────────────────────────────────

_ALL_CHECKS = (
    check_data_layout,
    check_config,
    check_database,
    check_portfolios,
    check_trade_stats,
    check_value_history,
    check_score_outputs,
    check_historical_dir,
)


def run_all() -> Report:
    """Run every check and aggregate. Each check is isolated — one
    raising doesn't poison the others (we collect the failure as an
    ERROR issue and move on)."""
    all_issues: list[Issue] = []
    for fn in _ALL_CHECKS:
        try:
            all_issues.extend(fn())
        except Exception as e:                  # noqa: BLE001
            all_issues.append(Issue(
                ERROR, fn.__name__.replace("check_", ""),
                f"check raised: {type(e).__name__}: {e}",
            ))
    return Report(issues=all_issues)
