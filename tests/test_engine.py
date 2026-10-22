"""Tests for schema boundaries, deterministic checks, and isolated failures."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from promptledger.checks import evaluate_checks
from promptledger.errors import ConfigError, ProviderError
from promptledger.runner import check_signature, run_suite
from promptledger.suite import load_json, load_suite, parse_json, validate_suite


def example_suite() -> dict:
    return {
        "version": 1,
        "name": "Regression checks",
        "cases": [{"id": "greeting", "prompt": "Say hello", "checks": [{"type": "contains", "value": "hello"}]}],
    }


class SuiteTests(unittest.TestCase):
    def test_normalization_does_not_mutate_and_is_idempotent(self):
        original = example_suite()
        before = copy.deepcopy(original)
        validated = validate_suite(original)
        self.assertEqual(original, before)
        self.assertEqual(validated["instructions"], "")
        self.assertFalse(validated["cases"][0]["checks"][0]["case_sensitive"])
        self.assertEqual(validated, validate_suite(validated))

    def test_utf8_bom_and_unicode(self):
        suite = example_suite()
        suite["name"] = "中文回归测试"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "suite.json"
            path.write_text(json.dumps(suite, ensure_ascii=False), encoding="utf-8-sig")
            self.assertEqual(load_suite(path)["name"], suite["name"])

    def test_invalid_json_is_rejected(self):
        for text in [
            '{"a": 1, "a": 2}', '{"nested": {"a": 1, "a": 2}}',
            '{"x": NaN}', '{"x": Infinity}', '{"x": -Infinity}',
            '{"x": 1e999}', '{"x": -1e999}', '{"x": 1,}', '',
        ]:
            with self.subTest(text=text), self.assertRaises(ConfigError):
                parse_json(text)

    def test_escaped_lone_surrogates_are_rejected_in_values_and_keys(self):
        for text in [
            '"\\ud800"', '"\\udfff"', '{"\\ud800": "value"}',
            '{"nested": [{"value": "\\udfff"}]}',
        ]:
            with self.subTest(text=text), self.assertRaisesRegex(ConfigError, "surrogate"):
                parse_json(text)
        # The JSON decoder already combines valid escaped pairs; preserve it.
        self.assertEqual(parse_json('"\\ud83d\\ude00"'), "😀")
        self.assertEqual(parse_json('{"\\ud83d\\ude00":"\\ud83d\\ude03"}'), {"😀": "😃"})

    def test_programmatic_surrogates_are_rejected_before_generation(self):
        mutations = [
            lambda suite: suite.update(name="\ud800"),
            lambda suite: suite.update(instructions="\udfff"),
            lambda suite: suite["cases"][0].update(id="\ud800"),
            lambda suite: suite["cases"][0].update(prompt="\udfff"),
            lambda suite: suite["cases"][0]["checks"][0].update(value="\ud800"),
            lambda suite: suite.update({"\ud800": 1}),
            lambda suite: suite["cases"][0].update({"\ud800": 1}),
            lambda suite: suite["cases"][0]["checks"][0].update({"\ud800": 1}),
            lambda suite: suite["cases"][0].update(checks=[{
                "type": "json_path_equals", "path": "", "value": {"\ud800": "bad key"},
            }]),
            lambda suite: suite["cases"][0].update(checks=[{
                "type": "json_path_equals", "path": "", "value": {"nested": ["\udfff"]},
            }]),
        ]
        class NeverCalled:
            def generate(self, case, instructions):
                raise AssertionError("validation must finish before generation")
        for index, mutate in enumerate(mutations):
            suite = example_suite()
            mutate(suite)
            with self.subTest(index=index), self.assertRaisesRegex(ConfigError, "surrogate"):
                run_suite(suite, NeverCalled(), "test")

    def test_missing_and_non_utf8_files_have_configuration_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            with self.assertRaises(ConfigError):
                load_json(path)
            path.write_bytes(b"\xff\xff")
            with self.assertRaises(ConfigError):
                load_json(path)

    def test_unknown_fields_rejected_at_all_levels(self):
        for level in ("suite", "case", "check"):
            suite = example_suite()
            target = suite if level == "suite" else suite["cases"][0]
            if level == "check":
                target = target["checks"][0]
            target["typo"] = True
            with self.subTest(level=level), self.assertRaisesRegex(ConfigError, "unknown"):
                validate_suite(suite)

    def test_invalid_top_level_and_case_types(self):
        mutations = [
            ("version", True), ("version", 1.0), ("version", 2),
            ("name", " "), ("name", 5), ("instructions", None),
            ("cases", []), ("cases", {}), ("cases", [None]),
        ]
        for field, value in mutations:
            suite = example_suite()
            suite[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ConfigError):
                validate_suite(suite)
        for field, value in [("id", ""), ("id", "\t"), ("prompt", None), ("checks", []), ("checks", {})]:
            suite = example_suite()
            suite["cases"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ConfigError):
                validate_suite(suite)

    def test_duplicate_case_ids_rejected(self):
        suite = example_suite()
        suite["cases"].append(copy.deepcopy(suite["cases"][0]))
        with self.assertRaisesRegex(ConfigError, "duplicate case id"):
            validate_suite(suite)

    def test_check_schema_rejects_wrong_types_and_ignored_parameters(self):
        invalid = [
            {"type": "unknown"}, {"type": "contains", "value": 2},
            {"type": "contains", "value": "ok", "case_sensitive": 0},
            {"type": "equals"}, {"type": "json_valid", "value": True},
            {"type": "max_length", "value": True}, {"type": "max_length", "value": -1},
            {"type": "max_length", "value": 2.0},
            {"type": "json_path_equals", "path": "a", "value": 1},
            {"type": "json_path_equals", "path": "/a~", "value": 1},
            {"type": "json_path_equals", "path": "/a~2b", "value": 1},
            {"type": "json_path_equals", "path": "/a", "value": float("nan")},
            {"type": "json_path_equals", "path": "/a", "value": {2: "bad key"}},
        ]
        for check in invalid:
            suite = example_suite()
            suite["cases"][0]["checks"] = [check]
            with self.subTest(check=check), self.assertRaises(ConfigError):
                validate_suite(suite)

    def test_circular_json_expectation_rejected(self):
        value = []
        value.append(value)
        suite = example_suite()
        suite["cases"][0]["checks"] = [{"type": "json_path_equals", "path": "", "value": value}]
        with self.assertRaisesRegex(ConfigError, "circular"):
            validate_suite(suite)


class CheckTests(unittest.TestCase):
    def check_passes(self, output, check):
        result = evaluate_checks(output, [check])[0]
        self.assertIsInstance(result["message"], str)
        self.assertEqual(result["type"], check["type"])
        return result["passed"]

    def test_text_defaults_and_explicit_case_sensitivity(self):
        self.assertTrue(self.check_passes("HELLO", {"type": "contains", "value": "hello"}))
        self.assertFalse(self.check_passes("HELLO", {"type": "contains", "value": "hello", "case_sensitive": True}))
        self.assertFalse(self.check_passes("HELLO", {"type": "equals", "value": "hello"}))
        self.assertTrue(self.check_passes("HELLO", {"type": "equals", "value": "hello", "case_sensitive": False}))
        self.assertFalse(self.check_passes("HELLO", {"type": "not_contains", "value": "hello"}))
        self.assertTrue(self.check_passes("safe", {"type": "not_contains", "value": "secret"}))

    def test_unicode_casefold_and_length(self):
        self.assertTrue(self.check_passes("Straße", {"type": "contains", "value": "STRASSE"}))
        self.assertTrue(self.check_passes("你好", {"type": "max_length", "value": 2}))
        self.assertFalse(self.check_passes("你好!", {"type": "max_length", "value": 2}))
        self.assertTrue(self.check_passes("", {"type": "max_length", "value": 0}))

    def test_json_valid_accepts_all_root_types(self):
        for output in ['{"ok":true}', '[1,2]', '"text"', "null", "false", "1.5"]:
            with self.subTest(output=output):
                self.assertTrue(self.check_passes(output, {"type": "json_valid"}))

    def test_json_valid_rejects_ambiguous_and_nonfinite_output(self):
        for output in ['{"a": 1, "a": 2}', "NaN", "Infinity", "1e999", "```json\n{}\n```", "nonsense"]:
            with self.subTest(output=output):
                self.assertFalse(self.check_passes(output, {"type": "json_valid"}))

    def test_json_pointer_escapes_empty_keys_arrays_and_root(self):
        output = '{"a/b":{"~key":[{"":3}]},"":false}'
        for path, value in [("/a~1b/~0key/0/", 3), ("/", False), ("", json.loads(output))]:
            with self.subTest(path=path):
                self.assertTrue(self.check_passes(output, {"type": "json_path_equals", "path": path, "value": value}))
        for path in ["/a~1b/~0key/01", "/a~1b/~0key/-", "/a~1b/~0key/2", "/missing", "/a~1b/~0key/0//x"]:
            with self.subTest(path=path):
                self.assertFalse(self.check_passes(output, {"type": "json_path_equals", "path": path, "value": None}))

    def test_json_pointer_distinguishes_null_missing_and_boolean_number(self):
        self.assertTrue(self.check_passes('{"x":null}', {"type": "json_path_equals", "path": "/x", "value": None}))
        self.assertFalse(self.check_passes('{}', {"type": "json_path_equals", "path": "/x", "value": None}))
        self.assertFalse(self.check_passes('{"x":true}', {"type": "json_path_equals", "path": "/x", "value": 1}))
        self.assertFalse(self.check_passes('{"x":[true]}', {"type": "json_path_equals", "path": "/x", "value": [1]}))
        self.assertTrue(self.check_passes('{"x":1.0}', {"type": "json_path_equals", "path": "/x", "value": 1}))

    def test_invalid_json_fails_each_json_check_without_aborting(self):
        results = evaluate_checks("not JSON", [
            {"type": "json_valid"}, {"type": "json_path_equals", "path": "/x", "value": 1},
            {"type": "contains", "value": "JSON"},
        ])
        self.assertEqual([row["passed"] for row in results], [False, False, True])

    def test_very_large_pointer_index_is_missing(self):
        self.assertFalse(self.check_passes("[]", {"type": "json_path_equals", "path": "/" + "9" * 5000, "value": None}))


class RunnerTests(unittest.TestCase):
    class Provider:
        def __init__(self, answers):
            self.answers = iter(answers)
            self.calls = []

        def generate(self, case, instructions):
            self.calls.append((case["id"], instructions))
            answer = next(self.answers)
            if isinstance(answer, Exception):
                raise answer
            return {"text": answer, "usage": {"input_tokens": 3, "output_tokens": 2}}

    def test_report_summary_counts_and_continues_after_provider_error(self):
        suite = example_suite()
        suite["instructions"] = "Be concise."
        suite["cases"] = [dict(copy.deepcopy(suite["cases"][0]), id=f"case-{i}") for i in range(3)]
        provider = self.Provider([ProviderError("Request timed out."), "wrong", "Hello"])
        report = run_suite(suite, provider, "test", "model-a")
        self.assertEqual(len(provider.calls), 3)
        self.assertEqual(provider.calls[0], ("case-0", "Be concise."))
        self.assertEqual(report["summary"], {
            "total": 3, "passed": 1, "failed": 2, "errors": 1, "input_tokens": 6, "output_tokens": 4,
        })
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["provider"], "test")
        self.assertEqual(report["model"], "model-a")
        self.assertTrue(report["created_at"].endswith("Z"))
        self.assertEqual(report["results"][0]["error"], "Request timed out.")
        self.assertEqual(report["results"][0]["checks"], [])
        self.assertIsNone(report["results"][2]["error"])
        for result in report["results"]:
            self.assertGreaterEqual(result["latency_ms"], 0)
            self.assertEqual(len(result["check_signature"]), 64)
        json.dumps(report, allow_nan=False)

    def test_late_invalid_case_prevents_all_provider_calls(self):
        suite = example_suite()
        invalid = copy.deepcopy(suite["cases"][0])
        invalid.update(id="bad", checks=[{"type": "not-a-check"}])
        suite["cases"].append(invalid)
        provider = self.Provider(["hello"])
        with self.assertRaises(ConfigError):
            run_suite(suite, provider, "test")
        self.assertEqual(provider.calls, [])

    def test_programming_errors_are_not_hidden(self):
        provider = self.Provider([RuntimeError("bug")])
        with self.assertRaisesRegex(RuntimeError, "bug"):
            run_suite(example_suite(), provider, "test")

    def test_invalid_provider_usage_is_not_recorded_as_success(self):
        class InvalidProvider:
            def generate(self, case, instructions):
                return {"text": "hello", "usage": {"input_tokens": True, "output_tokens": -1}}
        with self.assertRaises(TypeError):
            run_suite(example_suite(), InvalidProvider(), "test")

    def test_signature_is_stable_for_effective_defaults_and_changes_with_checks(self):
        suite = example_suite()
        first = run_suite(suite, self.Provider(["hello"]), "test")["results"][0]["check_signature"]
        suite["cases"][0]["checks"][0]["case_sensitive"] = False
        second = run_suite(suite, self.Provider(["hello"]), "test")["results"][0]["check_signature"]
        self.assertEqual(first, second)
        suite["cases"][0]["checks"][0]["value"] = "different"
        third = run_suite(suite, self.Provider(["hello"]), "test")["results"][0]["check_signature"]
        self.assertNotEqual(first, third)
        self.assertEqual(third, check_signature(validate_suite(suite)["cases"][0]["checks"]))

    def test_suite_provenance_tracks_prompt_and_instructions_independently_of_checks(self):
        suite = example_suite()
        original = copy.deepcopy(suite)
        first = run_suite(suite, self.Provider(["hello"]), "test")
        self.assertEqual(suite, original)
        self.assertEqual(first["suite_snapshot"], validate_suite(original))
        self.assertEqual(len(first["suite_digest"]), 64)
        suite["instructions"] = "Answer in one sentence."
        second = run_suite(suite, self.Provider(["hello"]), "test")
        suite["cases"][0]["prompt"] = "Say hello politely."
        third = run_suite(suite, self.Provider(["hello"]), "test")
        self.assertNotEqual(first["suite_digest"], second["suite_digest"])
        self.assertNotEqual(second["suite_digest"], third["suite_digest"])
        self.assertEqual(first["results"][0]["check_signature"], second["results"][0]["check_signature"])
        self.assertEqual(second["results"][0]["check_signature"], third["results"][0]["check_signature"])
        self.assertEqual(first["suite_snapshot"], validate_suite(original))
        # Editing a report must not edit a suite used for a later run.
        third["suite_snapshot"]["cases"][0]["checks"][0]["value"] = "changed"
        self.assertEqual(suite["cases"][0]["checks"][0]["value"], "hello")


if __name__ == "__main__":
    unittest.main()
