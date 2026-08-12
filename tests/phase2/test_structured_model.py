import json

import httpx
import pytest
from pydantic import BaseModel

from issueflow.structured_model import DeepSeekStructuredModel, ModelProtocolError


class PlanAnswer(BaseModel):
    summary: str
    steps: list[str]


def _response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}}],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 30,
                "prompt_cache_hit_tokens": 20,
                "prompt_cache_miss_tokens": 100,
            },
        },
    )


def test_structured_model_validates_json_and_returns_usage():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url == "https://example/chat/completions"
        assert request.headers["authorization"] == "Bearer secret"
        assert payload["model"] == "deepseek-v4-flash"
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["stream"] is False
        assert payload["temperature"] == 0.0
        assert payload["messages"] == [
            {"role": "system", "content": "plan"},
            {"role": "user", "content": '{"issue":"bug"}'},
        ]
        return _response('{"summary":"fix gradient","steps":["inspect","test"]}')

    client = DeepSeekStructuredModel(
        "secret",
        "deepseek-v4-flash",
        "https://example",
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.complete("plan", {"issue": "bug"}, PlanAnswer)

    assert result.value == PlanAnswer(summary="fix gradient", steps=["inspect", "test"])
    assert result.usage.input_tokens == 120
    assert result.usage.output_tokens == 30
    assert result.usage.model_calls == 1
    assert result.usage.cost_usd == pytest.approx(0.000022456, abs=1e-15)


def test_structured_model_rejects_invalid_schema_without_leaking_response_or_secret():
    secret = "top-secret"
    raw_content = '{"summary": 123, "steps": "not-a-list"}'
    client = DeepSeekStructuredModel(
        secret,
        "deepseek-v4-flash",
        "https://example",
        httpx.Client(transport=httpx.MockTransport(lambda request: _response(raw_content))),
    )

    with pytest.raises(ModelProtocolError, match="invalid_structured_response") as caught:
        client.complete("plan", {"issue": "bug"}, PlanAnswer)

    assert caught.value.usage.input_tokens == 120
    assert caught.value.usage.output_tokens == 30
    assert raw_content not in str(caught.value)
    assert secret not in str(caught.value)


def test_structured_model_normalizes_http_failures_without_retaining_secret():
    secret = "top-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {secret}"
        raise httpx.ConnectError("request failed", request=request)

    client = DeepSeekStructuredModel(
        secret,
        "deepseek-v4-flash",
        "https://example",
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(ModelProtocolError, match="model_request_failed") as caught:
        client.complete("plan", {"issue": "bug"}, PlanAnswer)

    assert caught.value.usage.model_calls == 1
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert secret not in str(caught.value)


def test_structured_model_counts_http_status_failures_as_model_calls():
    client = DeepSeekStructuredModel(
        "secret",
        "deepseek-v4-flash",
        "https://example",
        httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503))),
    )

    with pytest.raises(ModelProtocolError, match="model_request_failed") as caught:
        client.complete("plan", {"issue": "bug"}, PlanAnswer)

    assert caught.value.usage.model_calls == 1
