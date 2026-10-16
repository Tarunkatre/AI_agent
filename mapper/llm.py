from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Protocol

from .models import Proposal
from .tiers import LLM


class LLMError(Exception):
    pass


class LLMClient(Protocol):
    provider: str
    model: str

    def propose(self, account_code: str, account_name: str, coa_index: dict[str, Any]) -> Proposal:
        ...


def _parse_proposal(payload: dict[str, Any], coa_index: dict[str, Any]) -> Proposal:
    target = str(payload.get("target_code", "")).strip()
    confidence = float(payload.get("confidence", 0.0))
    rationale = str(payload.get("rationale", "")).strip()
    if not target:
        raise LLMError("missing target_code")
    if target not in coa_index:
        raise LLMError(f"target_code {target} is not a COA account")
    if not 0.0 <= confidence <= 1.0:
        raise LLMError("confidence out of range")
    return Proposal(tier=LLM, target_code=target, confidence=confidence, rationale=rationale)


class AnthropicClient:
    provider = "anthropic"
    endpoint = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def propose(self, account_code: str, account_name: str, coa_index: dict[str, Any]) -> Proposal:
        codes = sorted(coa_index.keys())
        names = ", ".join(f"{a.account_code}={a.account_name}" for a in coa_index.values())
        tool = {
            "name": "select_coa_account",
            "description": "Choose the best COA account_code for the unmapped trial-balance account.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "target_code": {"type": "string", "enum": codes},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": {"type": "string"},
                },
                "required": ["target_code", "confidence", "rationale"],
            },
        }
        body = {
            "model": self.model,
            "max_tokens": 512,
            "temperature": 0,
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": "select_coa_account"},
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"TB account {account_code} '{account_name}' is not present in the chart of "
                        f"accounts. Pick the single best COA account_code to map it to. COA: {names}"
                    ),
                }
            ],
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise LLMError(f"request failed: {exc}") from exc
        for block in data.get("content", []):
            if block.get("type") == "tool_use":
                return _parse_proposal(block.get("input", {}), coa_index)
        raise LLMError("model returned no tool_use block")


class FakeLLMClient:
    provider = "fake"
    model = "fake"

    def __init__(self, proposal: Proposal | None = None, error: str | None = None) -> None:
        self._proposal = proposal
        self._error = error

    def propose(self, account_code: str, account_name: str, coa_index: dict[str, Any]) -> Proposal:
        if self._error is not None:
            raise LLMError(self._error)
        if self._proposal is None:
            raise LLMError("no proposal configured")
        return self._proposal


class OpenAICompatibleClient:
    provider = "openai_compatible"

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def propose(self, account_code: str, account_name: str, coa_index: dict[str, Any]) -> Proposal:
        codes = sorted(coa_index.keys())
        names = ", ".join(f"{a.account_code}={a.account_name}" for a in coa_index.values())
        body = {
            "model": self.model,
            "temperature": 0,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "select_coa_account",
                        "description": "Choose the best COA account_code for the unmapped trial-balance account.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "target_code": {"type": "string", "enum": codes},
                                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                                "rationale": {"type": "string"},
                            },
                            "required": ["target_code", "confidence", "rationale"],
                        },
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": "select_coa_account"}},
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"TB account {account_code} '{account_name}' is not present in the chart of "
                        f"accounts. Pick the single best COA account_code to map it to. COA: {names}"
                    ),
                }
            ],
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"content-type": "application/json", "authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise LLMError(f"request failed: {exc}") from exc
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("response contained no choices")
        tool_calls = (choices[0].get("message") or {}).get("tool_calls") or []
        if not tool_calls:
            raise LLMError("model returned no tool_calls")
        try:
            payload = json.loads(tool_calls[0]["function"]["arguments"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise LLMError(f"malformed tool arguments: {exc}") from exc
        return _parse_proposal(payload, coa_index)


def create_client(model: str | None = None) -> LLMClient | None:
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        resolved = model or os.environ.get("ANTHROPIC_MODEL") or "claude-3-5-sonnet-20241022"
        return AnthropicClient(api_key=anthropic_key, model=resolved)

    base_url = os.environ.get("LLM_BASE_URL")
    api_key = os.environ.get("LLM_API_KEY")
    openai_model = model or os.environ.get("LLM_MODEL")
    if base_url and api_key and openai_model:
        return OpenAICompatibleClient(base_url=base_url, api_key=api_key, model=openai_model)

    return None

