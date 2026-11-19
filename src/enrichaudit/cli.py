"""Command-line interface for EnrichAudit.

Exit codes:
  0 — ran successfully (findings may or may not be present)
  1 — findings at or above the --fail-on severity (CI gate tripped)
  2 — usage or input error
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from enrichaudit import __version__
from enrichaudit.common import SEVERITY_RANK, load_rows, validate_options
from enrichaudit.logic import EnrichAuditLogic
from enrichaudit.output import render, worst_severity

TOOL = "enrichaudit"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL,
        description="Audit enriched social/content datasets for miscalculated engagement rates, "
        "stale fields, and contradictory labels.",
    )
    parser.add_argument("data", help="dataset file to audit (.csv, .json, or .jsonl)")
    parser.add_argument(
        "--format",
        "-f",
        choices=["table", "json", "csv", "md", "markdown"],
        default="table",
        help="output format (default: table)",
    )
    parser.add_argument("--output", "-o", metavar="FILE", help="write the report to FILE instead of stdout")
    parser.add_argument(
        "--fail-on",
        choices=["none", "low", "medium", "high"],
        default="none",
        help="exit 1 if any finding is at or above this severity (default: none)",
    )
    parser.add_argument("--config", metavar="FILE", help="JSON file of options (thresholds)")
    parser.add_argument(
        "--staleness-days",
        type=int,
        metavar="N",
        help="flag enrichment fields older than N days (default: 7)",
    )
    parser.add_argument(
        "--rate-tolerance",
        type=float,
        metavar="PT",
        help="allowed gap in percentage points between given and recomputed engagement rate (default: 2.0)",
    )
    parser.add_argument(
        "--as-of",
        metavar="DATE",
        help="reference date for staleness, e.g. 2026-10-01 (default: latest date in the data)",
    )
    parser.add_argument(
        "--no-sentiment-emotion",
        action="store_true",
        help="skip the sentiment-vs-emotion contradiction check",
    )
    parser.add_argument(
        "--profile",
        metavar="FILE",
        help="write a generated JSON column contract to FILE; normal checks still run",
    )
    parser.add_argument(
        "--results-log",
        metavar="FILE",
        help="append one JSON line per finding to FILE (check-results ledger)",
    )
    parser.add_argument(
        "--id-column",
        metavar="COLUMN",
        help="column to use as record id for conflict detection (default: best-effort id)",
    )
    parser.add_argument(
        "--conflict-columns",
        metavar="COLUMNS",
        help="comma-separated enrichment columns to compare for conflicts",
    )
    parser.add_argument("--version", action="version", version=f"{TOOL} {__version__}")
    return parser


def _merge_options(args: argparse.Namespace) -> dict[str, Any]:
    """Config file first, then explicit CLI flags override it."""
    options: dict[str, Any] = {}
    if args.config:
        parsed = json.loads(Path(args.config).read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError(f"{args.config}: config must be a JSON object")
        options.update(parsed)
    if args.staleness_days is not None:
        options["staleness_days"] = args.staleness_days
    if args.rate_tolerance is not None:
        options["rate_tolerance"] = args.rate_tolerance
    if args.as_of is not None:
        options["as_of"] = args.as_of
    if args.no_sentiment_emotion:
        options["check_sentiment_emotion"] = False
    if args.profile:
        options["profile"] = True
    if args.results_log is not None:
        options["results_log"] = args.results_log
    if args.id_column is not None:
        options["id_column"] = args.id_column
    if args.conflict_columns is not None:
        options["conflict_columns"] = args.conflict_columns
    validate_options(options)
    return options


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        options = _merge_options(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"{TOOL}: {exc}", file=sys.stderr)
        return 2

    try:
        rows = load_rows(args.data)
    except FileNotFoundError:
        print(f"{TOOL}: file not found: {args.data}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"{TOOL}: {exc}", file=sys.stderr)
        return 2

    result = EnrichAuditLogic().run(rows, options)

    if args.profile:
        contract = result.summary.get("profile")
        if contract is None:
            print(f"{TOOL}: profile was not generated", file=sys.stderr)
            return 2
        try:
            Path(args.profile).write_text(json.dumps(contract, indent=2, ensure_ascii=False), encoding="utf-8")
        except (OSError, TypeError, ValueError) as exc:
            print(f"{TOOL}: could not write profile: {exc}", file=sys.stderr)
            return 2

    text = render(result, args.format, tool=TOOL, version=__version__)

    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text, end="" if text.endswith("\n") else "\n")

    if args.fail_on != "none":
        worst = worst_severity(result)
        if worst and SEVERITY_RANK.get(worst, 0) >= SEVERITY_RANK[args.fail_on]:
            return 1
    return 0
