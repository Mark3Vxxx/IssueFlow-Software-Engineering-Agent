"""Deterministic success checks and advisory model review."""

from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, Field

from issueflow.models import Usage
from issueflow.structured_model import (
    ModelProtocolError,
    StructuredCompletion,
    estimate_cost,
)


class DeterministicReview(BaseModel):
    """Evidence-based functional result that no model opinion can override."""

    functional_success: bool
    reasons: list[str]


class ReviewResult(BaseModel):
    """Combined functional result and advisory reviewer status."""

    functional_success: bool
    status: Literal["approved", "needs_changes", "failed", "skipped"]
    reasons: list[str]
    usage: Usage = Field(default_factory=Usage)


class AdvisoryReview(BaseModel):
    """Schema-constrained opinion returned by the outer review model."""

    status: Literal["approved", "needs_changes", "failed"]
    reasons: list[str]


class ReviewModel(Protocol):
    """Interface for a model that returns one structured review."""

    def review(
        self,
        issue: str,
        diff_text: str,
    ) -> StructuredCompletion[AdvisoryReview]: ...


class DeepSeekReviewClient:
    """Request a small structured advisory review from DeepSeek."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.http_client = http_client or httpx.Client(timeout=60)

    def review(
        self,
        issue: str,
        diff_text: str,
    ) -> StructuredCompletion[AdvisoryReview]:
        """Return a parsed advisory result and chargeable request usage."""
        try:
            response = self.http_client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Review the proposed software repair. Return a JSON object with "
                                "status equal to approved, needs_changes, or failed, and reasons "
                                "as an array of concise strings. Do not claim that review replaces "
                                "tests."
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"Issue:\n{issue}\n\nPatch:\n{diff_text}",
                        },
                    ],
                    "response_format": {"type": "json_object"},
                    "thinking": {"type": "disabled"},
                    "stream": False,
                    "max_tokens": 256,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError:
            raise ModelProtocolError(
                "reviewer_request_failed", Usage(model_calls=1)
            ) from None

        try:
            payload = response.json()
        except (TypeError, ValueError):
            payload = {}
        usage = self._usage(payload if isinstance(payload, dict) else {})
        try:
            content = payload["choices"][0]["message"]["content"]
            value = AdvisoryReview.model_validate_json(content)
        except (IndexError, KeyError, TypeError, ValueError):
            raise ModelProtocolError("invalid_reviewer_response", usage) from None
        return StructuredCompletion(value=value, usage=usage)

    def _usage(self, payload: dict[str, object]) -> Usage:
        raw_usage = payload.get("usage")
        usage = raw_usage if isinstance(raw_usage, dict) else {}
        input_tokens = _token_count(usage.get("prompt_tokens"))
        output_tokens = _token_count(usage.get("completion_tokens"))
        return Usage(
            model_calls=1,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=estimate_cost(self.model, usage),
        )


class Reviewer:
    """Apply hard functional gates before requesting advisory model feedback."""

    def __init__(self, review_model: ReviewModel | None = None) -> None:
        self.review_model = review_model

    def evaluate_deterministic(
        self,
        reproduction_exit_code: int,
        verification_exit_code: int,
        diff_text: str,
        budget_exhausted: bool,
    ) -> DeterministicReview:
        """Evaluate the four non-negotiable functional success conditions."""
        reasons: list[str] = []
        if reproduction_exit_code == 0:
            reasons.append("reproduction_did_not_fail")
        if not diff_text.strip():
            reasons.append("patch_is_empty")
        if verification_exit_code != 0:
            reasons.append("verification_failed")
        if budget_exhausted:
            reasons.append("budget_exhausted")
        return DeterministicReview(functional_success=not reasons, reasons=reasons)

    def evaluate(
        self,
        issue: str,
        reproduction_exit_code: int,
        verification_exit_code: int,
        diff_text: str,
        budget_exhausted: bool,
    ) -> ReviewResult:
        """Return deterministic success plus optional model review."""
        deterministic = self.evaluate_deterministic(
            reproduction_exit_code=reproduction_exit_code,
            verification_exit_code=verification_exit_code,
            diff_text=diff_text,
            budget_exhausted=budget_exhausted,
        )
        if not deterministic.functional_success or self.review_model is None:
            return ReviewResult(
                functional_success=deterministic.functional_success,
                status="skipped",
                reasons=deterministic.reasons,
            )

        usage = Usage(model_calls=1)
        try:
            completion = self.review_model.review(issue, diff_text)
            usage = completion.usage
            raw_review = completion.value
            if isinstance(raw_review, BaseModel):
                raw_review = raw_review.model_dump()
            model_review = AdvisoryReview.model_validate(raw_review)
        except ModelProtocolError as error:
            reason = str(error)
            if reason not in {"reviewer_request_failed", "invalid_reviewer_response"}:
                reason = "invalid_reviewer_response"
            return ReviewResult(
                functional_success=True,
                status="failed",
                reasons=[reason],
                usage=error.usage,
            )
        except httpx.HTTPError:
            return ReviewResult(
                functional_success=True,
                status="failed",
                reasons=["reviewer_request_failed"],
                usage=usage,
            )
        except (AttributeError, ValueError, TypeError):
            return ReviewResult(
                functional_success=True,
                status="failed",
                reasons=["invalid_reviewer_response"],
                usage=usage,
            )
        return ReviewResult(
            functional_success=True,
            status=model_review.status,
            reasons=model_review.reasons,
            usage=usage,
        )


def _token_count(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
