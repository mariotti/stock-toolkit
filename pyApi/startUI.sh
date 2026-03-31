#!/usr/bin/env bash
# startUI.sh — launch the Stock Toolkit dashboard
#
# Usage:
#   ./startUI.sh           # opens at http://localhost:8501
#   ./startUI.sh --port 8080

set -euo pipefail

STOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── find Python ───────────────────────────────────────────────────────────────
if [[ -x "$STOCK_DIR/.venv/bin/python" ]]; then
    STREAMLIT="$STOCK_DIR/.venv/bin/streamlit"
elif [[ -n "${VIRTUAL_ENV:-}" ]]; then
    STREAMLIT="$VIRTUAL_ENV/bin/streamlit"
else
    STREAMLIT="${STREAMLIT:-streamlit}"
fi

if [[ ! -f "$STOCK_DIR/stock_ui.py" ]]; then
    echo "startUI.sh: stock_ui.py not found in $STOCK_DIR" >&2
    exit 1
fi

if ! command -v "$STREAMLIT" &>/dev/null && [[ ! -x "$STREAMLIT" ]]; then
    echo "startUI.sh: streamlit not found. Run install.sh first." >&2
    exit 1
fi

# ── launch ────────────────────────────────────────────────────────────────────
cd "$STOCK_DIR"
echo "Starting Stock Toolkit UI → http://localhost:8501"
exec "$STREAMLIT" run stock_ui.py \
    --server.headless true \
    --server.address 0.0.0.0 \
    "$@"
