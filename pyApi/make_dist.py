"""
make_dist.py
============
Creates a clean distribution directory suitable for a public repository.

What it does:
  - Copies all source files to dist/
  - Strips personal paths and usernames
  - Creates config.env.template (keys blanked, structure preserved)
  - Generates .gitignore
  - Generates LICENSE (MIT by default)
  - Ships pyproject.toml (install with: pip install .)

Run:
    python3 make_dist.py
    python3 make_dist.py --out ./public      # custom output directory
    python3 make_dist.py --license apache    # MIT (default), apache, or gpl3
    python3 make_dist.py --dry-run           # show what would be created
    python3 make_dist.py --package toolkit   # also create stock-toolkit.tar.gz + .zip
"""

import argparse
import shutil
import sys
from datetime import date
from pathlib import Path

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent

# Files to include verbatim (after path scrubbing).
# The stock_toolkit/ package and tests/ trees are expanded at runtime.
SOURCE_FILES = [
    "pyproject.toml",
    "crontab.demo",
    "make_dist.py",
    "make_docs.py",
    "install.sh",
    "startUI.sh",
    "VERSION",
]


def _tree_files() -> list[str]:
    """All package + test sources, as paths relative to SCRIPT_DIR."""
    out = []
    for root in ("stock_toolkit", "tests"):
        for p in sorted((SCRIPT_DIR / root).rglob("*.py")):
            if "__pycache__" not in p.parts:
                out.append(str(p.relative_to(SCRIPT_DIR)))
    return out

# Shell wrappers — copied to bin/ subdirectory in the dist
WRAPPER_FILES = [
    "collect",
    "analyse",
    "inventory",
    "score",
    "backtest",
    "alerts",
]

DOC_FILES = [
    "README.md",
    "QUICKSTART.md",
    "QUICKSTART_DEV.md",
    "ANALYSIS.md",
    "README_SCORE.md",
    "README_BACKTEST.md",
    "README_ALERTS.md",
]

# Personal path patterns to replace.
# Format: (pattern_to_find, replacement)
# Sorted longest first to avoid partial matches.
PATH_SCRUBS = [
    ("/Users/mariotti/GIT/stock_py_api/pyApi", "/path/to/stock"),
    ("/Users/mariotti/GIT/stock_py_api",       "/path/to/stock"),
    ("/Users/mariotti",                         "/home/user"),
    ("mariotti",                                "user"),
]

# ─────────────────────────────────────────────
#  LICENSES
# ─────────────────────────────────────────────

def _mit_license(year: int, author: str) -> str:
    return f"""\
MIT License

Copyright (c) {year} {author}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def _apache_license(year: int, author: str) -> str:
    return f"""\
Apache License
Version 2.0, January 2004
http://www.apache.org/licenses/

Copyright (c) {year} {author}

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""


def _gpl3_license(year: int, author: str) -> str:
    return f"""\
Stock Toolkit
Copyright (C) {year}  {author}

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.

Full license text: https://www.gnu.org/licenses/gpl-3.0.txt
"""


LICENSES = {
    "mit":    _mit_license,
    "apache": _apache_license,
    "gpl3":   _gpl3_license,
}

# ─────────────────────────────────────────────
#  GENERATED FILES
# ─────────────────────────────────────────────

GITIGNORE = """\
# ─── databases ────────────────────────────────
*.db
*.db-shm
*.db-wal

# ─── CSV exports and runtime files ────────────
stock_data.csv
collector.log
cron.log
cron_err.log
alerts.log
alerts_err.log
.collector_state.json
.alerts_state.json

# ─── config — contains API keys ───────────────
config.env

# ─── generated plot output ────────────────────
gnuplot-data/
matplot/
data/

# ─── distribution output ─────────────────────
dist/

# ─── Python ───────────────────────────────────
__pycache__/
*.py[cod]
*$py.class
*.egg-info/
.eggs/
.venv/
venv/
env/
.env

# ─── macOS ────────────────────────────────────
.DS_Store
.AppleDouble
.LSOverride

# ─── editors ──────────────────────────────────
.vscode/
.idea/
*.swp
*.swo
*~
"""

CONFIG_TEMPLATE = """\
# config.env — Stock Toolkit configuration
# ─────────────────────────────────────────────────────────────────────────────
# Copy this file to config.env and fill in your values.
# Lines starting with # are comments. Inline comments are supported.
# Quotes around values are optional.
#
# IMPORTANT: config.env is in .gitignore — never commit it.
#            It contains your API keys.
# ─────────────────────────────────────────────────────────────────────────────


# ── symbols ───────────────────────────────────────────────────────────────────
# Comma-separated list of tickers to track.
# Can be overridden per-run with:  collect -s AAPL

SYMBOLS=AAPL,MSFT,TSLA


# ── API keys ──────────────────────────────────────────────────────────────────
# Leave a key blank ("") or remove the line to skip that source.
# yfinance works without any key.

ALPHAVANTAGE_KEY=               # https://www.alphavantage.co/support/#api-key
FINNHUB_KEY=                    # https://finnhub.io/register
POLYGON_KEY=                    # https://polygon.io/dashboard/signup
FMP_KEY=                        # https://financialmodelingprep.com/developer/docs
TWELVEDATA_KEY=                 # https://twelvedata.com/register
MARKETSTACK_KEY=                # https://marketstack.com/signup/free


# ── paid tier flags ───────────────────────────────────────────────────────────
# FINNHUB_PAID=true       → unlocks /stock/candle (OHLCV bars)
# ALPHAVANTAGE_PAID=true  → unlocks TIME_SERIES_DAILY_ADJUSTED + full history

FINNHUB_PAID=false
ALPHAVANTAGE_PAID=false


# ── alert notifications (optional) ───────────────────────────────────────────

# Email (Gmail: use an App Password, not your main password)
# ALERT_EMAIL=you@example.com
# ALERT_SMTP_HOST=smtp.gmail.com
# ALERT_SMTP_PORT=587
# ALERT_SMTP_USER=you@gmail.com
# ALERT_SMTP_PASS=your_16_char_app_password

# Pushover (https://pushover.net)
# PUSHOVER_USER_KEY=
# PUSHOVER_APP_TOKEN=

# Slack incoming webhook
# SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...


# ── paths (optional) ──────────────────────────────────────────────────────────
# All paths default to $STOCK_DIR (or the working directory).
# Uncomment and set OUTPUT_DIR to store data elsewhere.

# OUTPUT_DIR=/data/stocks
"""

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

def scrub(text: str) -> str:
    """Replace personal paths/usernames with generic placeholders."""
    for pattern, replacement in PATH_SCRUBS:
        text = text.replace(pattern, replacement)
    return text


def copy_scrubbed(src: Path, dst: Path, dry_run: bool = False):
    """Copy a file to dst with personal info stripped."""
    try:
        content = src.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # binary file — copy verbatim
        if not dry_run:
            shutil.copy2(src, dst)
        return

    cleaned = scrub(content)
    if not dry_run:
        dst.write_text(cleaned, encoding="utf-8")
        # preserve executable bit for shell scripts
        if src.suffix == ".sh":
            dst.chmod(dst.stat().st_mode | 0o111)


def print_file(label: str, path: Path, dry_run: bool):
    marker = "[dry-run] " if dry_run else ""
    print(f"  {marker}{label:<28} {path}")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Create a clean public-ready distribution of the Stock Toolkit",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""
examples:
  python3 make_dist.py
  python3 make_dist.py --out ./public --license mit
  python3 make_dist.py --author "Jane Smith" --license apache
  python3 make_dist.py --dry-run
        """
    )
    parser.add_argument("--out", default="dist", metavar="DIR",
                        help="Output directory (default: ./dist)")
    parser.add_argument("--license", default="mit",
                        choices=list(LICENSES),
                        help="License to include: mit (default), apache, gpl3")
    parser.add_argument("--author", default="",
                        metavar="NAME",
                        help='Author name for license header (default: blank)')
    parser.add_argument("--package", metavar="NAME",
                        help="After building, create stock-NAME.tar.gz and stock-NAME.zip "
                             "with the dist dir renamed to NAME inside the archive. "
                             "Example: --package toolkit  → stock-toolkit.tar.gz")
    parser.add_argument("--docs", action="store_true",
                        help="Generate API docs and diagrams into docs/ before packaging "
                             "(requires: pip install pdoc pylint && brew install graphviz)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be created without writing files")
    args = parser.parse_args()

    out_dir  = Path(args.out)
    dry_run  = args.dry_run
    year     = date.today().year
    author   = args.author or "Stock Toolkit Contributors"

    # ── safety check ──────────────────────────────────────────────────────────
    if out_dir.exists() and not dry_run:
        ans = input(f"  '{out_dir}' already exists. Overwrite? [y/N] ").strip().lower()
        if ans != "y":
            print("  Aborted.")
            sys.exit(0)
        shutil.rmtree(out_dir)

    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'─'*60}")
    print(f"  Distribution: {out_dir.resolve()}")
    print(f"  License:      {args.license.upper()}")
    print(f"  Dry run:      {dry_run}")
    print(f"{'─'*60}")

    missing = []

    # ── source files ──────────────────────────────────────────────────────────
    print("\n  Python scripts:")
    for name in SOURCE_FILES + _tree_files():
        src = SCRIPT_DIR / name
        dst = out_dir / name
        if not src.exists():
            print(f"  ⚠  MISSING: {name}")
            missing.append(name)
            continue
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
        copy_scrubbed(src, dst, dry_run)
        print_file(name, dst, dry_run)

    # ── shell wrappers → bin/ subdir ──────────────────────────────────────────
    print("\n  Shell wrappers (bin/):")
    bin_dir = out_dir / "bin"
    if not dry_run:
        bin_dir.mkdir(exist_ok=True)
    for name in WRAPPER_FILES:
        src = SCRIPT_DIR / "bin" / name   # source is bin/ in dev tree
        dst = bin_dir / name
        if not src.exists():
            print(f"  ⚠  MISSING: bin/{name}")
            missing.append(name)
            continue
        copy_scrubbed(src, dst, dry_run)
        if not dry_run:
            dst.chmod(dst.stat().st_mode | 0o111)
        print_file(f"bin/{name}", dst, dry_run)

    # ── documentation ─────────────────────────────────────────────────────────
    print("\n  Documentation:")
    for name in DOC_FILES:
        src = SCRIPT_DIR / name
        dst = out_dir / name
        if not src.exists():
            print(f"  ⚠  MISSING: {name}")
            missing.append(name)
            continue
        copy_scrubbed(src, dst, dry_run)
        print_file(name, dst, dry_run)

    # ── generated files ───────────────────────────────────────────────────────
    print("\n  Generated files:")

    # .gitignore
    dst = out_dir / ".gitignore"
    if not dry_run:
        dst.write_text(GITIGNORE)
    print_file(".gitignore", dst, dry_run)

    # config.env.template
    dst = out_dir / "config.env.template"
    if not dry_run:
        dst.write_text(CONFIG_TEMPLATE)
    print_file("config.env.template", dst, dry_run)

    # LICENSE
    dst = out_dir / "LICENSE"
    license_text = LICENSES[args.license](year, author)
    if not dry_run:
        dst.write_text(license_text)
    print_file("LICENSE", dst, dry_run)

    # ── verify no personal info leaked ────────────────────────────────────────
    if not dry_run:
        print("\n  Checking for personal info leaks...")
        leaked = []
        for f in out_dir.rglob("*"):
            if not f.is_file():
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
                for pattern, _ in PATH_SCRUBS:
                    if pattern in text:
                        leaked.append((f.name, pattern))
            except Exception:
                pass
        if leaked:
            print("  ⚠  PERSONAL INFO FOUND — please review these files:")
            for fname, pat in leaked:
                print(f"     {fname}: '{pat}'")
        else:
            print("  ✓  No personal info detected")

    # ── summary ───────────────────────────────────────────────────────────────
    n_files = (len(SOURCE_FILES) + len(_tree_files()) + len(WRAPPER_FILES)
               + len(DOC_FILES) + 3)  # 3 generated: .gitignore, template, LICENSE
    n_ok    = n_files - len(missing)

    print(f"\n{'─'*60}")
    if dry_run:
        print(f"  Dry run complete — {n_ok} files would be created")
    elif missing:
        print(f"  ⚠  {n_ok}/{n_files} files written  |  {len(missing)} missing")
        print(f"     Missing: {', '.join(missing)}")
    else:
        print(f"  ✓  {n_ok} files written to {out_dir}/")
        print()
        if args.docs and not dry_run:
            print("  Generating documentation...")
            import subprocess as _sp
            docs_out = out_dir / "docs"
            result = _sp.run(
                [sys.executable, str(SCRIPT_DIR / "make_docs.py"),
                 "--out", str(docs_out)],
                cwd=SCRIPT_DIR
            )
            if result.returncode != 0:
                print("  ⚠  Documentation generation had errors (see above)")
            print()
        if args.package and not dry_run:
            _create_packages(out_dir, args.package)
        else:
            print("  Next steps:")
            print(f"    cd {out_dir}")
            print("    git init")
            print("    git add .")
            print("    git commit -m 'Initial release'")
            print("    git remote add origin <your-repo-url>")
            print("    git push -u origin main")
    print(f"{'─'*60}\n")


def _create_packages(out_dir: Path, name: str) -> None:
    """
    Create stock-NAME-VERSION.tar.gz and stock-NAME-VERSION.zip from out_dir,
    with the directory renamed to NAME inside the archive.
    Version is read from VERSION file in the source directory.
    Works on both macOS (BSD tar) and Linux (GNU tar).
    """
    import tarfile
    import zipfile

    # read version — fall back to 'dev' if VERSION file is missing
    version_file = SCRIPT_DIR / "VERSION"
    version = version_file.read_text().strip() if version_file.exists() else "dev"

    parent   = out_dir.parent
    base     = f"stock-{name}-{version}"
    tar_path = parent / f"{base}.tar.gz"
    zip_path = parent / f"{base}.zip"

    print(f"  Version: {version}")
    print(f"  Creating {tar_path.name} ...")
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(out_dir, arcname=name)
    print(f"  ✓  {tar_path}")

    print(f"  Creating {zip_path.name} ...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(out_dir.rglob("*")):
            arcname = name / f.relative_to(out_dir)
            zf.write(f, arcname)
    print(f"  ✓  {zip_path}")

    print()
    print("  Distribute either file — the user unpacks with:")
    print(f"    tar xzf {base}.tar.gz   # creates {name}/ directory")
    print(f"    unzip   {base}.zip       # creates {name}/ directory")


if __name__ == "__main__":
    main()
