"""Deterministic output checks with strict JSON and RFC 6901 pointers."""

from __future__ import annotations

from typing import Any

from .errors import ConfigError
from .suite import parse_json, validate_checks


def _json_equal(left: Any, right: Any) -> bool:
    # Python considers True == 1. JSON has distinct boolean and number types.
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_json_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    return left == right


def _resolve_pointer(document: Any, path: str) -> tuple[bool, Any]:
    if not path:
        return True, document
    current = document
    for segment in path[1:].split("/"):
        key = segment.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if key not in current:
                return False, None
            current = current[key]
        elif isinstance(current, list):
            # RFC 6901 indices are ASCII digits without leading zeroes.
            if not key or any(char not in "0123456789" for char in key):
                return False, None
            if len(key) > 1 and key[0] == "0":
                return False, None
            # Compare lengths before int conversion, including Python's digit cap.
            if len(key) > len(str(len(current))):
                return False, None
            index = int(key)
            if index >= len(current):
                return False, None
            current = current[index]
        else:
            return False, None
    return True, current


def evaluate_checks(output: str, checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one verdict per check; malformed model JSON is a failed check."""
    if not isinstance(output, str):
        raise TypeError("provider output must be a string")
    validated = validate_checks(checks)
    results: list[dict[str, Any]] = []
    parsed: Any = None
    parse_error: str | None = None
    parsed_once = False
    for check in validated:
        kind = check["type"]
        if kind in {"contains", "not_contains", "equals"}:
            actual, expected = output, check["value"]
            if not check["case_sensitive"]:
                actual, expected = actual.casefold(), expected.casefold()
            if kind == "contains":
                passed = expected in actual
                message = "Required text found." if passed else "Required text missing."
            elif kind == "not_contains":
                passed = expected not in actual
                message = "Forbidden text absent." if passed else "Forbidden text found."
            else:
                passed = actual == expected
                message = "Output matches expected text." if passed else "Output differs from expected text."
        elif kind == "max_length":
            length = len(output)
            passed = length <= check["value"]
            message = f"Output has {length} characters; limit is {check['value']}."
        else:
            if not parsed_once:
                try:
                    parsed = parse_json(output, "output")
                except ConfigError as exc:
                    parse_error = str(exc)
                parsed_once = True
            if parse_error is not None:
                passed = False
                message = f"Output is not valid strict JSON: {parse_error}"
            elif kind == "json_valid":
                passed = True
                message = "Output is valid strict JSON."
            else:
                found, value = _resolve_pointer(parsed, check["path"])
                passed = found and _json_equal(value, check["value"])
                if not found:
                    message = f"JSON pointer {check['path']!r} does not exist."
                elif passed:
                    message = f"Value at JSON pointer {check['path']!r} matches."
                else:
                    message = f"Value at JSON pointer {check['path']!r} differs."
        results.append({"type": kind, "passed": passed, "message": message})
    return results
