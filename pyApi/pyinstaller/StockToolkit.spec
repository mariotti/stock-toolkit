# PyInstaller spec for the standalone Stock Toolkit Windows executable.
# Build:
#     cd pyApi/pyinstaller
#     pyinstaller StockToolkit.spec
# Output: dist/StockToolkit/StockToolkit.exe (+ supporting files)
#
# Must be built on the target OS (PyInstaller does not cross-compile).
# The CI workflow .github/workflows/build-windows-exe.yml does this
# on a windows-latest runner.

from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files

# Streamlit + plotly + altair are notorious for losing their static
# assets when packaged. Pull them in wholesale.
datas, binaries, hiddenimports = collect_all("streamlit")
datas += collect_data_files("plotly")
datas += collect_data_files("altair")

# Ship the toolkit package itself so the frozen launcher can find it
# via sys._MEIPASS / stock_toolkit / ui / app.py.
project_root = Path(SPECPATH).parent          # pyApi/
datas += [(str(project_root / "stock_toolkit"), "stock_toolkit")]

# Hidden imports that PyInstaller's static analysis tends to miss:
hiddenimports += [
    "stock_toolkit",
    "stock_toolkit.ui",
    "stock_toolkit.ui.app",
    "stock_toolkit.ui.helpers",
    "stock_toolkit.ui.game",
    "stock_toolkit.ui.tabs",
    "stock_toolkit.ui.tabs.score",
    "stock_toolkit.ui.tabs.analysis",
    "stock_toolkit.ui.tabs.backtest",
    "stock_toolkit.ui.tabs.alerts",
    "stock_toolkit.ui.tabs.collect",
    "stock_toolkit.ui.tabs.briefing",
    "stock_toolkit.collector",
    "stock_toolkit.collector.config",
    "stock_toolkit.collector.sources",
    "stock_toolkit.analysis",
    "stock_toolkit.score",
    "stock_toolkit.backtest",
    "stock_toolkit.alerts",
    "stock_toolkit.game",
    "stock_toolkit.fundamentals",
    "yfinance",
    "pandas",
    "numpy",
    "plotly",
    "plotly.graph_objects",
    "anthropic",
]


a = Analysis(
    ["launcher.py"],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="StockToolkit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # Keep the console — Streamlit prints its status there, and the
    # user uses it to see the data dir + close the app.
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=True, upx_exclude=[],
    name="StockToolkit",
)
