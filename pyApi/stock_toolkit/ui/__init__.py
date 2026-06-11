"""
stock_toolkit.ui
================
Streamlit dashboard, split per tab: Score, Analysis, Backtest, Alerts,
Briefing (Claude), Collect.

Launch with the stock-ui entry point, startUI.sh, or:
    streamlit run stock_toolkit/ui/app.py
"""


def main():
    """Console entry point: run the dashboard via streamlit."""
    import subprocess
    import sys
    from pathlib import Path

    app = Path(__file__).parent / "app.py"
    raise SystemExit(subprocess.call(
        [sys.executable, "-m", "streamlit", "run", str(app), *sys.argv[1:]]
    ))
