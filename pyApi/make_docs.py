"""
make_docs.py
============
Generates API documentation and module relationship diagrams for the
Stock Toolkit.  Fully idempotent — same source files produce identical output.

Tools used:
  pdoc      — HTML API docs from docstrings (pip install pdoc)
  pyreverse — Module/package relationship diagrams (pip install pylint)

Output layout:
  docs/
    index.html              ← pdoc entry point (links to all modules)
    stock_collector.html    ← per-module API pages
    stock_analysis.html
    ...
    diagrams/
      packages.png          ← module dependency graph
      classes.png           ← class/function relationship diagram

Run:
    python3 make_docs.py                 # generate into docs/
    python3 make_docs.py --out ./public/docs
    python3 make_docs.py --no-diagrams   # skip pyreverse (faster)
    python3 make_docs.py --dry-run       # show what would run
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent

# Modules to document — in logical order
MODULES = [
    "stock_collector",
    "stock_analysis",
    "stock_inventory",
    "stock_score",
    "stock_backtest",
    "stock_alerts",
    "stock_ui",
    "stock_setup",
]

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

def check_tool(name: str) -> bool:
    """Return True if a command-line tool is available."""
    return shutil.which(name) is not None


def run(cmd: list[str], dry_run: bool, cwd: Path = SCRIPT_DIR) -> bool:
    """Print and optionally run a command. Returns True on success."""
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    if dry_run:
        return True
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ✗ failed (exit {result.returncode})")
        if result.stderr:
            for line in result.stderr.strip().splitlines():
                print(f"    {line}")
        return False
    return True


# ─────────────────────────────────────────────
#  DOCUMENTATION — pdoc
# ─────────────────────────────────────────────

def generate_docs(out_dir: Path, dry_run: bool) -> bool:
    """
    Run pdoc to generate HTML API documentation.

    pdoc reads Python source files and their docstrings, and produces
    clean, searchable HTML.  Output is fully idempotent.

    pdoc v14+ syntax:
        pdoc module1 module2 ... -o output_dir/
    """
    print("\n── API Documentation (pdoc) ─────────────────────────────────")

    if not check_tool("pdoc"):
        print("  ✗ pdoc not found. Install with: pip install pdoc")
        print("    Skipping API documentation.")
        return False

    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    cmd = ["pdoc", "--output-dir", str(out_dir)] + MODULES
    success = run(cmd, dry_run, cwd=SCRIPT_DIR)

    if success and not dry_run:
        # pdoc creates <module>.html files; find the entry point
        index = out_dir / "index.html"
        first_module = out_dir / f"{MODULES[0]}.html"
        if index.exists():
            print(f"  ✓ docs written to {out_dir}/")
            print(f"    Open: {index}")
        elif first_module.exists():
            print(f"  ✓ docs written to {out_dir}/")
            print(f"    Open: {first_module}")
    return success


# ─────────────────────────────────────────────
#  DIAGRAMS — pyreverse
# ─────────────────────────────────────────────

def generate_diagrams(out_dir: Path, dry_run: bool) -> bool:
    """
    Run pyreverse to generate module dependency and class relationship diagrams.

    pyreverse is bundled with pylint and produces .dot files which it
    converts to .png using graphviz (dot must be on PATH).

    Output:
      packages_StockToolkit.png  — import/dependency graph between modules
      classes_StockToolkit.png   — class and function relationship graph
    """
    print("\n── Relationship Diagrams (pyreverse) ────────────────────────")

    if not check_tool("pyreverse"):
        print("  ✗ pyreverse not found. Install with: pip install pylint")
        print("    Skipping diagrams.")
        return False

    if not check_tool("dot"):
        print("  ✗ graphviz 'dot' not found.")
        print("    macOS:  brew install graphviz")
        print("    Ubuntu: sudo apt install graphviz")
        print("    Skipping diagrams.")
        return False

    diag_dir = out_dir / "diagrams"
    if not dry_run:
        diag_dir.mkdir(parents=True, exist_ok=True)

    # pyreverse generates packages_<name>.png and classes_<name>.png
    # -o png        — output format
    # -p name       — project name (used in output filenames)
    # -d dir        — output directory
    # --no-stdlib   — exclude standard library modules from graph
    source_files = [str(SCRIPT_DIR / f"{m}.py") for m in MODULES]
    cmd = [
        "pyreverse",
        "-o", "png",
        "-p", "StockToolkit",
        "-d", str(diag_dir),
        "--no-stdlib",
        "--ignore=test_toolkit.py,test_live_apis.py",
    ] + source_files

    success = run(cmd, dry_run, cwd=SCRIPT_DIR)

    if success and not dry_run:
        generated = list(diag_dir.glob("*.png"))
        if generated:
            print(f"  ✓ diagrams written to {diag_dir}/")
            for f in sorted(generated):
                print(f"    {f.name}")
        else:
            print(f"  ✗ pyreverse ran but no PNG files found in {diag_dir}/")
    return success


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate API docs and diagrams for the Stock Toolkit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python3 make_docs.py                  # generate into docs/
  python3 make_docs.py --out ./pub/docs # custom output directory
  python3 make_docs.py --no-diagrams    # docs only, skip pyreverse
  python3 make_docs.py --dry-run        # show commands without running
        """
    )
    parser.add_argument("--out", default="docs", metavar="DIR",
                        help="Output directory (default: docs/)")
    parser.add_argument("--no-diagrams", action="store_true",
                        help="Skip pyreverse diagram generation")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print commands without executing them")
    args = parser.parse_args()

    out_dir  = SCRIPT_DIR / args.out
    dry_run  = args.dry_run

    print(f"\nStock Toolkit — Documentation Generator")
    print(f"Output directory: {out_dir}")
    if dry_run:
        print("(dry run — no files will be written)")

    docs_ok  = generate_docs(out_dir, dry_run)
    diag_ok  = True
    if not args.no_diagrams:
        diag_ok = generate_diagrams(out_dir, dry_run)

    print(f"\n{'─'*60}")
    if docs_ok and diag_ok:
        print("  ✓ Documentation generation complete")
        if not dry_run:
            # Find a reasonable entry point to suggest
            candidates = [
                out_dir / "index.html",
                out_dir / f"{MODULES[0]}.html",
            ]
            for candidate in candidates:
                if candidate.exists():
                    print(f"  Open: file://{candidate.resolve()}")
                    break
    else:
        missing = []
        if not docs_ok:
            missing.append("pdoc (pip install pdoc)")
        if not diag_ok:
            missing.append("pylint + graphviz (pip install pylint && brew install graphviz)")
        print(f"  ⚠  Partial output — install missing tools:")
        for m in missing:
            print(f"       {m}")
    print(f"{'─'*60}\n")


if __name__ == "__main__":
    main()
