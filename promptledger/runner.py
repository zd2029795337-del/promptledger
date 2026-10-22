"""Execute validated suites and build versioned, comparable reports."""

from __future__ import annotations

import copy
import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any

from .checks import evaluate_checks
from .errors import ProviderError
from .suite import validate_suite


def _digest(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def check_signature(checks: list[dict[str, Any]]) -> str:
    """Hash normalized check definitions for safe baseline comparisons."""
    return _digest(checks)


def _usage(response: dict[str, Any]) -> dict[str, int]:
    usage = response["usage"]
    result = {"input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"]}
    for name, value in result.items():
        if type(value) is not int or value < 0:
            raise TypeError(f"provider {name} must be a nonnegative integer")
    return result


def run_suite(
    suite: dict[str, Any], provider: Any, provider_name: str, model: str | None = None,
) -> dict[str, Any]:
    """Run all cases, continuing on sanitized provider failures only.

    Providers must return ``text`` and integer ``usage`` counts. A ProviderError
    message is considered safe to publish; providers must never include secrets.
    Other exceptions deliberately propagate so implementation defects stay visible.
    """
    validated = validate_suite(suite)
    summary = {
        "total": len(validated["cases"]), "passed": 0, "failed": 0,
        "errors": 0, "input_tokens": 0, "output_tokens": 0,
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "suite": validated["name"],
        "suite_snapshot": copy.deepcopy(validated),
        "suite_digest": _digest(validated),
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "provider": provider_name,
        "model": model,
        "summary": summary,
        "results": [],
    }
    for case in validated["cases"]:
        started = time.perf_counter_ns()
        result: dict[str, Any] = {
            "id": case["id"], "prompt": case["prompt"], "output": "",
            "passed": False, "error": None, "checks": [], "latency_ms": 0.0,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "check_signature": check_signature(case["checks"]),
        }
        try:
            response = provider.generate(case, validated["instructions"])
        except ProviderError as exc:
            result["error"] = str(exc)
            summary["errors"] += 1
        else:
            result["output"] = response["text"]
            result["usage"] = _usage(response)
            result["checks"] = evaluate_checks(response["text"], case["checks"])
            result["passed"] = all(check["passed"] for check in result["checks"])
        result["latency_ms"] = round((time.perf_counter_ns() - started) / 1_000_000, 3)
        summary["passed" if result["passed"] else "failed"] += 1
        summary["input_tokens"] += result["usage"]["input_tokens"]
        summary["output_tokens"] += result["usage"]["output_tokens"]
        report["results"].append(result)
    return report
