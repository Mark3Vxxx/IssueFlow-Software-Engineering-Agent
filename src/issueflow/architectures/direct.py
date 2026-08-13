"""One-shot Direct repair architecture with bounded repository context."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from time import monotonic

from pydantic import BaseModel

from issueflow.agent import ModelAction, ToolExecutor
from issueflow.architectures.base import (
    ArchitectureKind,
    ArchitectureResult,
    RoleName,
    RunContext,
)
from issueflow.models import AgentCaseView, BenchmarkCase, Budget, RunStatus, TraceStep, Usage
from issueflow.structured_model import ModelProtocolError, StructuredModel

MAX_REPOSITORY_PATHS = 120
MAX_FILE_PREVIEW_CHARS = 400
MAX_PAYLOAD_CHARS = 20_000
REPOSITORY_SUFFIXES = frozenset({".py", ".toml", ".yaml", ".md"})

SYSTEM_PROMPT = """You are the Direct software-repair baseline.
Return exactly one structured replacement for an existing workspace file. Use only the supplied
issue and bounded repository map. old_text must be a non-empty exact fragment that occurs once,
and new_text must be its complete replacement. Do not request tools and do not run tests.
"""


class DirectPatch(BaseModel):
    """One exact text replacement proposed by the Direct model call."""

    path: str
    old_text: str
    new_text: str
    explanation: str


class DirectArchitecture:
    """Generate and apply one patch without interactive tools or test feedback."""

    def __init__(
        self,
        model: StructuredModel,
        tools: ToolExecutor,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.model = model
        self.tools = tools
        self.clock = clock

    def run(
        self,
        case: BenchmarkCase,
        workspace: Path,
        budget: Budget,
        context: RunContext,
    ) -> ArchitectureResult:
        """Make one structured model request and one allowlisted patch attempt."""
        del context
        started_at = self.clock()
        steps: list[TraceStep] = []
        usage = Usage()

        def finish(
            status: RunStatus,
            stop_reason: str,
            *,
            final_message: str = "",
            elapsed_seconds: float | None = None,
        ) -> ArchitectureResult:
            elapsed = (
                max(0.0, self.clock() - started_at)
                if elapsed_seconds is None
                else max(0.0, elapsed_seconds)
            )
            total_usage = usage.model_copy(update={"duration_ms": int(elapsed * 1_000)})
            return ArchitectureResult(
                architecture=ArchitectureKind.DIRECT,
                status=status,
                stop_reason=stop_reason,
                steps=steps,
                usage=total_usage,
                role_usage={RoleName.DIRECT: total_usage},
                final_message=final_message,
            )

        if workspace.resolve() != self.tools.workspace:
            return finish(RunStatus.FAILED, "patch_application_failure")

        payload = _build_payload(case.agent_view(), workspace)
        elapsed = self.clock() - started_at
        if elapsed >= budget.max_seconds:
            return finish(
                RunStatus.TIMED_OUT,
                "time_budget_exhausted",
                elapsed_seconds=elapsed,
            )

        try:
            completion = self.model.complete(SYSTEM_PROMPT, payload, DirectPatch)
        except ModelProtocolError as error:
            usage = _model_usage(error.usage)
            elapsed = self.clock() - started_at
            budget_reason = _model_budget_reason(usage, budget)
            if budget_reason is not None:
                steps.append(
                    _model_step(
                        len(steps) + 1,
                        usage,
                        budget_reason,
                        "budget_exhausted",
                    )
                )
                return finish(
                    RunStatus.BUDGET_EXHAUSTED,
                    budget_reason,
                    elapsed_seconds=elapsed,
                )
            if elapsed >= budget.max_seconds:
                steps.append(
                    _model_step(
                        len(steps) + 1,
                        usage,
                        "time budget exhausted",
                        "timed_out",
                    )
                )
                return finish(
                    RunStatus.TIMED_OUT,
                    "time_budget_exhausted",
                    elapsed_seconds=elapsed,
                )
            steps.append(
                _model_step(
                    len(steps) + 1,
                    usage,
                    "model protocol failure",
                    "failed",
                )
            )
            return finish(
                RunStatus.FAILED,
                "model_protocol_failure",
                elapsed_seconds=elapsed,
            )

        usage = _model_usage(completion.usage)
        elapsed = self.clock() - started_at
        budget_reason = _model_budget_reason(usage, budget)
        if budget_reason is not None:
            steps.append(
                _model_step(
                    len(steps) + 1,
                    usage,
                    budget_reason,
                    "budget_exhausted",
                )
            )
            return finish(
                RunStatus.BUDGET_EXHAUSTED,
                budget_reason,
                elapsed_seconds=elapsed,
            )
        if elapsed >= budget.max_seconds:
            steps.append(
                _model_step(
                    len(steps) + 1,
                    usage,
                    "time budget exhausted",
                    "timed_out",
                )
            )
            return finish(
                RunStatus.TIMED_OUT,
                "time_budget_exhausted",
                elapsed_seconds=elapsed,
            )

        patch = completion.value
        steps.append(
            _model_step(
                len(steps) + 1,
                usage,
                patch.explanation,
                "completed",
            )
        )
        usage = usage.model_copy(update={"tool_calls": 1, "patch_attempts": 1})
        action = ModelAction(
            tool="apply_patch",
            arguments={
                "path": patch.path,
                "old_text": patch.old_text,
                "new_text": patch.new_text,
            },
        )
        remaining_seconds = max(1, int(budget.max_seconds - elapsed))
        try:
            observation = self.tools.execute(action, timeout_seconds=remaining_seconds)
        except TimeoutError:
            elapsed = self.clock() - started_at
            steps.append(
                _patch_step(
                    len(steps) + 1,
                    "time budget exhausted",
                    "timed_out",
                )
            )
            return finish(
                RunStatus.TIMED_OUT,
                "time_budget_exhausted",
                elapsed_seconds=elapsed,
            )
        except (
            FileNotFoundError,
            OSError,
            RuntimeError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
        ):
            elapsed = self.clock() - started_at
            steps.append(
                _patch_step(
                    len(steps) + 1,
                    "patch application failed",
                    "failed",
                )
            )
            return finish(
                RunStatus.FAILED,
                "patch_application_failure",
                elapsed_seconds=elapsed,
            )

        elapsed = self.clock() - started_at
        if elapsed >= budget.max_seconds:
            steps.append(
                _patch_step(
                    len(steps) + 1,
                    "time budget exhausted",
                    "timed_out",
                )
            )
            return finish(
                RunStatus.TIMED_OUT,
                "time_budget_exhausted",
                elapsed_seconds=elapsed,
            )
        steps.append(_patch_step(len(steps) + 1, observation, "completed"))
        return finish(
            RunStatus.SUCCEEDED,
            "patch_applied",
            final_message=patch.explanation,
            elapsed_seconds=elapsed,
        )


def _build_payload(view: AgentCaseView, workspace: Path) -> dict[str, object]:
    """Return deterministic repository context that fits all Direct input caps."""
    payload: dict[str, object] = {
        "issue": _bounded_issue(view.issue),
        "repository_map": [],
    }
    repository_map: list[dict[str, str]] = payload["repository_map"]  # type: ignore[assignment]
    root = workspace.resolve()
    candidates: list[tuple[str, Path]] = []
    for path in root.rglob("*"):
        try:
            relative = path.relative_to(root)
            if (
                path.is_symlink()
                or not path.is_file()
                or ".git" in relative.parts
                or path.suffix not in REPOSITORY_SUFFIXES
            ):
                continue
        except OSError:
            continue
        candidates.append((relative.as_posix(), path))

    for relative_path, path in sorted(candidates):
        if len(repository_map) >= MAX_REPOSITORY_PATHS:
            break
        try:
            with path.open(encoding="utf-8", errors="replace") as source:
                preview = source.read(MAX_FILE_PREVIEW_CHARS)
        except OSError:
            continue
        entry = {"path": relative_path, "preview": preview}
        repository_map.append(entry)
        if _payload_size(payload) > MAX_PAYLOAD_CHARS:
            repository_map.pop()
    return payload


def _bounded_issue(issue: str) -> str:
    """Keep even unusually large issue text inside the total request character cap."""
    empty_payload = {"issue": "", "repository_map": []}
    if _payload_size({"issue": issue, "repository_map": []}) <= MAX_PAYLOAD_CHARS:
        return issue
    low = 0
    high = len(issue)
    while low < high:
        middle = (low + high + 1) // 2
        empty_payload["issue"] = issue[:middle]
        if _payload_size(empty_payload) <= MAX_PAYLOAD_CHARS:
            low = middle
        else:
            high = middle - 1
    return issue[:low]


def _payload_size(payload: dict[str, object]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _model_usage(provider_usage: Usage) -> Usage:
    """Count the one attempted Direct request even for defensive test doubles."""
    return provider_usage.model_copy(update={"model_calls": 1})


def _model_budget_reason(usage: Usage, budget: Budget) -> str | None:
    if usage.input_tokens > budget.max_input_tokens:
        return "input_token_budget_exhausted"
    if usage.output_tokens > budget.max_output_tokens:
        return "output_token_budget_exhausted"
    if usage.cost_usd > budget.max_cost_usd:
        return "cost_budget_exhausted"
    return None


def _model_step(
    sequence: int,
    usage: Usage,
    output_summary: str,
    status: str,
) -> TraceStep:
    return TraceStep(
        sequence=sequence,
        role=RoleName.DIRECT,
        step_type="model",
        input_summary="issue and bounded repository map",
        output_summary=output_summary[:2_000],
        status=status,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=usage.cost_usd,
    )


def _patch_step(sequence: int, output_summary: str, status: str) -> TraceStep:
    return TraceStep(
        sequence=sequence,
        role=RoleName.DIRECT,
        step_type="tool",
        input_summary="apply_patch: structured replacement",
        output_summary=output_summary[:2_000],
        status=status,
    )
