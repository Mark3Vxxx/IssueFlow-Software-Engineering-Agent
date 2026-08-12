"""Schema-validated model responses with normalized error handling."""

from __future__ import annotations

import json
from typing import Generic, Protocol, TypeVar

import httpx
from pydantic import BaseModel

from issueflow.models import Usage

T = TypeVar("T", bound=BaseModel)

MODEL_PRICES_PER_MILLION = {
    "deepseek-v4-flash": {"cache_hit": 0.0028, "cache_miss": 0.14, "output": 0.28},
    "deepseek-v4-pro": {"cache_hit": 0.003625, "cache_miss": 0.435, "output": 0.87},
}


def estimate_cost(model: str, usage: dict[str, object]) -> float:
    """Estimate a DeepSeek request cost from vendor token-usage fields."""
    prices = MODEL_PRICES_PER_MILLION.get(model)
    if prices is None:
        return 0.0
    input_tokens = _token_count(usage, "prompt_tokens")
    cache_hit = _token_count(usage, "prompt_cache_hit_tokens")
    cache_miss = _token_count(usage, "prompt_cache_miss_tokens", input_tokens - cache_hit)
    output_tokens = _token_count(usage, "completion_tokens")
    return (
        cache_hit * prices["cache_hit"]
        + cache_miss * prices["cache_miss"]
        + output_tokens * prices["output"]
    ) / 1_000_000


def _token_count(usage: dict[str, object], key: str, default: int = 0) -> int:
    """Return a non-negative token count without exposing provider payloads."""
    try:
        return max(0, int(usage.get(key, default)))
    except (TypeError, ValueError):
        return max(0, default)


class StructuredCompletion(BaseModel, Generic[T]):
    """A locally validated value and the usage consumed to obtain it."""

    value: T
    usage: Usage


class StructuredModel(Protocol):
    """Dependency-neutral interface for schema-constrained model requests."""

    def complete(
        self,
        system_prompt: str,
        payload: dict[str, object],
        schema: type[T],
    ) -> StructuredCompletion[T]: ...


class ModelProtocolError(RuntimeError):
    """A normalized model-boundary failure that retains chargeable usage only."""

    def __init__(self, normalized_message: str, usage: Usage) -> None:
        super().__init__(normalized_message)
        self.usage = usage


class DeepSeekStructuredModel:
    """DeepSeek adapter that requires a locally validated JSON response."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        http_client: httpx.Client | None = None,
        temperature: float = 0.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.http_client = http_client or httpx.Client(timeout=60)
        self.temperature = temperature

    def complete(
        self,
        system_prompt: str,
        payload: dict[str, object],
        schema: type[T],
    ) -> StructuredCompletion[T]:
        """Request one JSON object and validate it without retaining raw output on errors."""
        response: httpx.Response | None = None
        try:
            response = self.http_client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": json.dumps(
                                payload, ensure_ascii=False, separators=(",", ":")
                            ),
                        },
                    ],
                    "response_format": {"type": "json_object"},
                    "thinking": {"type": "disabled"},
                    "stream": False,
                    "temperature": self.temperature,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError:
            response = None

        if response is None:
            raise ModelProtocolError("model_request_failed", Usage(model_calls=1))

        response_payload = self._response_payload(response)
        usage = self._usage(response_payload)
        try:
            content = response_payload["choices"][0]["message"]["content"]
            value = schema.model_validate_json(content)
        except (IndexError, KeyError, TypeError, ValueError):
            raise ModelProtocolError("invalid_structured_response", usage) from None
        return StructuredCompletion(value=value, usage=usage)

    @staticmethod
    def _response_payload(response: httpx.Response) -> dict[str, object]:
        """Decode a provider response without allowing decode details into errors."""
        try:
            payload = response.json()
        except (ValueError, TypeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _usage(self, payload: dict[str, object]) -> Usage:
        """Extract normalized chargeable usage from a successful provider response."""
        raw_usage = payload.get("usage")
        usage_fields = raw_usage if isinstance(raw_usage, dict) else {}
        return Usage(
            model_calls=1,
            input_tokens=_token_count(usage_fields, "prompt_tokens"),
            output_tokens=_token_count(usage_fields, "completion_tokens"),
            cost_usd=estimate_cost(self.model, usage_fields),
        )
