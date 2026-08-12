import json

import httpx
from pydantic import BaseModel

from issueflow.models import Usage
from issueflow.reviewer import DeepSeekReviewClient, Reviewer
from issueflow.structured_model import ModelProtocolError, StructuredCompletion


class AdvisoryPayload(BaseModel):
    status: str
    reasons: list[str]


class StaticReviewModel:
    def __init__(self, response: str, usage: Usage | None = None) -> None:
        self.response = response
        self.usage = usage or Usage(model_calls=1)
        self.calls = 0

    def review(self, issue: str, diff_text: str):
        self.calls += 1
        return StructuredCompletion(
            value=AdvisoryPayload.model_validate_json(self.response),
            usage=self.usage,
        )


class FailingReviewModel:
    def review(self, issue: str, diff_text: str):
        raise ModelProtocolError(
            "reviewer_request_failed",
            Usage(model_calls=1, input_tokens=7, cost_usd=0.00001),
        )


class InvalidParsedReviewModel:
    def __init__(self, usage: Usage) -> None:
        self.usage = usage

    def review(self, issue: str, diff_text: str):
        return StructuredCompletion(
            value=AdvisoryPayload(status="invalid", reasons=["provider-secret-detail"]),
            usage=self.usage,
        )


def test_functional_success_requires_all_deterministic_evidence():
    result = Reviewer().evaluate_deterministic(
        reproduction_exit_code=1,
        verification_exit_code=0,
        diff_text="diff --git a/micrograd/engine.py b/micrograd/engine.py",
        budget_exhausted=False,
    )

    assert result.functional_success is True
    assert result.reasons == []


def test_deterministic_review_reports_each_missing_requirement():
    result = Reviewer().evaluate_deterministic(
        reproduction_exit_code=0,
        verification_exit_code=1,
        diff_text="",
        budget_exhausted=True,
    )

    assert result.functional_success is False
    assert result.reasons == [
        "reproduction_did_not_fail",
        "patch_is_empty",
        "verification_failed",
        "budget_exhausted",
    ]


def test_llm_rejection_does_not_override_functional_success():
    model = StaticReviewModel('{"status":"needs_changes","reasons":["Prefer a smaller diff."]}')
    reviewer = Reviewer(review_model=model)

    result = reviewer.evaluate(
        issue="Unary negation returns the wrong value.",
        reproduction_exit_code=1,
        verification_exit_code=0,
        diff_text="diff --git a/micrograd/engine.py b/micrograd/engine.py",
        budget_exhausted=False,
    )

    assert result.functional_success is True
    assert result.status == "needs_changes"
    assert result.reasons == ["Prefer a smaller diff."]
    assert model.calls == 1


def test_invalid_reviewer_json_is_failed_without_losing_functional_success():
    class InvalidReviewModel:
        def review(self, issue: str, diff_text: str):
            raise ModelProtocolError(
                "invalid_reviewer_response",
                Usage(model_calls=1, input_tokens=19, output_tokens=3, cost_usd=0.00002),
            )

    reviewer = Reviewer(review_model=InvalidReviewModel())

    result = reviewer.evaluate(
        issue="Unary negation returns the wrong value.",
        reproduction_exit_code=1,
        verification_exit_code=0,
        diff_text="diff --git a/micrograd/engine.py b/micrograd/engine.py",
        budget_exhausted=False,
    )

    assert result.functional_success is True
    assert result.status == "failed"
    assert result.reasons == ["invalid_reviewer_response"]
    assert result.usage == Usage(
        model_calls=1,
        input_tokens=19,
        output_tokens=3,
        cost_usd=0.00002,
    )


def test_invalid_parsed_reviewer_completion_preserves_every_usage_field():
    usage = Usage(
        model_calls=1,
        tool_calls=2,
        patch_attempts=3,
        input_tokens=19,
        output_tokens=3,
        cost_usd=0.25,
        duration_ms=17,
    )
    reviewer = Reviewer(review_model=InvalidParsedReviewModel(usage))

    result = reviewer.evaluate(
        issue="Unary negation returns the wrong value.",
        reproduction_exit_code=1,
        verification_exit_code=0,
        diff_text="diff --git a/micrograd/engine.py b/micrograd/engine.py",
        budget_exhausted=False,
    )

    assert result.functional_success is True
    assert result.status == "failed"
    assert result.reasons == ["invalid_reviewer_response"]
    assert result.usage == usage
    assert "provider-secret-detail" not in result.model_dump_json()


def test_deepseek_review_client_requests_structured_json():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer test-key"
        assert payload["model"] == "deepseek-v4-flash"
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["thinking"] == {"type": "disabled"}
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"status":"approved","reasons":["Focused fix."]}'}}
                ]
            },
        )

    client = DeepSeekReviewClient(
        api_key="test-key",
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    completion = client.review("Negation is broken", "diff --git a/a.py b/a.py")

    assert completion.value.status == "approved"
    assert completion.usage.model_calls == 1


def test_reviewer_network_failure_does_not_override_functional_success():
    reviewer = Reviewer(review_model=FailingReviewModel())

    result = reviewer.evaluate(
        issue="Unary negation returns the wrong value.",
        reproduction_exit_code=1,
        verification_exit_code=0,
        diff_text="diff --git a/micrograd/engine.py b/micrograd/engine.py",
        budget_exhausted=False,
    )

    assert result.functional_success is True
    assert result.status == "failed"
    assert result.reasons == ["reviewer_request_failed"]
    assert result.usage.input_tokens == 7
    assert result.usage.cost_usd == 0.00001
