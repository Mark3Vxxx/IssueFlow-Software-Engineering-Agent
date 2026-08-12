"""Shared contract for comparable repair architectures."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field, NonNegativeInt

from issueflow.models import BenchmarkCase, Budget, RunStatus, TraceStep, Usage


class ArchitectureKind(StrEnum):
    DIRECT = "direct"
    SINGLE = "single"
    FIXED = "fixed"
    DYNAMIC = "dynamic"


class RoleName(StrEnum):
    DIRECT = "direct"
    SINGLE_AGENT = "single_agent"
    PLANNER = "planner"
    RETRIEVER = "retriever"
    CODER = "coder"
    REVIEWER = "reviewer"
    SUPERVISOR = "supervisor"


class RunContext(BaseModel):
    run_id: str


class ArchitectureResult(BaseModel):
    architecture: ArchitectureKind
    status: RunStatus
    stop_reason: str
    steps: list[TraceStep] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    role_usage: dict[RoleName, Usage] = Field(default_factory=dict)
    route_count: NonNegativeInt = 0
    final_message: str = ""


class ArchitectureRunner(Protocol):
    def run(
        self,
        case: BenchmarkCase,
        workspace: Path,
        budget: Budget,
        context: RunContext,
    ) -> ArchitectureResult: ...
