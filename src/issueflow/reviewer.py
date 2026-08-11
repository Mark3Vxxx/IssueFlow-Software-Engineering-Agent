"""Deterministic success checks and advisory model review."""

import json
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel


class DeterministicReview(BaseModel):
    """Evidence-based functional result that no model opinion can override."""

    functional_success: bool
    reasons: list[str]


class ReviewResult(BaseModel):
    """Combined functional result and advisory reviewer status."""

    functional_success: bool
    status: Literal["approved", "needs_changes", "failed", "skipped"]
    reasons: list[str]


class ReviewModel(Protocol):
    """Interface for a model that returns one structured review."""

    def review(self, issue: str, diff_text: str) -> str: ...


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

    def review(self, issue: str, diff_text: str) -> str:
        """Return the model's raw JSON text for local schema validation."""
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
                            "status equal to approved, needs_changes, or failed, and reasons as "
                            "an array of concise strings. Do not claim that review replaces tests."
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
        return response.json()["choices"][0]["message"]["content"]


class _ModelReview(BaseModel):
    status: Literal["approved", "needs_changes", "failed"]
    reasons: list[str]


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

        try:
            response = self.review_model.review(issue, diff_text)
            model_review = _ModelReview.model_validate(json.loads(response))
        except httpx.HTTPError:
            return ReviewResult(
                functional_success=True,
                status="failed",
                reasons=["reviewer_request_failed"],
            )
        except (json.JSONDecodeError, ValueError, TypeError):
            return ReviewResult(
                functional_success=True,
                status="failed",
                reasons=["invalid_reviewer_response"],
            )
        return ReviewResult(
            functional_success=True,
            status=model_review.status,
            reasons=model_review.reasons,
        )
