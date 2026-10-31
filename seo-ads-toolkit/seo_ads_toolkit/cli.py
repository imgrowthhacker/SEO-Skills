"""Command line entry point: `seo-ads <tool> ...` or `python -m seo_ads_toolkit <tool> ...`."""
from __future__ import annotations

import argparse
import json
import sys

from . import core
from .core import ToolError
from .registry import all_tools

CATEGORY_ORDER = [
    "SEO / On-page", "SEO / Technical", "SEO / Content & keywords",
    "SEO / Data & reporting", "Ads / Copy, tracking & landing pages",
    "Ads / Math & testing", "Ads / Keywords & search terms",
]


def catalog():
    tools = all_tools()
    lines = ["seo-ads %s  -  %d tools\n" % (core.VERSION, len(tools))]
    cats = [c for c in CATEGORY_ORDER if any(t.category == c for t in tools)]
    cats += sorted({t.category for t in tools} - set(cats))
    for c in cats:
        lines.append(c)
        for t in tools:
            if t.category == c:
                lines.append("  %-14s %s" % (t.name, t.help))
        lines.append("")
    lines.append("Run `seo-ads <tool> --help` for options. Add --json for machine-readable output.")
    return "\n".join(lines)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="seo-ads", description="Open-source SEO and Ads toolkit (standard library only).",
        epilog="Run `seo-ads list` to see every tool.")
    parser.add_argument("--version", action="version", version="seo-ads " + core.VERSION)
    sub = parser.add_subparsers(dest="tool", metavar="TOOL")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="print JSON instead of text")
    common.add_argument("--out", metavar="FILE", help="write result to FILE (.json, .csv or .txt)")
    common.add_argument("--fail-on", choices=["error", "warning"], help="exit 1 if issues of this severity or worse exist (for CI)")
    sub.add_parser("list", help="list all tools")
    for t in all_tools():
        p = sub.add_parser(t.name, help=t.help, description=t.help, parents=[common],
                           formatter_class=argparse.RawDescriptionHelpFormatter)
        t.add_args(p)
        p.set_defaults(_tool=t)
    return parser


def emit(result, args):
    raw = result.get("_raw") if isinstance(result, dict) else None
    out = args.out
    if out:
        if out.lower().endswith(".csv"):
            rows = core.first_table(result)
            if not rows:
                raise ToolError("this tool has no tabular result to write as CSV; use .json or .txt")
            core.write_csv(out, rows)
        elif out.lower().endswith(".json"):
            core.write_json(out, {k: v for k, v in result.items() if k != "_raw"} if isinstance(result, dict) else result)
        else:
            core.write_text(out, raw if raw is not None else core.render_text(result))
        print("wrote %s" % out, file=sys.stderr)
    if args.json:
        print(json.dumps({k: v for k, v in result.items() if k != "_raw"} if isinstance(result, dict) else result,
                         indent=2, ensure_ascii=False, default=str))
    elif raw is not None:
        sys.stdout.write(raw if raw.endswith("\n") else raw + "\n")
    elif not out or not args.out.lower().endswith((".json", ".csv", ".txt")):
        print(core.render_text(result))
    elif out:
        pass


def exit_code(result, fail_on):
    if not fail_on or not isinstance(result, dict):
        return 0
    bad = {"error"} if fail_on == "error" else {"error", "warning"}
    return 1 if any(i.get("severity") in bad for i in result.get("issues", []) if isinstance(i, dict)) else 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.tool in (None, "list"):
        print(catalog())
        return 0
    try:
        result = args._tool.run(args)
        emit(result, args)
        return exit_code(result, args.fail_on)
    except ToolError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
