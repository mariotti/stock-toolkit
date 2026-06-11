"""
stock_toolkit.common
====================
Shared configuration loading and filesystem paths for the stock toolkit.
All toolkit modules import from here instead of re-implementing the
config.env parser and the database path constants.

The data directory (config.env, stock_data.db, data/) is resolved from
the STOCK_DIR environment variable if set, otherwise the current working
directory. The bin/ wrappers cd into STOCK_DIR before launching.
"""

import os
from pathlib import Path

if os.environ.get("STOCK_DIR"):
    BASE_DIR = Path(os.environ["STOCK_DIR"]).expanduser().resolve()
else:
    BASE_DIR = Path.cwd()

CONFIG_PATH = BASE_DIR / "config.env"   # keep out of git (see .gitignore)
LIVE_DB     = BASE_DIR / "stock_data.db"
HIST_DIR    = BASE_DIR / "data"


def load_config(config_path: Path = CONFIG_PATH) -> dict:
    """
    Parse a simple KEY=VALUE config file.
    - Lines starting with # are comments.
    - Inline comments (value # comment) are stripped.
    - Quoted values ("value" or 'value') have quotes stripped.
    - Missing file is silently ignored (defaults apply).
    """
    cfg: dict = {}
    if not config_path.exists():
        return cfg
    with open(config_path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            # strip inline comment (covers both "value # comment" and "  # comment")
            if val.startswith("#"):
                val = ""
            elif " #" in val:
                val = val[:val.index(" #")].strip()
            # strip matching quotes
            if (val.startswith('"') and val.endswith('"')) or \
               (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            cfg[key] = val
    return cfg
