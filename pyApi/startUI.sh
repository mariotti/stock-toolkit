#!/usr/bin/env bash
# startUI.sh — launch the Stock Toolkit dashboard
#
# Usage:
#   ./startUI.sh           # opens at http://localhost:8501
#   ./startUI.sh --port 8080

set -euo pipefail

STOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── find streamlit ────────────────────────────────────────────────────────────
# Prefer the venv inside the toolkit directory
if [[ -x "$STOCK_DIR/.venv/bin/streamlit" ]]; then
    STREAMLIT="$STOCK_DIR/.venv/bin/streamlit"
elif [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/streamlit" ]]; then
    STREAMLIT="$VIRTUAL_ENV/bin/streamlit"
elif command -v streamlit &>/dev/null; then
    STREAMLIT="streamlit"
else
    echo "startUI.sh: streamlit not found." >&2
    echo "  Expected at: $STOCK_DIR/.venv/bin/streamlit" >&2
    echo "  Run install.sh first, or activate your virtual environment." >&2
    exit 1
fi

if [[ ! -f "$STOCK_DIR/stock_ui.py" ]]; then
    echo "startUI.sh: stock_ui.py not found in $STOCK_DIR" >&2
    exit 1
fi

# ── launch ────────────────────────────────────────────────────────────────────
cd "$STOCK_DIR"
exec "$STREAMLIT" run stock_ui.py \
    --server.address localhost \
    "$@"
