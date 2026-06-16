"""Streamlit page shim — appears as ❓ Help in the sidebar nav.

Real logic lives in stock_toolkit.ui.help (importable, testable);
the emoji/digit filename can't be imported as a module.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3]))

from stock_toolkit.ui.help import render  # noqa: E402

render()
