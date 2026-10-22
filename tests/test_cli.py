"""CLI integration and paid-provider preflight checks, entirely offline."""

from contextlib import redirect_stderr, redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from promptledger.cli import main
from promptledger.errors import ProviderError
from promptledger.providers import OpenAIProvider, ReplayProvider
from promptledger.runner import run_suite


class CLITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.suite_data = {
            "version": 1, "name": "CLI integration", "instructions": "Keep supplied facts.",
            "cases": [
                {"id": "first", "prompt": "Output ok.", "checks": [{"type": "equals", "value": "ok"}]},
                {"id": "second", "prompt": "Output ready.", "checks": [{"type": "equals", "value": "ready"}]},
            ],
        }
        self.suite = self.write_json("suite.json", self.suite_data)
        self.good = self.write_json("good.json", {"first": "ok", "second": "ready"})
        self.regressed = self.write_json("regressed.json", {"first": "ok", "second": "not ready"})
        self.baseline_data = run_suite(
            self.suite_data, ReplayProvider({"first": "ok", "second": "ready"}, ["first", "second"]), "replay"
        )
        self.baseline = self.write_json("baseline/report.json", self.baseline_data)
        guard = patch(
            "urllib.request.OpenerDirector.open",
            side_effect=AssertionError("An offline CLI test attempted an HTTP request"),
        )
        self.network = guard.start()
        self.addCleanup(guard.stop)

    def write_json(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def invoke(self, *arguments):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main([str(argument) for argument in arguments])
        return code, out.getvalue(), err.getvalue()

    def assert_preflight_rejected(self, *arguments, message):
        # Patch the actual imported class method as well as the CLI constructor:
        # either a construction or a generation would expose an ordering defect.
        with patch.object(OpenAIProvider, "generate") as generate, patch(
            "promptledger.cli.OpenAIProvider", wraps=OpenAIProvider
        ) as constructor:
            code, out, err = self.invoke(*arguments)
        self.assertEqual(code, 2, (out, err))
        self.assertIn(message, err)
        constructor.assert_not_called()
        generate.assert_not_called()
        self.network.assert_not_called()

    def test_validate_is_offline_and_does_not_require_provider_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            code, out, err = self.invoke("validate", self.suite)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("2 cases", out)
        self.network.assert_not_called()

    def test_good_and_regressed_runs_write_reports_and_a_comparable_diff(self):
        baseline_dir = self.root / "passing"
        code, out, err = self.invoke(
            "run", self.suite, "--responses", self.good, "--output", baseline_dir
        )
        self.assertEqual((code, err), (0, ""))
        self.assertIn("2/2 passed", out)
        baseline = baseline_dir / "report.json"
        current_dir = self.root / "current"
        code, out, err = self.invoke(
            "run", self.suite, "--responses", self.regressed,
            "--baseline", baseline, "--output", current_dir
        )
        self.assertEqual((code, err), (1, ""))
        self.assertIn("regressions: second", out)
        for directory in (baseline_dir, current_dir):
            for extension in ("json", "md", "html"):
                self.assertGreater((directory / f"report.{extension}").stat().st_size, 0)
        report = json.loads((current_dir / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["summary"], {
            "total": 2, "passed": 1, "failed": 1, "errors": 0,
            "input_tokens": 0, "output_tokens": 0,
        })
        self.assertEqual(report["comparison"]["regressions"], ["second"])
        self.assertEqual(report["comparison"]["changed_outputs"], ["second"])
        code, out, err = self.invoke("diff", baseline, current_dir / "report.json")
        self.assertEqual((code, err), (1, ""))
        self.assertIn("regressions: second", out)
        self.network.assert_not_called()

    def test_unchanged_baseline_passes_and_diff_is_offline(self):
        code, out, err = self.invoke(
            "run", self.suite, "--responses", self.good,
            "--baseline", self.baseline, "--output", self.root / "unchanged"
        )
        self.assertEqual((code, err), (0, ""))
        self.assertIn("regressions: -", out)
        code, out, err = self.invoke("diff", self.baseline, self.root / "unchanged/report.json")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("changed_outputs: -", out)
        self.network.assert_not_called()

    def test_replay_missing_or_extra_ids_fail_before_generation(self):
        for fixture in ({"first": "ok"}, {"first": "ok", "second": "ready", "extra": "unused"}):
            with self.subTest(fixture=fixture), patch.object(ReplayProvider, "generate") as generate:
                path = self.write_json("invalid-responses.json", fixture)
                code, out, err = self.invoke(
                    "run", self.suite, "--responses", path, "--output", self.root / "invalid-replay"
                )
                self.assertEqual(code, 2, (out, err))
                self.assertIn("Replay IDs must match", err)
                generate.assert_not_called()
        self.network.assert_not_called()

    def test_replay_requires_a_response_file(self):
        code, out, err = self.invoke("run", self.suite, "--output", self.root / "missing-replay")
        self.assertEqual(code, 2, (out, err))
        self.assertIn("Replay requires --responses", err)
        self.network.assert_not_called()

    def test_openai_requires_a_key_before_generation(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(OpenAIProvider, "generate") as generate:
            code, out, err = self.invoke(
                "run", self.suite, "--provider", "openai", "--model", "example-model",
                "--output", self.root / "no-key"
            )
        self.assertEqual(code, 2, (out, err))
        self.assertIn("OPENAI_API_KEY", err)
        generate.assert_not_called()
        self.network.assert_not_called()

    def test_invalid_suite_fails_before_paid_provider_is_constructed(self):
        data = copy.deepcopy(self.suite_data)
        data["cases"][0]["checks"] = [{"type": "unknown_check"}]
        bad_suite = self.write_json("invalid-suite.json", data)
        self.assert_preflight_rejected(
            "run", bad_suite, "--provider", "openai", "--model", "example-model",
            "--output", self.root / "invalid-suite-output", message="unsupported check type"
        )

    def test_removed_case_baseline_fails_before_paid_provider_is_constructed(self):
        data = copy.deepcopy(self.suite_data)
        data["cases"].pop()
        smaller = self.write_json("smaller-suite.json", data)
        self.assert_preflight_rejected(
            "run", smaller, "--provider", "openai", "--model", "example-model",
            "--baseline", self.baseline, "--output", self.root / "removed",
            message="Baseline cases were removed"
        )

    def test_changed_checks_fail_before_paid_provider_is_constructed(self):
        data = copy.deepcopy(self.suite_data)
        data["cases"][0]["checks"][0]["value"] = "a new criterion"
        changed = self.write_json("changed-suite.json", data)
        self.assert_preflight_rejected(
            "run", changed, "--provider", "openai", "--model", "example-model",
            "--baseline", self.baseline, "--output", self.root / "changed",
            message="Baseline checks changed"
        )

    def test_malformed_baseline_fails_before_paid_provider_is_constructed(self):
        bad_baseline = self.write_json("bad-baseline.json", {"suite": self.suite_data["name"]})
        self.assert_preflight_rejected(
            "run", self.suite, "--provider", "openai", "--model", "example-model",
            "--baseline", bad_baseline, "--output", self.root / "bad-baseline",
            message="schema_version"
        )

    def test_case_budget_is_checked_before_paid_provider_is_constructed(self):
        self.assert_preflight_rejected(
            "run", self.suite, "--provider", "openai", "--model", "example-model",
            "--max-cases", 1, "--output", self.root / "case-limit",
            message="--max-cases"
        )

    def test_baseline_cannot_be_overwritten_by_a_run(self):
        original = self.baseline.read_bytes()
        self.assert_preflight_rejected(
            "run", self.suite, "--provider", "openai", "--model", "example-model",
            "--baseline", self.baseline, "--output", self.baseline.parent,
            message="overwrite an input or baseline"
        )
        self.assertEqual(self.baseline.read_bytes(), original)

    def test_report_symlink_cannot_overwrite_the_baseline(self):
        output = self.root / "symlink-output"
        output.mkdir()
        try:
            (output / "report.json").symlink_to(self.baseline)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symbolic links are not available: {exc}")
        original = self.baseline.read_bytes()
        self.assert_preflight_rejected(
            "run", self.suite, "--provider", "openai", "--model", "example-model",
            "--baseline", self.baseline, "--output", output,
            message="overwrite an input or baseline"
        )
        self.assertEqual(self.baseline.read_bytes(), original)

    def test_safe_provider_errors_become_failed_reports_with_no_retry(self):
        # HTTPS client initialization reads OS proxy/certificate settings. Keep
        # the system environment while overriding only this test credential.
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-not-a-real-key"}), patch.object(
            OpenAIProvider, "generate", side_effect=ProviderError("Mocked provider unavailable.")
        ) as generate:
            code, out, err = self.invoke(
                "run", self.suite, "--provider", "openai", "--model", "example-model",
                "--output", self.root / "provider-errors"
            )
        self.assertEqual((code, err), (1, ""))
        self.assertIn("2 provider errors", out)
        self.assertEqual(generate.call_count, 2)  # One attempt per distinct case.
        report = json.loads((self.root / "provider-errors/report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["summary"]["errors"], 2)
        self.assertEqual(report["summary"]["input_tokens"], 0)
        self.assertTrue(all(result["error"] == "Mocked provider unavailable." for result in report["results"]))
        self.network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
