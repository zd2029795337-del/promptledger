"""Provider contract tests; all HTTP interactions are mocked."""

import http.client
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

from promptledger.errors import ConfigError, ProviderError
from promptledger.providers import OpenAIProvider, ReplayProvider, _NoRedirect


class ReplayProviderTests(unittest.TestCase):
    def setUp(self):
        guard = patch(
            "urllib.request.OpenerDirector.open",
            side_effect=AssertionError("An offline test attempted an HTTP request"),
        )
        guard.start()
        self.addCleanup(guard.stop)

    def test_replay_returns_recorded_text_with_zero_usage(self):
        responses = {"one": "人工编写的示例"}
        provider = ReplayProvider(responses, ["one"])
        responses["one"] = "changed outside the provider"
        self.assertEqual(
            provider.generate({"id": "one", "prompt": "unused"}, "unused"),
            {"text": "人工编写的示例", "usage": {"input_tokens": 0, "output_tokens": 0}},
        )

    def test_replay_loads_utf8_file(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "responses.json"
            fixture.write_text('{"one": "离线回复"}', encoding="utf-8")
            provider = ReplayProvider.from_file(fixture, ["one"])
            self.assertEqual(provider.generate({"id": "one"}, "")["text"], "离线回复")

    def test_replay_rejects_missing_extra_or_nonstring_responses(self):
        for responses in ({}, {"one": "ok", "extra": "unused"}, {"one": 7}, ["ok"]):
            with self.subTest(responses=responses), self.assertRaises(ConfigError):
                ReplayProvider(responses, ["one"])


class OpenAIProviderTests(unittest.TestCase):
    case = {"id": "one", "prompt": "请输出一个标题"}
    fake_key = "sk-test-not-a-real-secret"

    def setUp(self):
        guard = patch(
            "urllib.request.OpenerDirector.open",
            side_effect=AssertionError("An offline test attempted an HTTP request"),
        )
        guard.start()
        self.addCleanup(guard.stop)

    def make_provider(self):
        provider = OpenAIProvider(
            "example-model", api_key=self.fake_key, timeout=12, max_output_tokens=128
        )
        provider._opener = Mock()
        return provider

    @staticmethod
    def completed(text="ok"):
        return {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
            "usage": {"input_tokens": 13, "output_tokens": 4},
        }

    def mock_response(self, provider, data):
        response = Mock()
        response.read.return_value = json.dumps(data, ensure_ascii=False).encode("utf-8")
        provider._opener.open.return_value.__enter__ = Mock(return_value=response)
        provider._opener.open.return_value.__exit__ = Mock(return_value=False)
        return response

    def test_reasoning_before_multiple_messages_does_not_hide_output_text(self):
        provider = self.make_provider()
        data = self.completed()
        data["output"] = [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [
                {"type": "output_text", "text": "First "},
                {"type": "output_text", "text": "message. "},
            ]},
            {"type": "message", "content": [{"type": "output_text", "text": "第二条消息。"}]},
        ]
        self.mock_response(provider, data)
        result = provider.generate(self.case, "Keep supplied facts.")
        self.assertEqual(result["text"], "First message. 第二条消息。")
        self.assertEqual(result["usage"], {"input_tokens": 13, "output_tokens": 4})
        provider._opener.open.assert_called_once()
        request = provider._opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + self.fake_key)
        self.assertEqual(provider._opener.open.call_args.kwargs, {"timeout": 12})
        body = json.loads(request.data)
        self.assertEqual(body, {
            "model": "example-model", "input": self.case["prompt"],
            "store": False, "max_output_tokens": 128,
            "instructions": "Keep supplied facts.",
        })
        self.assertNotIn(self.fake_key, request.data.decode("utf-8"))

    def test_incomplete_response_is_not_graded_as_partial_success(self):
        data = self.completed("apparently passing output")
        data["status"] = "incomplete"
        data["incomplete_details"] = {"reason": "max_output_tokens"}
        with self.assertRaisesRegex(ProviderError, "did not complete"):
            OpenAIProvider._parse(data)

    def test_refusal_after_text_is_not_graded_as_success(self):
        data = self.completed("passing-looking prefix")
        data["output"][0]["content"].append({"type": "refusal", "refusal": "declined"})
        with self.assertRaisesRegex(ProviderError, "declined"):
            OpenAIProvider._parse(data)

    def test_invalid_usage_is_rejected_instead_of_silently_counted_as_zero(self):
        invalid = [None, {}, {"input_tokens": 1},
                   {"input_tokens": True, "output_tokens": 1},
                   {"input_tokens": 1, "output_tokens": -1},
                   {"input_tokens": 1.5, "output_tokens": 2},
                   {"input_tokens": 1, "output_tokens": "2"}]
        for usage in invalid:
            with self.subTest(usage=usage), self.assertRaisesRegex(ProviderError, "token usage"):
                data = self.completed()
                data["usage"] = usage
                OpenAIProvider._parse(data)

    def test_malformed_or_empty_output_is_a_provider_error(self):
        outputs = [None, [], [{"type": "reasoning", "summary": []}],
                   [{"type": "message", "content": "not an array"}],
                   [{"type": "message", "content": [None]}],
                   [{"type": "message", "content": [{"type": "output_text", "text": 4}]}]]
        for output in outputs:
            with self.subTest(output=output), self.assertRaises(ProviderError):
                data = self.completed()
                data["output"] = output
                OpenAIProvider._parse(data)

    def test_http_errors_redact_body_reason_and_secret_without_retry(self):
        for status in (401, 403, 429, 500):
            with self.subTest(status=status):
                provider = self.make_provider()
                body = io.BytesIO((self.fake_key + " private prompt in body").encode())
                error = urllib.error.HTTPError(
                    provider.endpoint, status, self.fake_key + " private reason", {}, body
                )
                provider._opener.open.side_effect = error
                with self.assertRaises(ProviderError) as caught:
                    provider.generate(self.case, "")
                self.assertIn(f"HTTP {status}", str(caught.exception))
                self.assertIn("No automatic retry", str(caught.exception))
                self.assertNotIn(self.fake_key, str(caught.exception))
                self.assertNotIn("private", str(caught.exception))
                self.assertTrue(body.closed)
                provider._opener.open.assert_called_once()

    def test_network_failure_redacts_exception_text_and_does_not_retry(self):
        provider = self.make_provider()
        provider._opener.open.side_effect = urllib.error.URLError(self.fake_key)
        with self.assertRaises(ProviderError) as caught:
            provider.generate(self.case, "")
        self.assertNotIn(self.fake_key, str(caught.exception))
        self.assertIn("No automatic retry", str(caught.exception))
        provider._opener.open.assert_called_once()

    def test_invalid_http_json_is_a_sanitized_provider_error(self):
        provider = self.make_provider()
        response = self.mock_response(provider, self.completed())
        response.read.return_value = ("not JSON: " + self.fake_key).encode()
        with self.assertRaisesRegex(ProviderError, "invalid JSON") as caught:
            provider.generate(self.case, "")
        self.assertNotIn(self.fake_key, str(caught.exception))
        provider._opener.open.assert_called_once()

    def test_truncated_http_body_is_a_sanitized_provider_error_without_retry(self):
        provider = self.make_provider()
        response = self.mock_response(provider, self.completed())
        response.read.side_effect = http.client.IncompleteRead(
            ("partial response " + self.fake_key).encode(), 50
        )
        with self.assertRaises(ProviderError) as caught:
            provider.generate(self.case, "")
        self.assertNotIn(self.fake_key, str(caught.exception))
        self.assertNotIn("partial response", str(caught.exception))
        self.assertIn("No automatic retry", str(caught.exception))
        provider._opener.open.assert_called_once()

    def test_missing_key_fails_before_building_a_network_client(self):
        with patch.dict(os.environ, {}, clear=True), patch("urllib.request.build_opener") as build:
            with self.assertRaisesRegex(ConfigError, "OPENAI_API_KEY"):
                OpenAIProvider("example-model")
            build.assert_not_called()

    def test_invalid_configuration_fails_before_building_a_network_client(self):
        values = [
            {"model": ""}, {"model": "example", "timeout": 0},
            {"model": "example", "timeout": float("nan")},
            {"model": "example", "timeout": True},
            {"model": "example", "max_output_tokens": 15},
            {"model": "example", "max_output_tokens": True},
            {"model": "example", "api_key": "key\ninvalid"},
        ]
        for arguments in values:
            with self.subTest(arguments=arguments), patch("urllib.request.build_opener") as build:
                arguments = {"api_key": self.fake_key, **arguments}
                with self.assertRaises(ConfigError):
                    OpenAIProvider(**arguments)
                build.assert_not_called()

    def test_redirect_handler_never_forwards_the_authorization_request(self):
        self.assertIsNone(_NoRedirect().redirect_request(
            Mock(), None, 302, "Found", {}, "https://unrelated.invalid/collect"
        ))


if __name__ == "__main__":
    unittest.main()
