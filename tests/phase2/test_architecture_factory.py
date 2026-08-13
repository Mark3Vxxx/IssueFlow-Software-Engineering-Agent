from pathlib import Path

import pytest

from issueflow.agent import ModelAction
from issueflow.architectures.base import ArchitectureKind
from issueflow.architectures.direct import DirectArchitecture
from issueflow.architectures.dynamic import DynamicSupervisorArchitecture
from issueflow.architectures.factory import ArchitectureFactory
from issueflow.architectures.fixed import FixedMultiAgentArchitecture
from issueflow.architectures.single import SingleArchitecture
from issueflow.models import BenchmarkCase
from issueflow.sandbox import SandboxResult
from issueflow.structured_model import StructuredCompletion


class StaticSingleModel:
    def next_action(self, issue, history):
        return ModelAction(message="unused")


class StaticStructuredModel:
    def complete(self, system_prompt, payload, schema):
        return StructuredCompletion(value=schema(), usage={})


class StaticSandbox:
    def run(self, workspace, command, timeout_seconds):
        return SandboxResult(0, "unused", False, 0)


@pytest.fixture
def case() -> BenchmarkCase:
    return BenchmarkCase(
        id="constructed-01",
        dataset_split="compatibility",
        repository_id="micrograd",
        environment_id="micrograd",
        difficulty="small",
        issue_category="numerical",
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


def test_factory_creates_each_exact_architecture_with_case_scoped_tools(case, tmp_path):
    sandbox = StaticSandbox()
    single_models: list[BenchmarkCase] = []

    def single_model_factory(selected_case: BenchmarkCase):
        single_models.append(selected_case)
        return StaticSingleModel()

    factory = ArchitectureFactory(
        single_model_factory=single_model_factory,
        structured_model=StaticStructuredModel(),
        sandbox=sandbox,
    )

    runners = {kind: factory.create(kind, case, tmp_path) for kind in ArchitectureKind}

    assert isinstance(runners[ArchitectureKind.DIRECT], DirectArchitecture)
    assert isinstance(runners[ArchitectureKind.SINGLE], SingleArchitecture)
    assert isinstance(runners[ArchitectureKind.FIXED], FixedMultiAgentArchitecture)
    assert isinstance(runners[ArchitectureKind.DYNAMIC], DynamicSupervisorArchitecture)
    assert single_models == [case]
    for runner in runners.values():
        executor = runner.agent.tools if isinstance(runner, SingleArchitecture) else runner.tools
        assert executor.workspace == Path(tmp_path).resolve()
        assert executor.case == case
        assert executor.sandbox is sandbox
