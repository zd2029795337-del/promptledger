"""Offline replay and an explicitly selected OpenAI Responses API adapter."""

from __future__ import annotations

import http.client
import json
import math
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .errors import ConfigError, ProviderError
from .suite import load_json


class ReplayProvider:
    """Return recorded or hand-authored outputs; never performs network I/O."""

    def __init__(self, responses: dict[str, str], case_ids: list[str]):
        if not isinstance(responses, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in responses.items()
        ):
            raise ConfigError("Replay responses must be an object mapping case IDs to strings.")
        missing = sorted(set(case_ids) - responses.keys())
        extra = sorted(responses.keys() - set(case_ids))
        if missing or extra:
            raise ConfigError(f"Replay IDs must match the suite (missing={missing}, extra={extra}).")
        self.responses = dict(responses)

    @classmethod
    def from_file(cls, path: str | Path, case_ids: list[str]) -> ReplayProvider:
        return cls(load_json(path), case_ids)

    def generate(self, case: dict, instructions: str) -> dict:
        return {
            "text": self.responses[case["id"]],
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a bearer credential to a redirect target.
        return None


class OpenAIProvider:
    """One paid request per case, no automatic retries and no tool execution."""

    endpoint = "https://api.openai.com/v1/responses"

    def __init__(
        self,
        model: str,
        *,
        timeout: float = 60.0,
        max_output_tokens: int = 1024,
        api_key: str | None = None,
    ):
        if not isinstance(model, str) or not model.strip():
            raise ConfigError("OpenAI requires --model or the OPENAI_MODEL environment variable.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ConfigError("Timeout must be a positive finite number.")
        if type(max_output_tokens) is not int or max_output_tokens < 16:
            raise ConfigError("max_output_tokens must be an integer of at least 16.")
        key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
        if not isinstance(key, str) or not key.strip():
            raise ConfigError("Set OPENAI_API_KEY before selecting --provider openai.")
        if any(ord(char) < 33 or ord(char) > 126 for char in key):
            raise ConfigError("OPENAI_API_KEY contains invalid characters.")
        self.model = model.strip()
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens
        self._api_key = key
        self._opener = urllib.request.build_opener(_NoRedirect())

    def generate(self, case: dict, instructions: str) -> dict:
        body = {
            "model": self.model,
            "input": case["prompt"],
            "store": False,
            "max_output_tokens": self.max_output_tokens,
        }
        if instructions:
            body["instructions"] = instructions
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + self._api_key,
                "Content-Type": "application/json",
                "User-Agent": "PromptLedger/0.1.0",
            },
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(8_000_001)
            if len(raw) > 8_000_000:
                raise ProviderError("OpenAI response exceeded the 8 MB response limit.")
            data = json.loads(raw)
        except urllib.error.HTTPError as error:
            error.close()
            hints = {
                401: "Check OPENAI_API_KEY.",
                403: "Check project permissions and model access.",
                429: "Check rate limits and available API quota.",
            }
            hint = hints.get(error.code, "Check model access and retry manually if appropriate.")
            raise ProviderError(f"OpenAI HTTP {error.code}. {hint} No automatic retry was made.") from None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
            raise ProviderError("OpenAI network request failed or timed out. No automatic retry was made.") from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ProviderError("OpenAI returned an invalid JSON response.") from None
        return self._parse(data)

    @staticmethod
    def _parse(data: Any) -> dict:
        if not isinstance(data, dict):
            raise ProviderError("OpenAI returned an unexpected response shape.")
        if data.get("status") != "completed":
            raise ProviderError("OpenAI response did not complete; increase the output limit or check model access.")
        output = data.get("output")
        if not isinstance(output, list):
            raise ProviderError("OpenAI response has no valid output list.")
        texts = []
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                raise ProviderError("OpenAI message has invalid content.")
            for part in content:
                if not isinstance(part, dict):
                    raise ProviderError("OpenAI returned an invalid content part.")
                if part.get("type") == "refusal":
                    raise ProviderError("OpenAI declined this request; no grade was inferred from the refusal.")
                if part.get("type") == "output_text":
                    if not isinstance(part.get("text"), str):
                        raise ProviderError("OpenAI returned an invalid text part.")
                    texts.append(part["text"])
        if not texts:
            raise ProviderError("OpenAI response contained no text to evaluate.")
        usage = data.get("usage")
        if not isinstance(usage, dict) or any(
            type(usage.get(key)) is not int or usage[key] < 0
            for key in ("input_tokens", "output_tokens")
        ):
            raise ProviderError("OpenAI response contained invalid token usage.")
        return {
            "text": "".join(texts),
            "usage": {key: usage[key] for key in ("input_tokens", "output_tokens")},
        }
