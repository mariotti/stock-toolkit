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

if [[ ! -f "$STOCK_DIR/pyproject.toml" ]]; then
    error "pyproject.toml not found in $STOCK_DIR"
    exit 1
fi

info "Installing stock-toolkit (and dependencies) into the venv ..."
"$VENV_PIP" install --quiet "$STOCK_DIR"
success "stock-toolkit installed (stock-collect, stock-score, ... on the venv PATH)"

# ── 4. Shell wrappers ─────────────────────────────────────────────────────────
header "Installing shell wrappers"

WRAPPERS=(collect analyse inventory score backtest alerts)
WRAPPERS_SRC="$STOCK_DIR/bin"   # wrappers shipped in bin/ subdir of the dist
WRAPPERS_DIR="$STOCK_DIR/bin"   # they stay there — no copy needed

if [[ ! -d "$WRAPPERS_SRC" ]]; then
    warn "bin/ directory not found in $STOCK_DIR — wrapper installation skipped."
else
    for wrapper in "${WRAPPERS[@]}"; do
        src="$WRAPPERS_SRC/$wrapper"
        if [[ ! -f "$src" ]]; then
            warn "Wrapper '$wrapper' not found — skipping."
            continue
        fi
        # Patch STOCK_DIR default inside the wrapper in-place
        sed -i.bak "s|STOCK_DIR=\"\${STOCK_DIR:-\$HOME/stock}\"|STOCK_DIR=\"\${STOCK_DIR:-$STOCK_DIR}\"|g" \
            "$src" && rm -f "${src}.bak"
        chmod +x "$src"
        success "bin/$wrapper"
    done
fi

# PATH guidance
echo ""
echo -e "  Wrappers installed to: ${CYAN}$WRAPPERS_DIR${RESET}"
echo ""
echo "  To use them from anywhere, choose one option:"
echo ""
echo "  Option A — add to PATH (add this to ~/.zshrc or ~/.bashrc):"
echo -e "    ${BOLD}export PATH=\"$WRAPPERS_DIR:\$PATH\"${RESET}"
echo ""
echo "  Option B — symlink each command to ~/bin:"
echo -e "    ${BOLD}mkdir -p ~/bin"
for wrapper in "${WRAPPERS[@]}"; do
    echo -e "    ln -sf $WRAPPERS_DIR/$wrapper ~/bin/$wrapper"
done
echo -e "    ${RESET}"
echo "  Option C — use with full path:"
echo -e "    ${BOLD}$WRAPPERS_DIR/collect${RESET}"
echo ""
read -rp "  Add $WRAPPERS_DIR to PATH automatically in your shell profile? [y/N] " answer
answer="${answer:-N}"
if [[ "$answer" =~ ^[Yy]$ ]]; then
    SHELL_PROFILE=""
    if [[ -f "$HOME/.zshrc" ]]; then
        SHELL_PROFILE="$HOME/.zshrc"
    elif [[ -f "$HOME/.bashrc" ]]; then
        SHELL_PROFILE="$HOME/.bashrc"
    elif [[ -f "$HOME/.bash_profile" ]]; then
        SHELL_PROFILE="$HOME/.bash_profile"
    fi
    if [[ -n "$SHELL_PROFILE" ]]; then
        echo "" >> "$SHELL_PROFILE"
        echo "# Stock Toolkit" >> "$SHELL_PROFILE"
        echo "export PATH=\"$WRAPPERS_DIR:\$PATH\"" >> "$SHELL_PROFILE"
        success "Added to $SHELL_PROFILE — restart your terminal or run: source $SHELL_PROFILE"
    else
        warn "Could not detect shell profile. Add the export line manually."
    fi
fi

# ── 5. startUI.sh ────────────────────────────────────────────────────────────
header "UI launcher"

START_UI="$STOCK_DIR/startUI.sh"
if [[ -f "$START_UI" ]]; then
    chmod +x "$START_UI"
    success "startUI.sh ready"
else
    warn "startUI.sh not found — UI must be launched manually."
fi
header "7. Configuration"

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
    (cd "$STOCK_DIR" && "$VENV_PYTHON" -m stock_toolkit.setup_wizard)
fi

# ── 6. Initial data collection ────────────────────────────────────────────────
header "Initial database"

DB="$STOCK_DIR/stock_data.db"
if [[ -f "$DB" ]]; then
    warn "Database already exists — skipping initial collection."
else
    echo ""
    echo "  No database found. You can seed it now with a full historical"
    echo "  download via yfinance (no API key needed, takes 1-2 minutes)."
    echo "  This gives the UI a proper dataset to work with from day one."
    echo ""
    read -rp "  Download full historical data now? [Y/n] " answer
    answer="${answer:-Y}"
    if [[ "$answer" =~ ^[Yy]$ ]]; then
        info "Running historical collection via yfinance (this may take a minute) ..."
        (cd "$STOCK_DIR" && "$VENV_PYTHON" -m stock_toolkit.collector \
            --sources yfinance --historical ALL)
        success "Historical data collected"
    else
        info "Skipped. Run 'bin/collect --sources yfinance --historical ALL' whenever you are ready."
    fi
fi

# ── done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════╗"
echo -e "║   Stock Toolkit installed successfully!  ║"
echo -e "╚══════════════════════════════════════════╝${RESET}"
echo ""
echo -e "  ${BOLD}▶  Start the dashboard:${RESET}"
echo -e "     ${CYAN}$STOCK_DIR/startUI.sh${RESET}"
echo ""
echo "  Then open your browser at: http://localhost:8501"
echo ""
echo "  Other commands (from $WRAPPERS_DIR):"
echo "    collect              — fetch latest data"
echo "    inventory --summary  — what's in the database"
echo "    inventory --check    — gap detection"
echo "    score                — rank your watchlist"
echo ""
echo "  See START_HERE.md for a quick-start guide."
echo ""
