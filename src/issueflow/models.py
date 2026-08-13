"""Domain types shared across IssueFlow modules."""

from enum import StrEnum
from typing import Literal

from pydantic import (
    BaseModel,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
    model_validator,
)


class RunStatus(StrEnum):
    """Lifecycle states for one repair attempt."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    BUDGET_EXHAUSTED = "budget_exhausted"

    @property
    def is_terminal(self) -> bool:
        """Whether a run in this state cannot accept further work."""
        return self in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.TIMED_OUT,
            RunStatus.BUDGET_EXHAUSTED,
        }


class DatasetSplit(StrEnum):
    """Evidence-quality bucket for a benchmark case."""

    COMPATIBILITY = "compatibility"
    STRICT = "strict"
    EXPLORATORY = "exploratory"


class Budget(BaseModel):
    """Hard per-run limits that prevent unbounded agent execution."""

    max_tool_calls: PositiveInt
    max_patch_attempts: PositiveInt
    max_seconds: PositiveInt
    max_input_tokens: PositiveInt
    max_output_tokens: PositiveInt
    max_cost_usd: PositiveFloat


class Usage(BaseModel):
    """Dependency-neutral metrics for one agent or architecture run."""

    model_calls: NonNegativeInt = 0
    tool_calls: NonNegativeInt = 0
    patch_attempts: NonNegativeInt = 0
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    cost_usd: NonNegativeFloat = 0.0
    duration_ms: NonNegativeInt = 0


class AgentCaseView(BaseModel):
    """The subset of a case exposed to Agent prompts and tool selection."""

    id: str
    repository_id: str
    issue: str
    reproduce_command: str
    verify_command: str


class BenchmarkCase(BaseModel):
    """A reproducible issue-repair sample from a dataset split."""

    id: str
    dataset_split: DatasetSplit
    repository_id: str
    environment_id: str
    kind: Literal["historical", "constructed"]
    budget_profile: Literal["small", "medium", "large"]
    repository_url: str
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    license: str
    issue: str
    source_url: str
    reproduce_command: str
    verify_command: str
    reference_patch: str
    construction_notes: str
    fault_patch: str | None = None
    hidden_test_path: str | None = None
    hidden_verify_command: str | None = None
    fixed_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    difficulty: Literal["small", "medium", "large"]
    issue_category: Literal[
        "data_config", "model_training", "test_boundary", "numerical", "performance", "refactor"
    ]

    @model_validator(mode="after")
    def validate_sample_provenance(self) -> "BenchmarkCase":
        """Enforce provenance and strict-split answer-isolation rules."""
        if self.kind == "constructed" and not self.fault_patch:
            raise ValueError("constructed cases require fault_patch")
        if self.kind == "historical" and self.fault_patch:
            raise ValueError("historical cases must not define fault_patch")
        if self.dataset_split is DatasetSplit.STRICT:
            if self.kind != "historical":
                raise ValueError("strict cases must be historical")
            if not self.hidden_test_path or not self.hidden_verify_command:
                raise ValueError("strict cases require hidden validation")
            if self.fixed_revision is None:
                raise ValueError("strict cases require a fixed revision")
            if not self.source_url.strip():
                raise ValueError("strict cases require a source URL")
        return self

    def agent_view(self) -> "AgentCaseView":
        """Return only the fields an Agent is allowed to see."""
        return AgentCaseView(
            id=self.id,
            repository_id=self.repository_id,
            issue=self.issue,
            reproduce_command=self.reproduce_command,
            verify_command=self.verify_command,
        )


class TraceStep(BaseModel):
    """One immutable event in a repair run's decision trace."""

    sequence: PositiveInt
    role: str
    step_type: str
    input_summary: str
    output_summary: str
    status: str
    duration_ms: NonNegativeInt = 0
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    cost_usd: NonNegativeFloat = 0.0


class RunRecord(BaseModel):
    """The top-level state for one repair attempt."""

    id: str
    case_id: str
    architecture: Literal["direct", "single", "fixed", "dynamic"] = "single"
    status: RunStatus = RunStatus.QUEUED
    stop_reason: str | None = None
    functional_success: bool | None = None
    review_status: str | None = None
    review_reasons: list[str] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    role_usage: dict[str, Usage] = Field(default_factory=dict)
