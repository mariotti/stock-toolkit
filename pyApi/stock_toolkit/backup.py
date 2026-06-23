"""
stock_toolkit.backup
====================
Snapshot the live state directory into ``data/backups/``.

Why a separate tool instead of cron'd `cp`:

  Live SQLite files are usually open by `stock-ui` or a fresh
  `stock-collect`. `cp` of an open DB is *not safe* — you can get a
  half-written page mid-checkpoint and a corrupted copy. SQLite's
  ``VACUUM INTO`` reads the DB through the normal locking protocol
  and writes a clean, fully-consistent file at the destination,
  ignoring WAL state. Use it instead.

  JSON state files (`.collector_state.json`, `.alerts_state.json`)
  are small, atomically rewritten by the writer, so `shutil.copy2`
  is fine for them.

Layout:

  DATA_DIR/
    backups/
      2026-06-23-1900/             # manual or scheduled snapshot
        portfolio.db
        stock_data.db
        stock_failures.db
        .collector_state.json
        .alerts_state.json
        manifest.json              # what was snapshotted + sizes
      pre-destructive/
        2026-06-23-1903-pre-delete-portfolio-2/
          portfolio.db              # only the DB the op touches
          manifest.json
        2026-06-23-1910-pre-reset-portfolio-1/
          portfolio.db
          manifest.json

Manual snapshots rotate (default: keep last 30); pre-destructive
snapshots are kept indefinitely — they're the safety net.

Public API:

  snapshot(reason=None, db_paths=None, dest_root=None) -> Path
      Create a snapshot. Returns the directory it landed in.
      Used by the CLI AND by the pre-destructive auto-hook.

  list_snapshots(dest_root=None) -> list[dict]
      Sorted newest-first; each dict has dir, kind ('manual' /
      'pre-destructive'), timestamp, total_bytes, reason.

  rotate(keep=30, dest_root=None) -> list[Path]
      Delete manual snapshots beyond the most-recent N. Returns
      the list of removed dirs. Pre-destructive snapshots are
      never rotated by this function.

  main() — `stock-backup` CLI entry point.
"""

import argparse
import datetime
import json
import shutil
import sqlite3
import sys
from pathlib import Path

from stock_toolkit.common import (
    DATA_DIR,
    LIVE_DB,
    PORTFOLIO_DB,
    load_config,
    CONFIG_PATH,
)


__all__ = [
    "snapshot",
    "list_snapshots",
    "rotate",
    "DEFAULT_DB_PATHS",
    "BACKUPS_DIR",
    "DEFAULT_KEEP",
]


BACKUPS_DIR  = DATA_DIR / "backups"
DEFAULT_KEEP = 30

# The set of files a "full" snapshot includes. Order matters only for
# the manifest's deterministic layout.
DEFAULT_DB_PATHS = (
    PORTFOLIO_DB,
    LIVE_DB,
    DATA_DIR / "stock_failures.db",
)
_JSON_STATE = (
    DATA_DIR / ".collector_state.json",
    DATA_DIR / ".alerts_state.json",
)


def _now_slug() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%d-%H%M")


def _vacuum_into(src: Path, dst: Path) -> None:
    """Consistent snapshot of a SQLite file via ``VACUUM INTO``. Safe
    against concurrent readers and writers."""
    con = sqlite3.connect(src)
    try:
        # ``VACUUM INTO ?`` requires SQLite ≥ 3.27 (2019). Every Python
        # 3.10+ sqlite3 ships with that or newer.
        con.execute("VACUUM INTO ?", (str(dst),))
    finally:
        con.close()


def snapshot(reason: str = None,
             db_paths: tuple = None,
             json_paths: tuple = None,
             dest_root: Path = None,
             subdir: str = None) -> Path:
    """Create a snapshot and return the directory it landed in.

    ``reason`` is recorded in the manifest (e.g. "manual",
    "pre-delete-portfolio-2"). ``db_paths`` defaults to
    ``DEFAULT_DB_PATHS``; pass a narrower tuple to snapshot only
    one DB (the auto-hook does this). ``subdir`` overrides the
    "<timestamp>" name (used to nest pre-destructive snapshots
    under ``pre-destructive/``).
    """
    db_paths   = db_paths   if db_paths   is not None else DEFAULT_DB_PATHS
    json_paths = json_paths if json_paths is not None else _JSON_STATE
    dest_root  = dest_root  or BACKUPS_DIR
    dest_root.mkdir(parents=True, exist_ok=True)

    name = subdir or _now_slug()
    dest = dest_root / name
    # If a same-named dir already exists (e.g. two pre-destructive ops
    # in the same minute), append -N to avoid clobber.
    if dest.exists():
        i = 2
        while (dest_root / f"{name}-{i}").exists():
            i += 1
        dest = dest_root / f"{name}-{i}"
    dest.mkdir(parents=True)

    entries = []
    for src in db_paths:
        if not src.exists():
            continue
        dst = dest / src.name
        _vacuum_into(src, dst)
        entries.append({
            "name":    src.name,
            "source":  str(src),
            "method":  "VACUUM INTO",
            "bytes":   dst.stat().st_size,
        })
    for src in json_paths:
        if not src.exists():
            continue
        dst = dest / src.name
        shutil.copy2(src, dst)
        entries.append({
            "name":   src.name,
            "source": str(src),
            "method": "copy",
            "bytes":  dst.stat().st_size,
        })

    manifest = {
        "created_at":  datetime.datetime.now(
            datetime.timezone.utc).isoformat(timespec="seconds"),
        "reason":      reason or "manual",
        "total_bytes": sum(e["bytes"] for e in entries),
        "entries":     entries,
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n")
    return dest


def _is_pre_destructive(dirpath: Path) -> bool:
    """A snapshot is pre-destructive iff it lives under ``pre-destructive/``."""
    return dirpath.parent.name == "pre-destructive"


def list_snapshots(dest_root: Path = None) -> list[dict]:
    """Enumerate snapshots; sorted newest first."""
    dest_root = dest_root or BACKUPS_DIR
    if not dest_root.exists():
        return []
    out = []
    for d in dest_root.rglob("manifest.json"):
        dirpath = d.parent
        try:
            m = json.loads(d.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        out.append({
            "dir":         dirpath,
            "kind":        "pre-destructive" if _is_pre_destructive(dirpath)
                           else "manual",
            "created_at":  m.get("created_at"),
            "reason":      m.get("reason"),
            "total_bytes": m.get("total_bytes", 0),
            "entries":     m.get("entries", []),
        })
    # Manifest timestamps sort lexicographically (ISO-8601). Newest first.
    out.sort(key=lambda x: x["created_at"] or "", reverse=True)
    return out


def rotate(keep: int = DEFAULT_KEEP,
           dest_root: Path = None) -> list[Path]:
    """Delete *manual* snapshots beyond the most-recent ``keep``.

    Pre-destructive snapshots are never rotated by this function —
    they're the safety net. The user can drop them by hand if needed.
    """
    if keep < 0:
        raise ValueError(f"keep must be ≥ 0, got {keep}")
    manual = [s for s in list_snapshots(dest_root) if s["kind"] == "manual"]
    deletable = manual[keep:]
    removed = []
    for s in deletable:
        try:
            shutil.rmtree(s["dir"])
            removed.append(s["dir"])
        except OSError as e:
            print(f"  ! could not remove {s['dir']}: {e}", file=sys.stderr)
    return removed


# ─────────────────────────────────────────────────────────────────────────────
#  Pre-destructive auto-snapshot hook
# ─────────────────────────────────────────────────────────────────────────────

def auto_snapshot_enabled() -> bool:
    """Check config for opt-out. Default: enabled."""
    cfg = load_config(CONFIG_PATH)
    val = (cfg.get("AUTO_BACKUP_BEFORE_DESTRUCTIVE") or "").strip().lower()
    if val in ("0", "false", "no", "off"):
        return False
    return True


def pre_destructive_snapshot(op_name: str, target_id: int = None,
                             db_paths: tuple = None,
                             backups_root: Path = None) -> Path | None:
    """Snapshot the DB just before a destructive op. Returns the
    snapshot dir, or None if auto-backup is disabled.

    The snapshot lands under ``<backups_root>/pre-destructive/`` so
    it's never touched by ``rotate()`` — destructive history outranks
    disk pressure as a concern. ``backups_root`` defaults to the live
    ``BACKUPS_DIR``; callers using a custom DB path (tests, or a
    non-default ``DATA_DIR``) pass their own to keep snapshots local
    to the DB they came from.
    """
    if not auto_snapshot_enabled():
        return None
    db_paths     = db_paths     if db_paths     is not None else (PORTFOLIO_DB,)
    backups_root = backups_root if backups_root is not None else BACKUPS_DIR
    reason = (f"pre-{op_name}"
              + (f"-portfolio-{target_id}" if target_id is not None else ""))
    return snapshot(
        reason=reason,
        db_paths=db_paths,
        json_paths=(),                              # JSON state is irrelevant for game ops
        dest_root=backups_root / "pre-destructive",
        subdir=f"{_now_slug()}-{reason}",
    )


# ─────────────────────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def main():
    parser = argparse.ArgumentParser(
        description="Snapshot the live state directory with VACUUM INTO + rotation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  stock-backup                         # snapshot now, then rotate to keep 30\n"
            "  stock-backup --keep 7                # keep the latest 7 manual snapshots\n"
            "  stock-backup --list                  # list existing snapshots\n"
            "  stock-backup --dry-run               # show what WOULD be done\n"
            "\nPre-destructive snapshots (auto-taken before strategy delete / reset)\n"
            "live under data/backups/pre-destructive/ and are NEVER rotated.\n"
            "Disable with AUTO_BACKUP_BEFORE_DESTRUCTIVE=false in config.env.\n"
        ),
    )
    parser.add_argument("--list", action="store_true",
                        help="List existing snapshots and exit.")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP,
                        metavar="N",
                        help=f"Keep the latest N manual snapshots after this one "
                             f"(default: {DEFAULT_KEEP}).")
    parser.add_argument("--reason", default="manual",
                        help="Free-form tag for the manifest (default: 'manual').")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without writing files.")
    args = parser.parse_args()

    if args.list:
        snaps = list_snapshots()
        if not snaps:
            print(f"No snapshots in {BACKUPS_DIR}.")
            return
        print(f"{len(snaps)} snapshot(s) in {BACKUPS_DIR}:")
        for s in snaps:
            print(f"  [{s['kind']:<16}] {s['created_at']}  "
                  f"{_fmt_bytes(s['total_bytes']):>8}  "
                  f"{s['reason'] or '-'}")
            print(f"      → {s['dir']}")
        return

    if args.dry_run:
        print(f"[dry-run] Would snapshot to {BACKUPS_DIR / _now_slug()} "
              f"(reason={args.reason!r}).")
        print(f"[dry-run] Files (each via VACUUM INTO or copy):")
        for p in (*DEFAULT_DB_PATHS, *_JSON_STATE):
            mark = "exists" if p.exists() else "skipped (missing)"
            print(f"  - {p.name:<25} [{mark}]")
        manual = [s for s in list_snapshots() if s["kind"] == "manual"]
        if len(manual) >= args.keep:
            to_rm = manual[args.keep:]
            print(f"[dry-run] After rotation, would remove "
                  f"{len(to_rm)} older manual snapshot(s):")
            for s in to_rm:
                print(f"  - {s['dir']}")
        else:
            print(f"[dry-run] Nothing to rotate (manual count {len(manual)} "
                  f"≤ keep {args.keep}).")
        return

    dest = snapshot(reason=args.reason)
    total = sum(e["bytes"] for e in
                json.loads((dest / "manifest.json").read_text())["entries"])
    print(f"Snapshot → {dest}  ({_fmt_bytes(total)})")

    removed = rotate(keep=args.keep)
    if removed:
        print(f"Rotated: removed {len(removed)} older snapshot(s).")
    else:
        print(f"Rotation: nothing to remove (kept the latest {args.keep}).")


if __name__ == "__main__":
    main()
