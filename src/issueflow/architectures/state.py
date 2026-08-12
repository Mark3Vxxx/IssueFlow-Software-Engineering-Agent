"""Bounded structured outputs and shared state for multi-agent workflows."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, TypeAdapter
from typing_extensions import TypedDict

from issueflow.architectures.base import RoleName
from issueflow.models import Budget, Usage

MAX_PLAN_STEPS = 6
MAX_EVIDENCE_ITEMS = 20
MAX_EVIDENCE_SUMMARY_CHARS = 2_000
MAX_DIFF_CHARS = 20_000
MAX_PUBLIC_TEST_RESULT_CHARS = 2_000
MAX_ROLE_HISTORY = 50


class _StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetrievalToolCall(_StrictOutput):
    """One Retriever request constrained before it reaches ToolExecutor."""

    tool: Literal["search", "read_file"]
    arguments: dict[str, object] = Field(default_factory=dict)


class CoderToolCall(_StrictOutput):
    """One Coder request constrained before it reaches ToolExecutor."""

    tool: Literal["read_file", "apply_patch", "run_tests"]
    arguments: dict[str, object] = Field(default_factory=dict)


class PlanOutput(_StrictOutput):
    """Planner output kept small enough for every later role."""

    steps: list[str] = Field(max_length=MAX_PLAN_STEPS)
    target_files: list[str] = Field(default_factory=list, max_length=MAX_PLAN_STEPS)
    validation_conditions: list[str] = Field(default_factory=list, max_length=MAX_PLAN_STEPS)
    risks: list[str] = Field(default_factory=list, max_length=MAX_PLAN_STEPS)


class EvidenceItem(_StrictOutput):
    """One bounded source reference and its compact observation."""

    path: str
    line: PositiveInt = 1
    summary: str = Field(max_length=MAX_EVIDENCE_SUMMARY_CHARS)


class EvidenceBundle(_StrictOutput):
    """Retriever evidence plus allowlisted requests used to obtain it."""

    items: list[EvidenceItem] = Field(default_factory=list, max_length=MAX_EVIDENCE_ITEMS)
    tool_calls: list[RetrievalToolCall] = Field(default_factory=list, max_length=MAX_EVIDENCE_ITEMS)


class CoderOutput(_StrictOutput):
    """Coder's bounded state summary and allowlisted workspace operations."""

    current_diff: str = Field(max_length=MAX_DIFF_CHARS)
    public_test_result: str = Field(default="", max_length=MAX_PUBLIC_TEST_RESULT_CHARS)
    tool_calls: list[CoderToolCall] = Field(default_factory=list, max_length=6)


class ReviewOutput(_StrictOutput):
    """Advisory review; deterministic functional success remains outside the graph."""

    status: Literal["approved", "needs_changes", "failed"]
    feedback: str


EvidenceList = Annotated[list[EvidenceItem], Field(max_length=MAX_EVIDENCE_ITEMS)]
DiffText = Annotated[str, Field(max_length=MAX_DIFF_CHARS)]
PublicTestResult = Annotated[str, Field(max_length=MAX_PUBLIC_TEST_RESULT_CHARS)]
RoleHistory = Annotated[list[RoleName], Field(max_length=MAX_ROLE_HISTORY)]


class WorkflowState(TypedDict):
    """The complete bounded LangGraph state; no conversation transcript is retained."""

    case_id: str
    issue: str
    plan: PlanOutput | None
    evidence: EvidenceList
    current_diff: DiffText
    public_test_result: PublicTestResult
    review_feedback: ReviewOutput | None
    usage: Usage
    role_usage: dict[RoleName, Usage]
    role_history: RoleHistory
    rework_count: int
    route_count: int
    stop_reason: str | None


_WORKFLOW_STATE_ADAPTER = TypeAdapter(WorkflowState)


def validate_workflow_state(state: object) -> WorkflowState:
    """Validate state caps at graph initialization and after every role update."""
    return _WORKFLOW_STATE_ADAPTER.validate_python(state)


def budget_stop_reason(
    usage: Usage,
    budget: Budget,
    elapsed_seconds: float,
) -> str | None:
    """Return the shared hard-budget reason used before roles and individual tools."""
    if elapsed_seconds >= budget.max_seconds:
        return "time_budget_exhausted"
    if usage.tool_calls > budget.max_tool_calls:
        return "tool_budget_exhausted"
    if usage.patch_attempts > budget.max_patch_attempts:
        return "patch_budget_exhausted"
    if usage.input_tokens > budget.max_input_tokens:
        return "input_token_budget_exhausted"
    if usage.output_tokens > budget.max_output_tokens:
        return "output_token_budget_exhausted"
    if usage.cost_usd > budget.max_cost_usd:
        return "cost_budget_exhausted"
    return None
