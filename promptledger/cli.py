"""Command line entry point with configuration preflight before generation."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .errors import ConfigError
from .providers import OpenAIProvider, ReplayProvider
from .reports import compare_reports, validate_report, write_reports
from .runner import check_signature, run_suite
from .suite import load_json, load_suite


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(
        prog="promptledger", description="Reproducible regression checks for AI outputs."
    )
    cli.add_argument("--version", action="version", version=f"PromptLedger {__version__}")
    commands = cli.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Validate a suite without generating outputs.")
    validate.add_argument("suite", type=Path)
    run = commands.add_parser("run", help="Evaluate recorded outputs or explicitly call OpenAI.")
    run.add_argument("suite", type=Path)
    run.add_argument("--provider", choices=("replay", "openai"), default="replay")
    run.add_argument("--responses", type=Path, help="JSON mapping of case IDs to replay strings.")
    run.add_argument("--model", help="OpenAI model ID (or set OPENAI_MODEL).")
    run.add_argument("--output", type=Path, default=Path("reports/latest"))
    run.add_argument("--baseline", type=Path, help="Previous report.json to compare against.")
    run.add_argument("--timeout", type=float, default=60.0)
    run.add_argument("--max-output-tokens", type=int, default=1024)
    run.add_argument("--max-cases", type=int, default=100, help="Reject larger runs before generation.")
    diff = commands.add_parser("diff", help="Compare two existing reports without network calls.")
    diff.add_argument("baseline", type=Path)
    diff.add_argument("current", type=Path)
    return cli


def _preflight_baseline(baseline: dict, suite: dict) -> None:
    validate_report(baseline)
    if baseline["suite"] != suite["name"]:
        raise ConfigError("Baseline and current suite names must match.")
    cases = {case["id"]: case for case in suite["cases"]}
    for old in baseline["results"]:
        if old["id"] not in cases:
            raise ConfigError("Baseline cases were removed; review the change and create a new baseline.")
        if old["check_signature"] != check_signature(cases[old["id"]]["checks"]):
            raise ConfigError("Baseline checks changed; review the change and create a new baseline.")


def _show_comparison(comparison: dict) -> None:
    for label, ids in comparison.items():
        print(f"{label}: {', '.join(ids) if ids else '-'}")


def _gate(comparison: dict) -> bool:
    return bool(comparison["regressions"] or comparison["removed"] or comparison["incompatible"])


def _run(args: argparse.Namespace) -> int:
    suite = load_suite(args.suite)
    if args.command == "validate":
        print(f"Valid suite: {suite['name']} ({len(suite['cases'])} cases)")
        return 0
    if args.max_cases < 1 or len(suite["cases"]) > args.max_cases:
        raise ConfigError("Suite exceeds --max-cases, or --max-cases is not positive.")
    baseline = load_json(args.baseline) if args.baseline else None
    if baseline is not None:
        _preflight_baseline(baseline, suite)
    # Prevent replacing the baseline or source data with the generated reports.
    protected = [args.suite, args.responses, args.baseline]
    targets = [args.output.resolve() / filename for filename in ("report.json", "report.md", "report.html")]
    resolved_targets = [target.resolve() for target in targets]
    if any(path and path.resolve() in resolved_targets for path in protected):
        raise ConfigError("Output files would overwrite an input or baseline; choose another --output.")
    args.output.mkdir(parents=True, exist_ok=True)
    for target in targets:
        if target.is_symlink():
            raise ConfigError("Output report paths must not be symbolic links.")
        if target.exists() and not target.is_file():
            raise ConfigError("Output report path exists and is not a regular file.")
    if args.provider == "replay":
        if not args.responses:
            raise ConfigError("Replay requires --responses FILE; no network call was made.")
        if args.model:
            raise ConfigError("--model is only supported with --provider openai.")
        provider = ReplayProvider.from_file(args.responses, [case["id"] for case in suite["cases"]])
        model = None
    else:
        if args.responses:
            raise ConfigError("--responses is only supported with --provider replay.")
        model = args.model or os.environ.get("OPENAI_MODEL", "")
        provider = OpenAIProvider(model, timeout=args.timeout, max_output_tokens=args.max_output_tokens)
        print(f"OpenAI: {len(suite['cases'])} requests, output cap {args.max_output_tokens} tokens/request; no automatic retries.")
    report = run_suite(suite, provider, args.provider, model=model)
    comparison = compare_reports(baseline, report) if baseline is not None else None
    if comparison:
        report["comparison"] = comparison
        _show_comparison(comparison)
    paths = write_reports(report, args.output)
    summary = report["summary"]
    print(f"{summary['passed']}/{summary['total']} passed; {summary['failed']} failed ({summary['errors']} provider errors).")
    print(f"Token usage (successful responses): input={summary['input_tokens']}, output={summary['output_tokens']}")
    for path in paths:
        print(path)
    return 1 if summary["failed"] or (comparison is not None and _gate(comparison)) else 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "diff":
            comparison = compare_reports(load_json(args.baseline), load_json(args.current))
            _show_comparison(comparison)
            return 1 if _gate(comparison) else 0
        return _run(args)
    except ConfigError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    except OSError:
        print("File operation failed; check paths, permissions, and available disk space.", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted. Earlier API requests may still have incurred usage.", file=sys.stderr)
        return 130
