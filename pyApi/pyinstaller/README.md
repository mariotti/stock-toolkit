# PyInstaller — standalone Windows executable

This directory builds `StockToolkit.exe`: a Windows-native bundle of
the Stock Toolkit that needs **no Docker and no Python install** on
the user's machine. Pure double-click.

## What the user gets

`StockToolkit-windows-x64-X.Y.Z.zip` unzips to a folder containing:

- `StockToolkit.exe` — entry point (double-click to launch)
- ~200 supporting DLLs and Python wheels
- ~200 MB total unpacked

Running the .exe opens a console window, starts the Streamlit server
on the first free port (8501 if available), and points the user's
default browser at `http://localhost:8501`. Closing the console
window shuts the server down.

### Where data lives

The launcher first tries `./data/` next to the .exe — same behaviour
as the Docker bundle. If that location is read-only (e.g. someone
unzipped into `Program Files`), it falls back to:

- `%APPDATA%\stock-toolkit\` on Windows
- `~/.local/share/stock-toolkit/` elsewhere (dev runs)

Either way, `config.env`, the SQLite DBs, and `logs/` all persist
across launches.

## Building

You **must** build on Windows. PyInstaller does not cross-compile;
Wine-based hacks generally break on Streamlit's runtime assets.

The supported path is the GitHub Actions workflow:
`.github/workflows/build-windows-exe.yml`. It runs on every pushed
tag and via `Actions → Build Windows executable → Run workflow`.
The resulting zip lands as a workflow artifact; download it and
upload to the corresponding GitLab Release.

### Local build (Windows host)

```powershell
cd pyApi
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -e .
pip install pyinstaller

cd pyinstaller
pyinstaller --noconfirm StockToolkit.spec
```

Output appears under `dist/StockToolkit/`. Zip the folder for
distribution:

```powershell
Compress-Archive dist\StockToolkit\* StockToolkit-windows-x64.zip
```

## Known issues

- **SmartScreen warning.** Until the binary is code-signed with an
  Authenticode certificate (~$200–500/year), Windows shows
  *"Windows protected your PC"* on first run. Users click
  *More info* → *Run anyway*. Microsoft's reputation system
  silences the warning after enough installs.
- **Size.** ~200 MB unpacked. Dominated by numpy + pandas + plotly +
  Streamlit's bundled JS/CSS. Some of this can be trimmed by excluding
  unused matplotlib backends, but it's not a quick win.
- **First-launch latency.** PyInstaller extracts to a temp dir on
  startup; expect 5–10 s before the browser opens.
- **Antivirus false positives.** Some AVs heuristically flag
  PyInstaller-packaged executables. The mitigation is to submit the
  binary to the vendor for whitelisting, or code-sign.

## Architecture

`launcher.py` is the entry script. The PyInstaller spec (`StockToolkit.spec`)
collects:

- All of `streamlit` (data + binaries + hidden imports) via `collect_all`
- `plotly` and `altair` data files
- The entire `stock_toolkit` source tree as a bundled data folder

In a frozen build, `launcher.main()`:

1. Resolves the data directory (next-to-.exe preferred).
2. Sets `OUTPUT_DIR` so `stock_toolkit.collector.config` writes
   `stock_data.db`, `logs/`, etc. to that location.
3. Finds a free port (defaults to 8501).
4. Starts a background thread that opens the browser after a short
   delay.
5. Resolves the Streamlit app script from `sys._MEIPASS`.
6. Hands control to `streamlit.web.cli.main()`.
