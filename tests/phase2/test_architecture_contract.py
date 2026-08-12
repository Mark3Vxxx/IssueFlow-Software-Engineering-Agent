from pathlib import Path

import pytest

from issueflow.agent import AgentResult
from issueflow.architectures.base import ArchitectureKind, RoleName, RunContext
from issueflow.architectures.single import SingleArchitecture
from issueflow.models import BenchmarkCase, Budget, RunStatus, TraceStep


class ScriptedSingle:
    def run(self, case: BenchmarkCase, workspace: Path, budget: Budget) -> AgentResult:
        return AgentResult(
            status=RunStatus.SUCCEEDED,
            stop_reason="verification_passed",
            steps=[
                TraceStep(
                    sequence=1,
                    role="single_agent",
                    step_type="tool",
                    input_summary="run_tests",
                    output_summary="exit_code=0",
                    status="completed",
                )
            ],
            model_calls=3,
            tool_calls=2,
            patch_attempts=1,
            final_message="Fixed the issue.",
        )


@pytest.fixture
def case() -> BenchmarkCase:
    return BenchmarkCase(
        id="constructed-01",
        kind="constructed",
        budget_profile="small",
        repository_url="https://github.com/karpathy/micrograd",
        revision="a" * 40,
        license="MIT",
        issue="Unary negation returns the wrong value",
        source_url="https://github.com/karpathy/micrograd",
        reproduce_command="python -c 'raise SystemExit(1)'",
        verify_command="python -c 'raise SystemExit(0)'",
        reference_patch="patches/constructed-01-fix.patch",
        construction_notes="Controlled arithmetic regression.",
        fault_patch="patches/constructed-01-fault.patch",
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def budget() -> Budget:
    return Budget(
        max_tool_calls=2,
        max_patch_attempts=1,
        max_seconds=60,
        max_input_tokens=1_000,
        max_output_tokens=1_000,
        max_cost_usd=1.0,
    )


def test_architecture_kinds_are_the_four_experiment_arms():
    assert [item.value for item in ArchitectureKind] == [
        "direct", "single", "fixed", "dynamic"
    ]


def test_single_adapter_preserves_terminal_result(case, workspace, budget):
    result = SingleArchitecture(ScriptedSingle()).run(
        case, workspace, budget, RunContext(run_id="run-1")
    )

    assert result.architecture is ArchitectureKind.SINGLE
    assert result.status is RunStatus.SUCCEEDED
    assert result.usage.model_calls == 3
    assert result.role_usage[RoleName.SINGLE_AGENT].tool_calls == 2
    assert result.steps[0].role == "single_agent"
