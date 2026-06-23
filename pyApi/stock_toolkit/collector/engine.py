"""
stock_toolkit.collector.engine
==============================

Engine dispatcher for ``stock-collect --engine rust``. The default
engine is the in-process Python collector — calling main() on this
module from the CLI without ``--engine`` is a no-op (returns False,
caller proceeds with the Python path).

Why a shim rather than a port:
  The Python collector is correct and tested. The Rust fetcher is
  faster (concurrent per-source) but only implements Alpha Vantage
  today (v2.3.x). The shim lets users opt in source-by-source as
  the Rust side gains coverage, without rewriting their cron jobs.
  Anything Rust can't do yet, falls back honestly to Python.

Discovery order for the Rust binary:
  1. ``$STOCK_FETCHER_BIN`` env var (absolute or relative path).
  2. ``rust-fetcher/target/release/stock-fetcher`` relative to the
     repo root (the typical dev-checkout layout).
  3. ``shutil.which('stock-fetcher')`` — anywhere on PATH.

If none of those resolve, ``--engine rust`` exits with a friendly
message pointing at ``rust-fetcher/README.md`` (build it with
``cargo build --release``). It never silently falls back to Python
on a missing binary — explicit intent is the whole point of the
flag.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


# Sources the Rust fetcher currently supports. Python handles the rest.
#
# CROSS-LANGUAGE CONTRACT — keep in sync with `rust-fetcher/src/main.rs`,
# specifically the `match source_name.as_str()` arm. If you add a source
# to Rust without updating this set, `stock-collect --engine rust
# --sources <new>` will be rejected with rc=2 *before* the binary is
# invoked. (Safe failure, confusing failure — update both at once.)
RUST_SUPPORTED_SOURCES = frozenset({"alphavantage"})


def find_rust_binary() -> Optional[Path]:
    """Locate stock-fetcher. Returns the path or None if not found."""
    # 1. Explicit env override.
    env_path = os.environ.get("STOCK_FETCHER_BIN", "").strip()
    if env_path:
        p = Path(env_path).expanduser().resolve()
        if p.is_file() and os.access(p, os.X_OK):
            return p
        return None

    # 2. Dev checkout layout. common.BASE_DIR points at pyApi/ (or
    # the user's $STOCK_DIR). Repo root = BASE_DIR.parent.
    from stock_toolkit.common import BASE_DIR
    candidate = (
        BASE_DIR.parent / "rust-fetcher" / "target" / "release" / "stock-fetcher"
    )
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate

    # 3. PATH lookup.
    which = shutil.which("stock-fetcher")
    if which:
        return Path(which)

    return None


def unsupported_sources(sources) -> list:
    """Return the subset of ``sources`` the Rust fetcher can't handle yet."""
    return [s for s in (sources or []) if s not in RUST_SUPPORTED_SOURCES]


def run_rust(
    sources: list,
    symbols: list,
    *,
    binary: Optional[Path] = None,
    db: Optional[Path] = None,
    extra_args: Optional[list] = None,
) -> int:
    """Subprocess out to the Rust binary. Returns the binary's exit code.

    Streams stdout / stderr live so logs surface in real time (Rust
    side emits structured tracing; users see it as it happens).
    """
    binary = binary or find_rust_binary()
    if binary is None:
        print(
            "stock-collect: --engine rust requested but `stock-fetcher` "
            "binary not found.\n"
            "  Build it with:\n"
            "    cd rust-fetcher && cargo build --release\n"
            "  Or set STOCK_FETCHER_BIN to its path.\n"
            "  See rust-fetcher/README.md for details.",
            file=sys.stderr,
        )
        return 127

    bad = unsupported_sources(sources)
    if bad:
        supported = ", ".join(sorted(RUST_SUPPORTED_SOURCES))
        print(
            f"stock-collect: --engine rust requested with unsupported "
            f"source(s) {bad}.\n"
            f"  Rust currently supports: {supported}.\n"
            "  Either drop the unsupported source(s), or omit "
            "--engine to use the Python collector.",
            file=sys.stderr,
        )
        return 2

    argv = [str(binary)]
    if sources:
        argv += ["--sources", ",".join(sources)]
    if symbols:
        argv += ["--symbols", ",".join(symbols)]
    if db is not None:
        argv += ["--db", str(db)]
    if extra_args:
        argv += extra_args

    # Inherit stdin/stdout/stderr so the user sees structured logs live.
    # The Rust binary uses tracing-subscriber → STDERR by default, which
    # composes cleanly with the Python collector's logging output.
    try:
        result = subprocess.run(argv, check=False)
    except FileNotFoundError:
        # Race: binary disappeared between find and exec (unlikely
        # but explicit).
        print(
            f"stock-collect: could not execute {binary}", file=sys.stderr,
        )
        return 127
    return result.returncode
