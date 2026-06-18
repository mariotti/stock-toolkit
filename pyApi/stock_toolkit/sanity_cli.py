"""
stock-sanity — run stock_toolkit.sanity.run_all() and print the report.

Exit codes:
  0  no ERROR-severity issues (warnings/infos still printed)
  1  at least one ERROR (--strict promotes warnings to errors too)
"""

import argparse
import json
import sys

from stock_toolkit.sanity import ERROR, INFO, WARNING, run_all


_PREFIX = {
    ERROR:   "\033[31mERROR  \033[0m",
    WARNING: "\033[33mWARN   \033[0m",
    INFO:    "\033[36mINFO   \033[0m",
}

_PLAIN_PREFIX = {ERROR: "ERROR  ", WARNING: "WARN   ", INFO: "INFO   "}


def _emit(report, *, use_color: bool) -> None:
    prefixes = _PREFIX if use_color else _PLAIN_PREFIX
    if not report.issues:
        print("All sanity checks passed.")
        return
    # Group by check for readability.
    groups: dict[str, list] = {}
    for i in report.issues:
        groups.setdefault(i.check, []).append(i)
    for check_name in sorted(groups):
        print(f"\n{check_name}:")
        for i in groups[check_name]:
            print(f"  {prefixes[i.severity]} {i.message}")
            if i.detail:
                print(f"           {i.detail}")
    print(
        f"\nSummary: {len(report.errors)} error(s), "
        f"{len(report.warnings)} warning(s), {len(report.infos)} info(s)."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run sanity checks against the toolkit's on-disk state.",
    )
    parser.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Emit the report as JSON to stdout (machine consumption).",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Treat warnings as errors when computing the exit code.",
    )
    parser.add_argument(
        "--no-color", dest="no_color", action="store_true",
        help="Disable ANSI colour in the human-readable output.",
    )
    args = parser.parse_args()

    report = run_all()

    if args.as_json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        use_color = sys.stdout.isatty() and not args.no_color
        _emit(report, use_color=use_color)

    failing = bool(report.errors)
    if args.strict and report.warnings:
        failing = True
    sys.exit(1 if failing else 0)


if __name__ == "__main__":
    main()
