"""
stock_toolkit.common
====================
Shared configuration loading and filesystem paths for the stock toolkit.
All toolkit modules import from here instead of re-implementing the
config.env parser and the database path constants.

Path hierarchy (resolved once at import time):

  BASE_DIR  = $STOCK_DIR if set, else os.getcwd()
              — where config.env, bin/, etc. live
  CONFIG_PATH = BASE_DIR / "config.env"
  DATA_DIR  = where all on-disk *state* lives — DBs, state files,
              logs, historical bootstrap DBs
              — resolved via _resolve_data_dir() (see docstring)

Stable exports for downstream consumers:

  LIVE_DB       = DATA_DIR / "stock_data.db"
  HIST_DIR      = DATA_DIR / "historical"
  PORTFOLIO_DB  = DATA_DIR / "portfolio.db"

If a legacy install is detected on import (loose DBs sitting at
BASE_DIR from before the v1.17 layout), _auto_migrate() moves them
into DATA_DIR once and continues.
"""

import os
import shutil
import sys
from pathlib import Path

# Public API — the stable surface 2.x onwards commits to preserve.
# Anything not listed here (names starting with _) is implementation
# detail and may change between any two versions.
__all__ = [
    "BASE_DIR",
    "CONFIG_PATH",
    "DATA_DIR",
    "LIVE_DB",
    "HIST_DIR",
    "PORTFOLIO_DB",
    "NoDataError",
    "load_config",
    "update_config_value",
]

if os.environ.get("STOCK_DIR"):
    BASE_DIR = Path(os.environ["STOCK_DIR"]).expanduser().resolve()
else:
    BASE_DIR = Path.cwd()

CONFIG_PATH = BASE_DIR / "config.env"   # keep out of git (see .gitignore)


class NoDataError(RuntimeError):
    """No databases or rows match a request (nothing collected yet, unknown
    symbol, or an empty date range). Library functions raise this instead of
    exiting so callers like the Streamlit UI can degrade gracefully; the CLI
    main() functions catch it and exit with an error message."""


def load_config(config_path: Path = CONFIG_PATH) -> dict:
    """
    Parse a simple KEY=VALUE config file.
    - Lines starting with # are comments.
    - Inline comments (value # comment) are stripped.
    - Quoted values ("value" or 'value') have quotes stripped.
    - Missing file is silently ignored (defaults apply).
    """
    cfg: dict = {}
    if not config_path.exists():
        return cfg
    with open(config_path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            # strip inline comment (covers both "value # comment" and "  # comment")
            if val.startswith("#"):
                val = ""
            elif " #" in val:
                val = val[:val.index(" #")].strip()
            # strip matching quotes
            if (val.startswith('"') and val.endswith('"')) or \
               (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            cfg[key] = val
    return cfg


def update_config_value(key: str, value: str,
                        config_path: Path = CONFIG_PATH) -> None:
    """Rewrite a single KEY=value line in config.env, preserving everything else.

    - If the file doesn't exist, write a minimal one with just the new line.
    - If the key exists, replace its value in place (keep an inline comment
      if present, keep surrounding lines untouched).
    - If the key doesn't exist, append it at the end.

    Used by the UI admin page to edit SYMBOLS / SYMBOLS_IGNORE without
    losing user-edited comments, paid-tier flags, API keys, etc.
    """
    new_line = f"{key}={value}\n"
    if not config_path.exists():
        config_path.write_text(new_line)
        return

    lines = config_path.read_text().splitlines(keepends=True)
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped.startswith(f"{key}="):
            continue
        comment = ""
        if "#" in line and "#" not in stripped[: len(key) + 1]:
            _, _, comment = line.partition("#")
            comment = "  # " + comment.lstrip("# ").rstrip("\n")
        lines[i] = f"{key}={value}{comment}\n"
        config_path.write_text("".join(lines))
        return

    # Key not present — append cleanly
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append(new_line)
    config_path.write_text("".join(lines))


# ─────────────────────────────────────────────────────────────────────────
#  DATA_DIR — single root for every on-disk state file
# ─────────────────────────────────────────────────────────────────────────

# Loose state files to relocate from BASE_DIR → DATA_DIR on first run
# of the v1.17 layout. Order matters only insofar as we always move the
# main DB last so a half-failed migration is detectable.
_LEGACY_STATE_FILES = (
    ".collector_state.json",
    ".alerts_state.json",
    "stock_data.csv",
    "stock_failures.csv",
    "stock_failures_report.csv",
    "stock_failures.db",
    "stock_failures.db-shm",
    "stock_failures.db-wal",
    "portfolio.db",
    "portfolio.db-shm",
    "portfolio.db-wal",
    "stock_data.db",
    "stock_data.db-shm",
    "stock_data.db-wal",
)


def _resolve_data_dir() -> Path:
    """Single root for all on-disk state. Precedence:

      1. ``DATA_DIR`` in config.env — the v1.19 spelling.
      2. ``OUTPUT_DIR`` in config.env — pre-v1.19 spelling. Honoured
         with a one-shot DeprecationWarning so existing installs keep
         working; will be removed in 3.x.
      3. ``STOCK_DIR`` env var set — Docker / production mode. The
         bind-mount root IS the data dir; do not nest a second
         ``data/`` inside it.
      4. ``BASE_DIR / "data"`` — fresh native install, keeps the
         source tree clean (this is the v1.17 default).
    """
    cfg = load_config(CONFIG_PATH)
    explicit = (cfg.get("DATA_DIR") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    legacy = (cfg.get("OUTPUT_DIR") or "").strip()
    if legacy:
        import warnings as _warnings
        _warnings.warn(
            "config.env: OUTPUT_DIR is deprecated as of v1.19; rename to "
            "DATA_DIR. The old name still works in 2.x but will be removed "
            "in 3.x.",
            DeprecationWarning,
            stacklevel=2,
        )
        return Path(legacy).expanduser().resolve()
    if os.environ.get("STOCK_DIR"):
        return BASE_DIR
    return BASE_DIR / "data"


def _auto_migrate(data_dir: Path) -> None:
    """Move legacy loose state files from BASE_DIR into DATA_DIR
    and rename the legacy ``HIST_DIR`` (``BASE_DIR/data/``) to the
    new ``DATA_DIR/historical/``. Idempotent — re-running it is a
    no-op once the layout is already v1.17.
    """
    # Step 1 — record what counts as a "historical bootstrap DB"
    # BEFORE any moves change the layout. After we relocate loose
    # files into DATA_DIR (= BASE_DIR/data in the common native case),
    # a naive glob would otherwise capture the freshly-moved
    # stock_data.db as a "historical".
    old_hist = BASE_DIR / "data"
    new_hist = data_dir / "historical"
    historicals = []
    if old_hist.exists() and old_hist != new_hist:
        historicals = [
            p for p in old_hist.glob("*.db")
            if p.name not in _LEGACY_STATE_FILES and p.is_file()
        ]

    moved_files = []

    # Step 2 — move loose state files BASE_DIR → DATA_DIR. Skipped
    # entirely when the two are equal (Docker / OUTPUT_DIR=BASE_DIR).
    if data_dir != BASE_DIR:
        for name in _LEGACY_STATE_FILES:
            src = BASE_DIR / name
            dst = data_dir / name
            if not src.exists() or dst.exists():
                continue
            try:
                data_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                moved_files.append(name)
            except OSError as e:
                print(
                    f"[stock-toolkit] migration: could not move "
                    f"{src} → {dst}: {e}",
                    file=sys.stderr,
                )

    # Step 3 — relocate the historicals collected in step 1. The list
    # was frozen before step 2 so we never mistake a freshly-moved
    # live DB for a historical.
    if historicals:
        try:
            new_hist.mkdir(parents=True, exist_ok=True)
            for p in historicals:
                target = new_hist / p.name
                if not target.exists() and p.exists():
                    shutil.move(str(p), str(target))
            # Husk cleanup: rm the old hist dir only if it's now empty.
            if old_hist.exists() and not any(old_hist.iterdir()):
                old_hist.rmdir()
        except OSError as e:
            print(
                f"[stock-toolkit] migration: could not move historical "
                f"DBs to {new_hist}: {e}",
                file=sys.stderr,
            )

    if moved_files or historicals:
        parts = []
        if moved_files:
            parts.append(
                f"moved {len(moved_files)} legacy file(s) → {data_dir}"
            )
        if historicals:
            parts.append(
                f"relocated {len(historicals)} historical DB(s) → {new_hist}"
            )
        print(
            f"[stock-toolkit] v1.17 layout migration: {'; '.join(parts)}",
            file=sys.stderr,
        )


DATA_DIR = _resolve_data_dir()
_auto_migrate(DATA_DIR)

LIVE_DB      = DATA_DIR / "stock_data.db"
HIST_DIR     = DATA_DIR / "historical"
PORTFOLIO_DB = DATA_DIR / "portfolio.db"
