#!/usr/bin/env bash
# install.sh — Stock Toolkit installer
#
# Usage:
#   cd stock-toolkit/
#   bash install.sh
#
# What this does:
#   1. Checks Python 3.10+
#   2. Creates a virtual environment (.venv/)
#   3. Installs Python dependencies
#   4. Copies shell wrappers to ~/bin and patches STOCK_DIR inside them
#   5. Runs stock_setup.py for interactive configuration (API keys, symbols)
#   6. Optionally seeds the database with a first yfinance collection

set -euo pipefail

# ── colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}▸${RESET} $*"; }
success() { echo -e "${GREEN}✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}⚠${RESET}  $*"; }
error()   { echo -e "${RED}✗${RESET} $*" >&2; }
header()  { echo -e "\n${BOLD}$*${RESET}"; }

# ── locate install dir ────────────────────────────────────────────────────────
STOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
info "Installing from: $STOCK_DIR"

# ── 1. Python version check ───────────────────────────────────────────────────
header "Checking Python"

PYTHON=""
for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" &>/dev/null; then
        version=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        major=${version%%.*}; minor=${version##*.}
        if [[ "$major" -eq 3 && "$minor" -ge 10 ]]; then
            PYTHON="$candidate"
            success "Found $candidate ($version)"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    error "Python 3.10 or higher is required but not found."
    echo "  Install it from https://python.org or via your package manager."
    echo "  macOS:  brew install python@3.12"
    echo "  Ubuntu: sudo apt install python3.12"
    exit 1
fi

# ── 2. Virtual environment ────────────────────────────────────────────────────
header "Setting up virtual environment"

VENV_DIR="$STOCK_DIR/.venv"
if [[ -d "$VENV_DIR" ]]; then
    warn "Virtual environment already exists — skipping creation."
else
    info "Creating .venv ..."
    "$PYTHON" -m venv "$VENV_DIR"
    success "Virtual environment created"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

# ── 3. Install dependencies ───────────────────────────────────────────────────
header "Installing dependencies"

info "Upgrading pip ..."
"$VENV_PIP" install --quiet --upgrade pip

REQUIREMENTS="$STOCK_DIR/requirements.txt"
if [[ ! -f "$REQUIREMENTS" ]]; then
    error "requirements.txt not found in $STOCK_DIR"
    exit 1
fi

info "Installing packages from requirements.txt ..."
"$VENV_PIP" install --quiet -r "$REQUIREMENTS"
success "Dependencies installed"

# ── 4. Shell wrappers ─────────────────────────────────────────────────────────
header "Installing shell wrappers"

BIN_DIR="$HOME/bin"
mkdir -p "$BIN_DIR"

WRAPPERS=(collect analyse inventory score backtest alerts)
for wrapper in "${WRAPPERS[@]}"; do
    src="$STOCK_DIR/$wrapper"
    dst="$BIN_DIR/$wrapper"
    if [[ ! -f "$src" ]]; then
        warn "Wrapper '$wrapper' not found in $STOCK_DIR — skipping."
        continue
    fi
    # Copy and patch STOCK_DIR default inside the wrapper
    sed "s|STOCK_DIR=\"\${STOCK_DIR:-\$HOME/stock}\"|STOCK_DIR=\"\${STOCK_DIR:-$STOCK_DIR}\"|g" \
        "$src" > "$dst"
    chmod +x "$dst"
    success "~/bin/$wrapper"
done

# Check ~/bin is on PATH
if ! echo "$PATH" | tr ':' '\n' | grep -q "^$BIN_DIR$"; then
    warn "~/bin is not on your PATH."
    echo "  Add this to your shell profile (~/.zshrc or ~/.bashrc):"
    echo ""
    echo "    export PATH=\"\$HOME/bin:\$PATH\""
    echo ""
    echo "  Then reload: source ~/.zshrc"
fi

# ── 5. Configuration ──────────────────────────────────────────────────────────
header "Configuration"

CONFIG="$STOCK_DIR/config.env"
TEMPLATE="$STOCK_DIR/config.env.template"

if [[ -f "$CONFIG" ]]; then
    warn "config.env already exists — skipping setup wizard."
    info "Edit $CONFIG manually if you want to change settings."
else
    if [[ -f "$TEMPLATE" ]]; then
        info "Copying config.env.template → config.env ..."
        cp "$TEMPLATE" "$CONFIG"
    fi
    info "Launching configuration wizard ..."
    echo ""
    "$VENV_PYTHON" "$STOCK_DIR/stock_setup.py"
fi

# ── 6. Initial data collection ────────────────────────────────────────────────
header "Initial database"

DB="$STOCK_DIR/stock_data.db"
if [[ -f "$DB" ]]; then
    warn "Database already exists — skipping initial collection."
else
    echo ""
    echo "  No database found. You can seed it now with a quick yfinance"
    echo "  collection (no API key needed, takes ~30 seconds)."
    echo ""
    read -rp "  Download initial data now? [Y/n] " answer
    answer="${answer:-Y}"
    if [[ "$answer" =~ ^[Yy]$ ]]; then
        info "Running initial collection (yfinance only) ..."
        "$VENV_PYTHON" "$STOCK_DIR/stock_collector.py" --sources yfinance
        success "Initial data collected"
    else
        info "Skipped. Run 'collect' whenever you are ready."
    fi
fi

# ── done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}Installation complete!${RESET}"
echo ""
echo "  Start the UI:     cd $STOCK_DIR && .venv/bin/streamlit run stock_ui.py"
echo "  Collect data:     collect"
echo "  View inventory:   inventory --summary"
echo "  Check gaps:       inventory --check"
echo "  Run scoring:      score"
echo ""
echo "  See START_HERE.md for a quick-start guide."
echo ""
