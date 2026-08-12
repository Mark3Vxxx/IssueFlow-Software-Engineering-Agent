"""Adapter that presents the existing single agent as an architecture arm."""

from pathlib import Path

from issueflow.agent import SingleAgent
from issueflow.architectures.base import (
    ArchitectureKind,
    ArchitectureResult,
    RoleName,
    RunContext,
)
from issueflow.models import BenchmarkCase, Budget, Usage


class SingleArchitecture:
    """Delegate a run to the existing single-agent repair loop exactly once."""

    def __init__(self, agent: SingleAgent) -> None:
        self.agent = agent

    def run(
        self,
        case: BenchmarkCase,
        workspace: Path,
        budget: Budget,
        context: RunContext,
    ) -> ArchitectureResult:
        """Adapt a terminal ``SingleAgent`` result to the architecture contract."""
        del context
        result = self.agent.run(case, workspace, budget)
        usage = Usage(
            tool_calls=result.tool_calls,
            patch_attempts=result.patch_attempts,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
            duration_ms=result.duration_ms,
        )
        return ArchitectureResult(
            architecture=ArchitectureKind.SINGLE,
            status=result.status,
            stop_reason=result.stop_reason,
            steps=result.steps,
            usage=usage,
            role_usage={RoleName.SINGLE_AGENT: usage},
            final_message=result.final_message,
        )
