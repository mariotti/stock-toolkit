"""
PyInstaller entry point for the standalone Stock Toolkit executable.

When frozen by PyInstaller, this becomes ``StockToolkit.exe``. Running
it starts the Streamlit server in-process, points the user's default
browser at the dashboard, and tears the server down when the console
window is closed.
"""
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _find_free_port(preferred: int = 8501) -> int:
    """Return ``preferred`` if free, else any free ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def _data_dir() -> Path:
    """Pick a writable directory for OUTPUT_DIR.

    Preference order:
      1. ``data/`` next to the .exe — mirrors the Docker bundle and
         keeps everything together for a user who unzipped to Desktop.
      2. ``%APPDATA%\\stock-toolkit\\`` on Windows / ``~/.local/share``
         elsewhere — fallback if the .exe lives somewhere read-only
         (e.g. ``Program Files``).
    """
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
    else:
        exe_dir = Path(__file__).parent

    candidate = exe_dir / "data"
    try:
        candidate.mkdir(exist_ok=True)
        probe = candidate / ".write_test"
        probe.write_text("ok")
        probe.unlink()
        return candidate
    except OSError:
        if sys.platform == "win32":
            base = Path(os.environ.get("APPDATA", str(Path.home())))
        else:
            base = Path.home() / ".local" / "share"
        d = base / "stock-toolkit"
        d.mkdir(parents=True, exist_ok=True)
        return d


def _open_browser_when_ready(url: str, delay: float = 3.0) -> None:
    """Give Streamlit a moment to bind, then open the browser."""
    time.sleep(delay)
    webbrowser.open(url)


def main() -> None:
    data = _data_dir()
    os.environ["OUTPUT_DIR"] = str(data)
    os.chdir(str(data))

    print("=" * 60)
    print("  Stock Toolkit")
    print(f"  Data dir:  {data}")
    print("  Close this window to stop the server.")
    print("=" * 60)

    port = _find_free_port(8501)
    url  = f"http://localhost:{port}"

    threading.Thread(
        target=_open_browser_when_ready, args=(url, 3.0), daemon=True,
    ).start()

    # Locate the Streamlit entry script — in a frozen build PyInstaller
    # extracts the package tree under sys._MEIPASS; in a dev run we
    # walk the importable package.
    if getattr(sys, "frozen", False):
        app_script = Path(sys._MEIPASS) / "stock_toolkit" / "ui" / "app.py"
    else:
        from stock_toolkit import ui
        app_script = Path(ui.__file__).parent / "app.py"

    # Force production mode — Streamlit treats explicit --server.port as
    # incompatible with global.developmentMode=true, which is the default
    # when no source-watcher detects a script. Setting it false here lets
    # both flags coexist in the frozen build.
    sys.argv = [
        "streamlit", "run", str(app_script),
        "--server.port",              str(port),
        "--server.headless",          "true",
        "--global.developmentMode",   "false",
        "--browser.gatherUsageStats", "false",
    ]
    from streamlit.web import cli
    cli.main()


if __name__ == "__main__":
    main()
