"""Domain types shared across IssueFlow modules."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, PositiveFloat, PositiveInt


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


class Budget(BaseModel):
    """Hard per-run limits that prevent unbounded agent execution."""

    max_tool_calls: PositiveInt
    max_patch_attempts: PositiveInt
    max_seconds: PositiveInt
    max_input_tokens: PositiveInt
    max_output_tokens: PositiveInt
    max_cost_usd: PositiveFloat


class BenchmarkCase(BaseModel):
    """A reproducible issue-repair sample from the approved catalog."""

    id: str
    kind: Literal["historical", "constructed"]
    repository_url: str
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    license: str
    issue: str
    source_url: str
    reproduce_command: str
    verify_command: str
    reference_patch: str
    construction_notes: str


class TraceStep(BaseModel):
    """One immutable event in a repair run's decision trace."""

    sequence: PositiveInt
    role: str
    step_type: str
    input_summary: str
    output_summary: str
    status: str


class RunRecord(BaseModel):
    """The top-level state for one repair attempt."""

    id: str
    case_id: str
    status: RunStatus = RunStatus.QUEUED
