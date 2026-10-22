"""Strict JSON loading and validation for PromptLedger suites."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .errors import ConfigError


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate object key {key!r}")
        result[key] = value
    return result


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("JSON numbers must be finite")
    return number


def _reject_constant(value: str) -> Any:
    raise ValueError(f"{value} is not a valid JSON number")


def _utf8_string(value: str, where: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ConfigError(f"{where} contains an unpaired Unicode surrogate; use valid Unicode scalar values") from exc


def _utf8_json(value: Any, where: str) -> None:
    """Reject escaped lone surrogates before JSON data reaches UTF-8 outputs."""
    if isinstance(value, str):
        _utf8_string(value, where)
    elif isinstance(value, list):
        for item in value:
            _utf8_json(item, where)
    elif isinstance(value, dict):
        for key, item in value.items():
            _utf8_string(key, f"{where} object key")
            _utf8_json(item, where)


def parse_json(text: str, source: str = "JSON") -> Any:
    """Parse unambiguous, finite JSON; UTF-8 BOMs are accepted at the start."""
    try:
        value = json.loads(
            text.removeprefix("\ufeff"),
            object_pairs_hook=_unique_object,
            parse_float=_finite_float,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{source}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    except (ValueError, RecursionError) as exc:
        raise ConfigError(f"{source}: {exc}") from exc
    try:
        _utf8_json(value, source)
    except RecursionError as exc:
        raise ConfigError(f"{source}: JSON nesting is too deep") from exc
    return value


def load_json(path: str | Path) -> Any:
    """Read a UTF-8 JSON file with duplicate-key and nonfinite-number checks."""
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"Cannot read JSON file {source}: {exc}") from exc
    return parse_json(text, str(source))


def _fields(value: Any, allowed: set[str], required: set[str], where: str) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{where} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ConfigError(f"{where} must have string keys")
    for key in value:
        _utf8_string(key, f"{where} field name")
    unknown = set(value) - allowed
    if unknown:
        raise ConfigError(f"{where}: unknown field(s): {', '.join(sorted(unknown))}")
    missing = required - set(value)
    if missing:
        raise ConfigError(f"{where}: missing field(s): {', '.join(sorted(missing))}")
    return value


def _string(value: Any, where: str, *, nonempty: bool = False) -> str:
    if not isinstance(value, str) or (nonempty and not value.strip()):
        qualifier = "a nonempty string" if nonempty else "a string"
        raise ConfigError(f"{where} must be {qualifier}")
    _utf8_string(value, where)
    return value


def _json_value(value: Any, where: str, parents: set[int] | None = None) -> Any:
    """Validate and copy programmatically supplied JSON values without coercion."""
    if isinstance(value, str):
        return _string(value, where)
    if value is None or isinstance(value, bool):
        return value
    if type(value) is int:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, (list, dict)):
        parents = set() if parents is None else parents
        if id(value) in parents:
            raise ConfigError(f"{where} cannot contain a circular value")
        parents.add(id(value))
        try:
            if isinstance(value, list):
                return [_json_value(item, where, parents) for item in value]
            if any(not isinstance(key, str) for key in value):
                raise ConfigError(f"{where} must use string object keys")
            for key in value:
                _utf8_string(key, f"{where} object key")
            return {key: _json_value(item, where, parents) for key, item in value.items()}
        finally:
            parents.remove(id(value))
    raise ConfigError(f"{where} must be a finite JSON value")


def _pointer(path: Any, where: str) -> str:
    path = _string(path, where)
    if path and not path.startswith("/"):
        raise ConfigError(f"{where} must be an RFC 6901 JSON pointer (empty or starting with '/')")
    position = 0
    while position < len(path):
        if path[position] == "~":
            if position + 1 >= len(path) or path[position + 1] not in "01":
                raise ConfigError(f"{where}: '~' must be escaped as '~0' or '~1'")
            position += 1
        position += 1
    return path


def validate_checks(checks: Any, where: str = "checks") -> list[dict[str, Any]]:
    """Validate checks and make their effective defaults explicit."""
    if not isinstance(checks, list) or not checks:
        raise ConfigError(f"{where} must be a nonempty array")
    normalized: list[dict[str, Any]] = []
    for index, check in enumerate(checks):
        location = f"{where}[{index}]"
        if not isinstance(check, dict):
            raise ConfigError(f"{location} must be an object")
        kind = check.get("type")
        if not isinstance(kind, str):
            raise ConfigError(f"{location}.type must be a string")
        if kind in {"contains", "not_contains", "equals"}:
            _fields(check, {"type", "value", "case_sensitive"}, {"type", "value"}, location)
            value = _string(check["value"], f"{location}.value")
            sensitive = check.get("case_sensitive", kind == "equals")
            if type(sensitive) is not bool:
                raise ConfigError(f"{location}.case_sensitive must be a boolean")
            normalized.append({"type": kind, "value": value, "case_sensitive": sensitive})
        elif kind == "json_valid":
            _fields(check, {"type"}, {"type"}, location)
            normalized.append({"type": kind})
        elif kind == "json_path_equals":
            _fields(check, {"type", "path", "value"}, {"type", "path", "value"}, location)
            normalized.append({
                "type": kind,
                "path": _pointer(check["path"], f"{location}.path"),
                "value": _json_value(check["value"], f"{location}.value"),
            })
        elif kind == "max_length":
            _fields(check, {"type", "value"}, {"type", "value"}, location)
            value = check["value"]
            if type(value) is not int or value < 0:
                raise ConfigError(f"{location}.value must be a nonnegative integer")
            normalized.append({"type": kind, "value": value})
        else:
            raise ConfigError(f"{location}: unsupported check type {kind!r}")
    return normalized


def validate_suite(data: Any) -> dict[str, Any]:
    """Validate the full suite before a provider is contacted; never mutate input."""
    _fields(data, {"version", "name", "instructions", "cases"}, {"version", "name", "cases"}, "suite")
    if type(data["version"]) is not int or data["version"] != 1:
        raise ConfigError("suite.version must be the integer 1")
    name = _string(data["name"], "suite.name", nonempty=True)
    instructions = _string(data.get("instructions", ""), "suite.instructions")
    cases = data["cases"]
    if not isinstance(cases, list) or not cases:
        raise ConfigError("suite.cases must be a nonempty array")
    normalized: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    for index, case in enumerate(cases):
        location = f"suite.cases[{index}]"
        _fields(case, {"id", "prompt", "checks"}, {"id", "prompt", "checks"}, location)
        identifier = _string(case["id"], f"{location}.id", nonempty=True)
        if identifier in identifiers:
            raise ConfigError(f"{location}: duplicate case id {identifier!r}")
        identifiers.add(identifier)
        normalized.append({
            "id": identifier,
            "prompt": _string(case["prompt"], f"{location}.prompt"),
            "checks": validate_checks(case["checks"], f"{location}.checks"),
        })
    return {"version": 1, "name": name, "instructions": instructions, "cases": normalized}


def load_suite(path: str | Path) -> dict[str, Any]:
    """Read and validate a suite from disk."""
    return validate_suite(load_json(path))
