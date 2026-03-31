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
  - Generates requirements.txt

Run:
    python3 make_dist.py
    python3 make_dist.py --out ./public      # custom output directory
    python3 make_dist.py --license apache    # MIT (default), apache, or gpl3
    python3 make_dist.py --dry-run           # show what would be created
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

# Files to include verbatim (after path scrubbing)
SOURCE_FILES = [
    "stock_collector.py",
    "stock_analysis.py",
    "stock_inventory.py",
    "stock_score.py",
    "stock_backtest.py",
    "stock_alerts.py",
    "stock_ui.py",
    "stock_setup.py",
    "test_toolkit.py",
    "test_live_apis.py",
    "crontab.demo",
    "make_dist.py",
    "install.sh",
]

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
    "ANALYSIS.md",
    "README_SCORE.md",
    "README_BACKTEST.md",
    "README_ALERTS.md",
    "START_HERE.md",
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
# All paths default to the directory containing stock_collector.py.
# Uncomment and set OUTPUT_DIR to store data elsewhere.

# OUTPUT_DIR=/data/stocks
"""

REQUIREMENTS = """\
# Stock Toolkit — Python dependencies
# Install with: pip install -r requirements.txt

requests>=2.31.0
yfinance>=0.2.40
pandas>=2.0.0
numpy>=1.24.0
scipy>=1.11.0
matplotlib>=3.7.0

# Streamlit UI (optional — only needed for stock_ui.py)
# streamlit>=1.30.0
# plotly>=5.18.0
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
    for name in SOURCE_FILES:
        src = SCRIPT_DIR / name
        dst = out_dir / name
        if not src.exists():
            print(f"  ⚠  MISSING: {name}")
            missing.append(name)
            continue
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

    # requirements.txt
    dst = out_dir / "requirements.txt"
    if not dry_run:
        dst.write_text(REQUIREMENTS)
    print_file("requirements.txt", dst, dry_run)

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
    n_files = len(SOURCE_FILES) + len(DOC_FILES) + 4  # 4 generated
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
        print("  Next steps:")
        print(f"    cd {out_dir}")
        print(f"    git init")
        print(f"    git add .")
        print(f"    git commit -m 'Initial release'")
        print(f"    git remote add origin <your-repo-url>")
        print(f"    git push -u origin main")
    print(f"{'─'*60}\n")


if __name__ == "__main__":
    main()
