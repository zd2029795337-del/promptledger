"""Validate, compare, and render portable evaluation reports.

Reports are data files: generated prompts and completions are never interpreted as
HTML, scripts, or Markdown instructions by these renderers.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
import json
import math
from pathlib import Path
import re
from typing import Any

from .errors import ConfigError


_COMPARISON_LABELS = {
    "regressions": "Regressions",
    "improvements": "Improvements",
    "added": "Added cases",
    "removed": "Removed cases",
    "changed_outputs": "Changed outputs",
    "incompatible": "Changed checks",
}
_SIGNATURE = re.compile(r"[0-9a-fA-F]{64}\Z")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigError(message)


def _string(value: Any, location: str, *, nonempty: bool = False) -> None:
    _require(isinstance(value, str), f"{location} must be a string")
    if nonempty:
        _require(bool(value.strip()), f"{location} must not be empty")


def _integer(value: Any, location: str) -> None:
    _require(type(value) is int and value >= 0, f"{location} must be a nonnegative integer")


def _object(value: Any, location: str, required: tuple[str, ...]) -> None:
    _require(isinstance(value, dict), f"{location} must be an object")
    for key in required:
        _require(key in value, f"{location} is missing {key!r}")


def _validate_comparison(comparison: Any, location: str) -> None:
    _object(comparison, location, tuple(_COMPARISON_LABELS))
    for key in _COMPARISON_LABELS:
        items = comparison[key]
        _require(isinstance(items, list), f"{location}.{key} must be a list")
        for index, case_id in enumerate(items):
            _string(case_id, f"{location}.{key}[{index}]", nonempty=True)
        _require(len(items) == len(set(items)), f"{location}.{key} contains duplicate IDs")


def _validate_report(report: Any, location: str = "report") -> None:
    """Reject malformed files before they can affect a comparison or rendering."""
    _object(
        report,
        location,
        ("schema_version", "suite", "created_at", "provider", "model", "summary", "results"),
    )
    _require(
        type(report["schema_version"]) is int and report["schema_version"] == 1,
        f"{location}.schema_version must be 1",
    )
    for key in ("suite", "provider"):
        _string(report[key], f"{location}.{key}", nonempty=True)
    _string(report["created_at"], f"{location}.created_at", nonempty=True)
    try:
        _require(len(report["created_at"]) > 10, f"{location}.created_at must be an ISO datetime")
        datetime.fromisoformat(report["created_at"])
    except ValueError as exc:
        raise ConfigError(f"{location}.created_at must be an ISO datetime") from exc
    if report["model"] is not None:
        _string(report["model"], f"{location}.model", nonempty=True)
    summary = report["summary"]
    summary_keys = ("total", "passed", "failed", "errors", "input_tokens", "output_tokens")
    _object(summary, f"{location}.summary", summary_keys)
    for key in summary_keys:
        _integer(summary[key], f"{location}.summary.{key}")
    _require(isinstance(report["results"], list), f"{location}.results must be a list")
    ids: set[str] = set()
    passed = errors = input_tokens = output_tokens = 0
    for index, result in enumerate(report["results"]):
        at = f"{location}.results[{index}]"
        _object(
            result,
            at,
            ("id", "prompt", "output", "passed", "error", "checks", "latency_ms", "usage", "check_signature"),
        )
        _string(result["id"], f"{at}.id", nonempty=True)
        _require(result["id"] not in ids, f"{location} contains duplicate case ID {result['id']!r}")
        ids.add(result["id"])
        for key in ("prompt", "output"):
            _string(result[key], f"{at}.{key}")
        _require(type(result["passed"]) is bool, f"{at}.passed must be a boolean")
        if result["error"] is not None:
            _string(result["error"], f"{at}.error")
            _require(not result["passed"], f"{at} cannot pass with an error")
            errors += 1
        _require(isinstance(result["checks"], list), f"{at}.checks must be a list")
        for check_index, check in enumerate(result["checks"]):
            check_at = f"{at}.checks[{check_index}]"
            _object(check, check_at, ("type", "passed", "message"))
            _string(check["type"], f"{check_at}.type", nonempty=True)
            _string(check["message"], f"{check_at}.message")
            _require(type(check["passed"]) is bool, f"{check_at}.passed must be a boolean")
        if result["error"] is None:
            _require(
                result["passed"] == all(check["passed"] for check in result["checks"]),
                f"{at}.passed does not match check results",
            )
        latency = result["latency_ms"]
        try:
            finite_latency = type(latency) in (int, float) and math.isfinite(latency)
        except OverflowError:
            finite_latency = False
        _require(
            finite_latency and latency >= 0,
            f"{at}.latency_ms must be a finite nonnegative number",
        )
        _object(result["usage"], f"{at}.usage", ("input_tokens", "output_tokens"))
        for key in ("input_tokens", "output_tokens"):
            _integer(result["usage"][key], f"{at}.usage.{key}")
        _string(result["check_signature"], f"{at}.check_signature")
        _require(
            bool(_SIGNATURE.fullmatch(result["check_signature"])),
            f"{at}.check_signature must be a SHA-256 hexadecimal digest",
        )
        passed += int(result["passed"])
        input_tokens += result["usage"]["input_tokens"]
        output_tokens += result["usage"]["output_tokens"]
    expected = {
        "total": len(report["results"]),
        "passed": passed,
        "failed": len(report["results"]) - passed,
        "errors": errors,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    for key, value in expected.items():
        _require(summary[key] == value, f"{location}.summary.{key} does not match results")
    if "comparison" in report:
        _validate_comparison(report["comparison"], f"{location}.comparison")


def compare_reports(baseline: dict, current: dict) -> dict:
    """Compare case IDs and outcomes, rejecting corrupt or unrelated reports.

    Changed check definitions are incompatible and do not count as a regression
    or improvement. Removed cases and incompatible checks are explicit so callers
    can fail a CI gate instead of silently shrinking evaluation coverage.
    """
    _validate_report(baseline, "baseline report")
    _validate_report(current, "current report")
    _require(baseline["suite"] == current["suite"], "Cannot compare reports from different suites")
    before = {result["id"]: result for result in baseline["results"]}
    after = {result["id"]: result for result in current["results"]}
    comparison: dict[str, list[str]] = {key: [] for key in _COMPARISON_LABELS}
    for case_id, result in after.items():
        if case_id not in before:
            comparison["added"].append(case_id)
            continue
        previous = before[case_id]
        if previous["output"] != result["output"]:
            comparison["changed_outputs"].append(case_id)
        if previous["check_signature"] != result["check_signature"]:
            comparison["incompatible"].append(case_id)
        elif previous["passed"] and not result["passed"]:
            comparison["regressions"].append(case_id)
        elif not previous["passed"] and result["passed"]:
            comparison["improvements"].append(case_id)
    comparison["removed"] = [case_id for case_id in before if case_id not in after]
    return comparison


def validate_report(report: dict) -> dict:
    """Validate a loaded report before making provider calls; return it unchanged."""
    _validate_report(report)
    return report


def _md_inline(value: Any) -> str:
    # Escape raw HTML as well as Markdown delimiters; metadata cannot create a
    # new table row, heading, image, or link in a Markdown renderer.
    text = escape(str(value), quote=False).replace("\r", " ").replace("\n", " ")
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~\-])", r"\\\1", text)


def _fenced(value: str) -> str:
    longest = max((len(match.group()) for match in re.finditer(r"`+", value)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{value}\n{fence}"


def _markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        f"# PromptLedger report: {_md_inline(report['suite'])}",
        "",
        f"Created: {_md_inline(report['created_at'])}  ",
        f"Provider: {_md_inline(report['provider'])}  ",
        f"Model: {_md_inline(report['model'] or 'Not specified')}",
        "",
        "| Total | Passed | Failed | Errors | Input tokens | Output tokens |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| {summary['total']} | {summary['passed']} | {summary['failed']} | {summary['errors']} | {summary['input_tokens']} | {summary['output_tokens']} |",
        "",
    ]
    if "comparison" in report:
        lines += ["## Baseline comparison", "", "| Change | Count | Case IDs |", "| --- | ---: | --- |"]
        for key, label in _COMPARISON_LABELS.items():
            ids = report["comparison"][key]
            lines.append(f"| {label} | {len(ids)} | {', '.join(_md_inline(case_id) for case_id in ids) or '—'} |")
        lines += ["", "Changed checks require review before comparing pass/fail outcomes.", ""]
    lines += ["## Cases", "", "| Case | Status | Latency (ms) | Input tokens | Output tokens |", "| --- | --- | ---: | ---: | ---: |"]
    for result in report["results"]:
        status = "ERROR" if result["error"] is not None else "PASS" if result["passed"] else "FAIL"
        lines.append(f"| {_md_inline(result['id'])} | {status} | {result['latency_ms']:.2f} | {result['usage']['input_tokens']} | {result['usage']['output_tokens']} |")
    lines.append("")
    for result in report["results"]:
        lines += [f"### {_md_inline(result['id'])}", "", "Prompt:", "", _fenced(result["prompt"]), "", "Output:", "", _fenced(result["output"]), ""]
        if result["error"] is not None:
            lines += ["Error:", "", _fenced(result["error"]), ""]
        if result["checks"]:
            lines += ["| Check | Status | Message |", "| --- | --- | --- |"]
            for check in result["checks"]:
                lines.append(f"| {_md_inline(check['type'])} | {'PASS' if check['passed'] else 'FAIL'} | {_md_inline(check['message'])} |")
            lines.append("")
        else:
            lines += ["No checks were evaluated.", ""]
    return "\n".join(lines)


_CSS = """
:root{color-scheme:light dark;--bg:#f5f7fb;--card:#fff;--text:#182438;--muted:#54647c;--line:#dce3ed;--pass:#146747;--fail:#af263e;--soft:#edf2fa}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.6 system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
main{max-width:1160px;margin:auto;padding:40px 24px 64px}header{margin-bottom:28px}.eyebrow{font-size:12px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
h1{font-size:clamp(28px,5vw,44px);line-height:1.15;letter-spacing:-.03em;margin:10px 0 16px;overflow-wrap:anywhere}h2{font-size:23px;margin:0 0 16px}h3{margin:0;font-size:19px;overflow-wrap:anywhere}p{margin:8px 0}.meta,.muted{color:var(--muted)}.meta{display:flex;flex-wrap:wrap;gap:8px 24px;overflow-wrap:anywhere}
.stats{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px;margin-bottom:28px}.stat,.panel,.case{background:var(--card);border:1px solid var(--line);border-radius:12px}.stat{padding:18px}.stat strong{font-size:28px;line-height:1.2;display:block}.stat span{font-size:12px;color:var(--muted)}
.panel{padding:24px;margin:24px 0}.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;text-align:left}th{color:var(--muted);font-size:12px;white-space:nowrap}th,td{padding:12px;border-bottom:1px solid var(--line);vertical-align:top}tr:last-child td{border-bottom:0}td{overflow-wrap:anywhere}.number{text-align:right;font-variant-numeric:tabular-nums}
a{color:inherit;text-underline-offset:3px}.badge{font-size:11px;font-weight:750;padding:4px 9px;border-radius:5px;display:inline-block;background:var(--soft);white-space:nowrap}.pass{color:var(--pass)}.fail,.error{color:var(--fail)}.case{padding:24px;margin:20px 0;scroll-margin:20px}.case-head{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;margin-bottom:12px}.case-label{font-size:12px;color:var(--muted);font-weight:650;margin:22px 0 8px}pre{margin:0;padding:16px;background:var(--soft);border-radius:8px;white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace}.error-box{border-left:3px solid var(--fail)}footer{margin-top:32px;font-size:12px;color:var(--muted)}
@media(max-width:800px){.stats{grid-template-columns:repeat(3,minmax(0,1fr))}main{padding:28px 16px}.panel,.case{padding:18px}}
@media(max-width:420px){.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.stat{padding:14px}.case-head{flex-direction:column;gap:8px}}
@media(prefers-color-scheme:dark){:root{--bg:#101827;--card:#172235;--text:#e4ebf7;--muted:#a4b4cc;--line:#304158;--pass:#75deb0;--fail:#ff9aaa;--soft:#1f2d43}}
@media print{body{background:white;color:black}main{max-width:none;padding:0}.case{break-inside:avoid}pre{background:#f2f2f2}.table-wrap{overflow:visible}}
"""


def _html(report: dict) -> str:
    def e(value: Any) -> str:
        return escape(str(value), quote=True)

    summary = report["summary"]
    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;; base-uri &#39;none&#39;; form-action &#39;none&#39;">',
        f"<title>{e(report['suite'])} · PromptLedger report</title><style>{_CSS}</style></head><body><main>",
        '<header><div class="eyebrow">PromptLedger / Evaluation report</div>',
        f"<h1>{e(report['suite'])}</h1><div class=\"meta\"><span>Provider: {e(report['provider'])}</span><span>Model: {e(report['model'] or 'Not specified')}</span><span>{e(report['created_at'])}</span></div></header>",
        '<section class="stats" aria-label="Evaluation summary">',
    ]
    for key, label in (("total", "Total cases"), ("passed", "Passed"), ("failed", "Failed"), ("errors", "Provider errors"), ("input_tokens", "Input tokens"), ("output_tokens", "Output tokens")):
        parts.append(f'<div class="stat"><strong>{summary[key]:,}</strong><span>{label}</span></div>')
    parts.append("</section>")
    if "comparison" in report:
        parts.append('<section class="panel"><h2>Baseline comparison</h2><div class="table-wrap"><table><thead><tr><th>Change</th><th class="number">Count</th><th>Case IDs</th></tr></thead><tbody>')
        for key, label in _COMPARISON_LABELS.items():
            ids = report["comparison"][key]
            parts.append(f'<tr><td>{label}</td><td class="number">{len(ids)}</td><td>{", ".join(e(case_id) for case_id in ids) or "—"}</td></tr>')
        parts.append('</tbody></table></div><p class="muted">Changed checks require review before comparing pass/fail outcomes.</p></section>')
    parts.append('<section class="panel"><h2>Cases</h2><div class="table-wrap"><table><thead><tr><th>Case</th><th>Status</th><th class="number">Latency (ms)</th><th class="number">Input tokens</th><th class="number">Output tokens</th></tr></thead><tbody>')
    for index, result in enumerate(report["results"]):
        status = "error" if result["error"] is not None else "pass" if result["passed"] else "fail"
        parts.append(f'<tr><td><a href="#case-{index}">{e(result["id"])}</a></td><td><span class="badge {status}">{status.upper()}</span></td><td class="number">{result["latency_ms"]:.2f}</td><td class="number">{result["usage"]["input_tokens"]:,}</td><td class="number">{result["usage"]["output_tokens"]:,}</td></tr>')
    if not report["results"]:
        parts.append('<tr><td colspan="5" class="muted">No cases in this report.</td></tr>')
    parts.append('</tbody></table></div></section><section aria-label="Case details">')
    for index, result in enumerate(report["results"]):
        status = "error" if result["error"] is not None else "pass" if result["passed"] else "fail"
        parts.append(f'<article class="case" id="case-{index}"><div class="case-head"><h3>{e(result["id"])}</h3><span class="badge {status}">{status.upper()}</span></div><p class="muted">{result["latency_ms"]:.2f} ms · {result["usage"]["input_tokens"]:,} input tokens · {result["usage"]["output_tokens"]:,} output tokens</p>')
        for key, label in (("prompt", "Prompt"), ("output", "Output")):
            parts.append(f'<div class="case-label">{label}</div><pre>{e(result[key])}</pre>')
        if result["error"] is not None:
            parts.append(f'<div class="case-label">Provider error</div><pre class="error-box">{e(result["error"])}</pre>')
        if result["checks"]:
            parts.append('<div class="case-label">Checks</div><div class="table-wrap"><table><thead><tr><th>Check</th><th>Status</th><th>Message</th></tr></thead><tbody>')
            for check in result["checks"]:
                check_status = "pass" if check["passed"] else "fail"
                parts.append(f'<tr><td>{e(check["type"])}</td><td><span class="badge {check_status}">{check_status.upper()}</span></td><td>{e(check["message"])}</td></tr>')
            parts.append('</tbody></table></div>')
        else:
            parts.append('<p class="muted">No checks were evaluated.</p>')
        parts.append('</article>')
    parts.append('</section><footer>Generated by PromptLedger. This standalone report works offline. Model output is displayed as plain text.</footer></main></body></html>')
    return "\n".join(parts)


def write_reports(report: dict, output_dir: Path) -> list[Path]:
    """Write JSON, Markdown, and an offline HTML report; return their paths."""
    _validate_report(report)
    try:
        json_text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ConfigError(f"Report cannot be serialized as JSON: {exc}") from exc
    documents = {"report.json": json_text, "report.md": _markdown(report), "report.html": _html(report)}
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, content in documents.items():
        path = output_dir / name
        path.write_text(content, encoding="utf-8")
        paths.append(path)
    return paths
