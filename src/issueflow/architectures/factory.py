"""Construct one case-scoped runner for each comparable architecture."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from issueflow.agent import ModelClient, SandboxRunner, SingleAgent, ToolExecutor
from issueflow.architectures.base import ArchitectureKind, ArchitectureRunner
from issueflow.architectures.direct import DirectArchitecture
from issueflow.architectures.dynamic import DynamicSupervisorArchitecture
from issueflow.architectures.fixed import FixedMultiAgentArchitecture
from issueflow.architectures.single import SingleArchitecture
from issueflow.models import BenchmarkCase
from issueflow.structured_model import StructuredModel

SingleModelFactory = Callable[[BenchmarkCase], ModelClient]


class ArchitectureFactory:
    """Bind the selected architecture to one case, workspace, and sandbox."""

    def __init__(
        self,
        *,
        single_model_factory: SingleModelFactory,
        structured_model: StructuredModel,
        sandbox: SandboxRunner,
    ) -> None:
        self.single_model_factory = single_model_factory
        self.structured_model = structured_model
        self.sandbox = sandbox

    def create(
        self,
        kind: ArchitectureKind,
        case: BenchmarkCase,
        workspace: Path,
    ) -> ArchitectureRunner:
        """Create exactly the requested runner with a case-scoped tool executor."""
        tools = ToolExecutor(workspace, case, self.sandbox)
        if kind is ArchitectureKind.DIRECT:
            return DirectArchitecture(self.structured_model, tools)
        if kind is ArchitectureKind.SINGLE:
            return SingleArchitecture(
                SingleAgent(self.single_model_factory(case), tools)
            )
        if kind is ArchitectureKind.FIXED:
            return FixedMultiAgentArchitecture(
                model=self.structured_model,
                tools=tools,
            )
        if kind is ArchitectureKind.DYNAMIC:
            return DynamicSupervisorArchitecture(
                self.structured_model,
                tools=tools,
            )
        raise ValueError(f"unsupported architecture: {kind}")
