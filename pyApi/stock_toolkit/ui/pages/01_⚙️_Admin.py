"""Streamlit page shim — appears as ⚙️ Admin in the sidebar nav.

Streamlit auto-discovers files under a `pages/` directory next to the
entry script (`app.py`). The filename's leading digit controls order
and the emoji becomes the sidebar icon, but that naming convention
makes the file un-importable as a Python module — so all the real
logic lives in stock_toolkit.ui.admin (testable as a normal import).
"""

import sys
from pathlib import Path

# When Streamlit launches `streamlit run app.py`, it doesn't put the
# parent of stock_toolkit/ on sys.path. The app.py shim handles that,
# but page files are loaded by Streamlit through a separate path that
# bypasses app.py's setup — repeat it here so the import below works.
sys.path.insert(0, str(Path(__file__).parents[3]))

from stock_toolkit.ui.admin import render  # noqa: E402

render()
