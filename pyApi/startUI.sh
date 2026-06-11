#!/usr/bin/env bash
# startUI.sh — launch the Stock Toolkit dashboard
#
# Usage:
#   ./startUI.sh           # opens at http://localhost:8501
#   ./startUI.sh --port 8080

set -euo pipefail

STOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── find streamlit ────────────────────────────────────────────────────────────
# Priority: .venv inside toolkit → active venv → conda env → system PATH
if [[ -x "$STOCK_DIR/.venv/bin/streamlit" ]]; then
    STREAMLIT="$STOCK_DIR/.venv/bin/streamlit"
elif [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/streamlit" ]]; then
    STREAMLIT="$VIRTUAL_ENV/bin/streamlit"
elif [[ -n "${CONDA_PREFIX:-}" && -x "$CONDA_PREFIX/bin/streamlit" ]]; then
    STREAMLIT="$CONDA_PREFIX/bin/streamlit"
elif command -v streamlit &>/dev/null; then
    STREAMLIT="$(command -v streamlit)"
else
    echo "startUI.sh: streamlit not found." >&2
    echo "  Tried:" >&2
    echo "    $STOCK_DIR/.venv/bin/streamlit  (toolkit venv)" >&2
    echo "    \$VIRTUAL_ENV/bin/streamlit      (active venv)" >&2
    echo "    \$CONDA_PREFIX/bin/streamlit     (conda env)" >&2
    echo "    streamlit on \$PATH" >&2
    echo "" >&2
    echo "  Run install.sh first, or install streamlit in your active environment:" >&2
    echo "    pip install streamlit" >&2
    exit 1
fi

if [[ ! -f "$STOCK_DIR/stock_toolkit/ui/app.py" ]]; then
    echo "startUI.sh: stock_toolkit/ui/app.py not found in $STOCK_DIR" >&2
    exit 1
fi

# ── launch ────────────────────────────────────────────────────────────────────
cd "$STOCK_DIR"
exec "$STREAMLIT" run stock_toolkit/ui/app.py \
    --server.address localhost \
    "$@"
