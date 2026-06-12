"""
stock_setup.py
==============
Interactive configuration wizard for the Stock Toolkit.
Creates/updates config.env by walking the user through the available
settings.  Safe to re-run: existing values are shown as defaults.

Run:
    python3 stock_setup.py
    python3 stock_setup.py --non-interactive   # accept all defaults silently
"""

import argparse
import sys
from pathlib import Path

from stock_toolkit.common import BASE_DIR, CONFIG_PATH

TEMPLATE_PATH = BASE_DIR / "config.env.template"

# ── colours ───────────────────────────────────────────────────────────────────
def _c(code, text): return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text
cyan   = lambda t: _c("36",   t)
green  = lambda t: _c("32",   t)
yellow = lambda t: _c("33",   t)
bold   = lambda t: _c("1",    t)
dim    = lambda t: _c("2",    t)


# ── config.env parser/writer ──────────────────────────────────────────────────

def load_cfg(path: Path) -> dict:
    """Load key=value pairs from a config.env file, preserving comments."""
    cfg = {}
    if not path.exists():
        return cfg
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            val = val.strip()
            if val.startswith("#"):
                val = ""
            elif " #" in val:
                val = val[:val.index(" #")].strip()
            if len(val) >= 2 and val[0] in ('"', "'") and val[-1] == val[0]:
                val = val[1:-1]
            cfg[key.strip()] = val
    return cfg


def write_cfg(path: Path, cfg: dict, template: Path | None = None) -> None:
    """
    Write cfg to path.  If a template exists, preserve its comments and
    section structure, just filling in the values.  Otherwise write bare
    key=value pairs.
    """
    if template and template.exists():
        lines = template.read_text().splitlines()
        out = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                out.append(line)
                continue
            key = stripped.split("=", 1)[0].strip()
            if key in cfg:
                out.append(f"{key}={cfg[key]}")
            else:
                out.append(line)
        path.write_text("\n".join(out) + "\n")
    else:
        with open(path, "w") as f:
            for key, val in cfg.items():
                f.write(f"{key}={val}\n")


# ── interactive prompt ────────────────────────────────────────────────────────

def ask(prompt: str, default: str = "", secret: bool = False,
        required: bool = False) -> str:
    """
    Prompt the user for a value.
    - Shows existing value as default (masked if secret)
    - Pressing Enter keeps the default
    - Returns the new value or the default
    """
    display_default = ("*" * min(len(default), 8) + "…" if secret and default
                       else default)
    if display_default:
        full_prompt = f"  {prompt} [{dim(display_default)}]: "
    else:
        full_prompt = f"  {prompt}: "

    while True:
        try:
            val = input(full_prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
        if not val:
            val = default
        if required and not val:
            print(f"  {yellow('⚠')}  This field is required.")
            continue
        return val


def ask_yn(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    try:
        ans = input(f"  {prompt} [{hint}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    if not ans:
        return default
    return ans.startswith("y")


def section(title: str) -> None:
    print(f"\n{bold('── ' + title + ' ' + '─' * max(0, 54 - len(title)))}")


# ── setup wizard ──────────────────────────────────────────────────────────────

def run_wizard(non_interactive: bool = False) -> dict:
    """Walk the user through all settings and return a config dict."""

    existing = load_cfg(CONFIG_PATH)

    def get(key, default=""):
        return existing.get(key, default)

    def field(key, prompt, default_key=None, secret=False, required=False,
              hint=None):
        default = get(default_key or key)
        if non_interactive:
            return default
        if hint:
            print(f"  {dim(hint)}")
        val = ask(prompt, default=default, secret=secret, required=required)
        return val

    cfg = {}

    print()
    print(bold("Stock Toolkit — Configuration Wizard"))
    print(dim("Press Enter to keep the current value shown in brackets."))
    print(dim("Run with --non-interactive to accept all defaults silently."))

    # ── symbols ───────────────────────────────────────────────────────────────
    section("Symbols")
    print(dim("  Comma-separated list of tickers to track."))
    print(dim("  Use exchange suffixes for non-US stocks: ENEL.MI, SAP.DE"))
    cfg["SYMBOLS"] = field(
        "SYMBOLS", "Symbols to collect",
        default_key="SYMBOLS",
        hint=None,
    ) or "AAPL,MSFT,GOOGL,AMZN,TSLA"

    print(dim("\n  Symbols to never collect (bare EU tickers, delisted, etc.)"))
    cfg["SYMBOLS_IGNORE"] = field(
        "SYMBOLS_IGNORE", "Symbols to ignore (optional)",
    )

    # ── API keys ──────────────────────────────────────────────────────────────
    section("API Keys")
    print(dim("  All keys are optional — yfinance works without any key."))
    print(dim("  Skip keys you don't have by pressing Enter."))

    keys = [
        ("ALPHAVANTAGE_KEY", "Alpha Vantage key",
         "https://www.alphavantage.co/support/#api-key",
         "25 calls/day free — good EU+US daily bars"),
        ("FINNHUB_KEY",      "Finnhub key",
         "https://finnhub.io/register",
         "60 calls/min free — US real-time quotes only"),
        ("MASSIVE_KEY",      "Massive (formerly Polygon.io) key",
         "https://massive.com/dashboard",
         "5 calls/min free — US EOD bars"),
        ("FMP_KEY",          "FMP (Financial Modeling Prep) key",
         "https://site.financialmodelingprep.com/developer/docs/dashboard",
         "250 calls/day free — major US large-caps"),
        ("TWELVEDATA_KEY",   "Twelve Data key",
         "https://twelvedata.com/register",
         "8 credits/min free — US daily + hourly bars"),
        ("MARKETSTACK_KEY",  "Marketstack key",
         "https://marketstack.com/signup",
         "100 calls/month free — EOD bars including EU"),
        ("ANTHROPIC_API_KEY", "Anthropic key (for Briefing tab)",
         "https://console.anthropic.com/",
         "Pay-as-you-go — ~$0.01 per briefing on Sonnet"),
    ]
    for key, prompt, url, hint in keys:
        print(f"\n  {dim(hint)}")
        print(f"  {dim('Get key: ' + url)}")
        cfg[key] = field(key, prompt, secret=True)

    # ── paid tier flags ───────────────────────────────────────────────────────
    section("Paid Tier Flags")
    print(dim("  Set to 'true' if you have a paid plan for these services."))

    def yn_field(key, prompt):
        current = get(key, "false").lower() == "true"
        if non_interactive:
            return "true" if current else "false"
        return "true" if ask_yn(prompt, default=current) else "false"

    cfg["FINNHUB_PAID"]      = yn_field("FINNHUB_PAID",
                                         "Finnhub paid plan? (unlocks EU + candle data)")
    cfg["ALPHAVANTAGE_PAID"] = yn_field("ALPHAVANTAGE_PAID",
                                         "Alpha Vantage paid plan? (unlocks full history)")

    # ── UI settings ───────────────────────────────────────────────────────────
    section("UI Settings")
    print(dim("  Sources the Collect tab in the UI is allowed to use."))
    print(dim("  Keep conservative to protect API call budgets."))
    print(dim("  All sources: yfinance,alphavantage,finnhub,polygon,fmp,twelvedata,marketstack"))
    cfg["UI_COLLECT_SOURCES"] = field(
        "UI_COLLECT_SOURCES", "UI collect sources",
    ) or "yfinance"

    # ── paths ─────────────────────────────────────────────────────────────────
    section("Paths (optional)")
    print(dim("  Leave blank to use the directory containing the scripts."))
    cfg["OUTPUT_DIR"] = field("OUTPUT_DIR", "Output directory (blank = script dir)")

    return cfg


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Interactive configuration wizard for the Stock Toolkit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python3 stock_setup.py                  # interactive wizard
  python3 stock_setup.py --non-interactive  # accept all defaults
  python3 stock_setup.py --show           # print current config
        """
    )
    parser.add_argument("--non-interactive", action="store_true",
                        help="Accept all current/default values without prompting")
    parser.add_argument("--show", action="store_true",
                        help="Print current config.env and exit")
    args = parser.parse_args()

    if args.show:
        if not CONFIG_PATH.exists():
            print("config.env not found. Run stock_setup.py to create it.")
            sys.exit(1)
        cfg = load_cfg(CONFIG_PATH)
        print(f"\nCurrent config ({CONFIG_PATH}):\n")
        for key, val in cfg.items():
            masked = ("*" * 8 + "…") if ("KEY" in key or "key" in key) and val else val
            print(f"  {key:<24} = {masked or dim('(not set)')}")
        return

    cfg = run_wizard(non_interactive=args.non_interactive)

    # write
    write_cfg(CONFIG_PATH, cfg, template=TEMPLATE_PATH)

    print()
    print(f"{green('✓')}  Config saved to {CONFIG_PATH}")

    if not args.non_interactive:
        print()
        print(dim("  You can re-run this wizard at any time:"))
        print(dim("    python3 stock_setup.py"))
        print(dim("  Or edit the file directly:"))
        print(dim(f"    {CONFIG_PATH}"))


if __name__ == "__main__":
    main()
