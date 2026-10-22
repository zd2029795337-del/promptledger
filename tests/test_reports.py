"""Report integrity, baseline behavior, and untrusted text rendering tests."""

from copy import deepcopy
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import unittest

from promptledger.errors import ConfigError
from promptledger.reports import compare_reports, validate_report, write_reports


def case(case_id="greeting", *, passed=True, output="你好，世界", signature="a" * 64):
    return {
        "id": case_id,
        "prompt": "Say hello",
        "output": output,
        "passed": passed,
        "error": None,
        "checks": [{"type": "contains", "passed": passed, "message": "Expected greeting"}],
        "latency_ms": 12.5,
        "usage": {"input_tokens": 3, "output_tokens": 4},
        "check_signature": signature,
    }


def report(*cases):
    cases = list(cases)
    passed = sum(item["passed"] for item in cases)
    return {
        "schema_version": 1,
        "suite": "greetings",
        "created_at": "2026-10-22T06:00:00+00:00",
        "provider": "mock",
        "model": None,
        "summary": {
            "total": len(cases),
            "passed": passed,
            "failed": len(cases) - passed,
            "errors": sum(item["error"] is not None for item in cases),
            "input_tokens": sum(item["usage"]["input_tokens"] for item in cases),
            "output_tokens": sum(item["usage"]["output_tokens"] for item in cases),
        },
        "results": cases,
    }


class TagCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.attributes = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.extend(attrs)


class ReportTests(unittest.TestCase):
    def test_writes_unicode_and_three_portable_formats(self):
        data = report(case())
        with tempfile.TemporaryDirectory() as directory:
            paths = write_reports(data, Path(directory))
            self.assertEqual([path.name for path in paths], ["report.json", "report.md", "report.html"])
            self.assertEqual(json.loads(paths[0].read_text(encoding="utf-8")), data)
            for path in paths:
                self.assertIn("你好，世界", path.read_text(encoding="utf-8"))
            self.assertNotIn("\\u4f60", paths[0].read_text(encoding="utf-8"))

    def test_html_escapes_all_untrusted_content(self):
        attack = '</pre><script>alert("x")</script><img src=x onerror=alert(1)>'
        result = case(attack, passed=False, output=attack)
        result.update(prompt=attack, error=attack)
        result["checks"] = [{"type": attack, "passed": False, "message": attack}]
        data = report(result)
        data.update(suite=attack, provider=attack, model=attack)
        data["comparison"] = {key: [attack] for key in ("regressions", "improvements", "added", "removed", "changed_outputs", "incompatible")}
        with tempfile.TemporaryDirectory() as directory:
            write_reports(data, Path(directory))
            rendered = (Path(directory) / "report.html").read_text(encoding="utf-8")
        self.assertNotIn(attack, rendered)
        self.assertIn("&lt;script&gt;", rendered)
        parser = TagCollector()
        parser.feed(rendered)
        self.assertNotIn("script", parser.tags)
        self.assertNotIn("img", parser.tags)
        self.assertFalse(any(key.startswith("on") for key, _ in parser.attributes))
        self.assertFalse(any(key in ("src", "srcdoc") for key, _ in parser.attributes))
        self.assertIn("default-src", rendered)

    def test_markdown_uses_fences_that_output_cannot_close(self):
        attack = "```\n# injected\n<script>bad</script>\n````"
        result = case("name|table\n# heading", output=attack)
        result["checks"][0]["message"] = "<img src=x>|bad\nrow"
        with tempfile.TemporaryDirectory() as directory:
            write_reports(report(result), Path(directory))
            rendered = (Path(directory) / "report.md").read_text(encoding="utf-8")
        self.assertIn("`````text\n" + attack + "\n`````", rendered)
        self.assertIn(r"name\|table \# heading", rendered)
        self.assertIn(r"&lt;img src=x&gt;\|bad row", rendered)

    def test_comparison_detects_regression_improvement_added_and_removed(self):
        baseline = report(case("regress"), case("improve", passed=False), case("removed"))
        current = report(case("regress", passed=False, output="bad"), case("improve"), case("added"))
        comparison = compare_reports(baseline, current)
        self.assertEqual(comparison, {
            "regressions": ["regress"],
            "improvements": ["improve"],
            "added": ["added"],
            "removed": ["removed"],
            "changed_outputs": ["regress"],
            "incompatible": [],
        })

    def test_changed_checks_are_incompatible(self):
        baseline = report(case())
        current = report(case(passed=False, signature="b" * 64, output="new output"))
        comparison = compare_reports(baseline, current)
        self.assertEqual(comparison["incompatible"], ["greeting"])
        self.assertEqual(comparison["regressions"], [])
        self.assertEqual(comparison["changed_outputs"], ["greeting"])

    def test_cannot_compare_different_suites(self):
        baseline = report(case())
        current = report(case())
        current["suite"] = "another-suite"
        with self.assertRaisesRegex(ConfigError, "different suites"):
            compare_reports(baseline, current)

    def test_duplicate_case_ids_are_invalid(self):
        with self.assertRaisesRegex(ConfigError, "duplicate case ID"):
            compare_reports(report(case(), case()), report(case()))

    def test_empty_report_is_valid(self):
        data = report()
        self.assertIs(validate_report(data), data)
        self.assertTrue(all(not ids for ids in compare_reports(data, data).values()))
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(len(write_reports(data, Path(directory))), 3)

    def test_error_report_is_valid(self):
        result = case(passed=False)
        result["error"] = "connection failed"
        result["checks"] = []
        with tempfile.TemporaryDirectory() as directory:
            write_reports(report(result), Path(directory))

    def test_invalid_schema_fields_are_rejected_for_either_report(self):
        valid = report(case())
        mutations = [
            lambda data: data.pop("suite"),
            lambda data: data.update(schema_version=True),
            lambda data: data.update(schema_version=2),
            lambda data: data.update(suite=""),
            lambda data: data.update(created_at="not a date"),
            lambda data: data.update(model=123),
            lambda data: data.update(results={}),
            lambda data: data["summary"].update(total=99),
            lambda data: data["summary"].update(errors=True),
            lambda data: data["results"][0].update(passed="false"),
            lambda data: data["results"][0].update(error=4),
            lambda data: data["results"][0].update(error="cannot pass with an error"),
            lambda data: data["results"][0].update(latency_ms=float("nan")),
            lambda data: data["results"][0].update(latency_ms=float("inf")),
            lambda data: data["results"][0].update(latency_ms=True),
            lambda data: data["results"][0].update(latency_ms=10 ** 1000),
            lambda data: data["results"][0].update(check_signature="invalid"),
            lambda data: data["results"][0].update(output=None),
            lambda data: data["results"][0].update(checks=[{}]),
            lambda data: data["results"][0]["usage"].update(input_tokens=-1),
            lambda data: data["results"][0]["checks"][0].update(passed=1),
            lambda data: data["results"][0]["checks"][0].update(passed=False),
            lambda data: data.update(comparison={}),
        ]
        for mutate in mutations:
            invalid = deepcopy(valid)
            mutate(invalid)
            for baseline, current in ((invalid, valid), (valid, invalid)):
                with self.subTest(mutation=mutate, baseline=baseline is invalid):
                    with self.assertRaises(ConfigError):
                        compare_reports(baseline, current)

    def test_malformed_top_level_input_raises_config_error(self):
        for invalid in (None, [], "report", 1):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ConfigError):
                    compare_reports(invalid, report())

    def test_unknown_nonfinite_json_data_is_not_written(self):
        data = report(case())
        data["extra"] = float("nan")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ConfigError, "serialized"):
                write_reports(data, Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
