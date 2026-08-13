from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from subprocess import TimeoutExpired

import pytest
from pydantic import ValidationError

from issueflow.agent import ToolExecutor
from issueflow.architectures.base import ArchitectureKind, RoleName, RunContext
from issueflow.architectures.fixed import FixedMultiAgentArchitecture
from issueflow.architectures.roles import RoleSet
from issueflow.architectures.state import (
    CoderOutput,
    CoderToolCall,
    EvidenceBundle,
    EvidenceItem,
    PlanOutput,
    ReviewOutput,
    validate_workflow_state,
)
from issueflow.models import BenchmarkCase, Budget, RunStatus, TraceStep, Usage
from issueflow.structured_model import StructuredCompletion


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


@pytest.fixture
def budget() -> Budget:
    return Budget(
        max_tool_calls=12,
        max_patch_attempts=2,
        max_seconds=60,
        max_input_tokens=1_000,
        max_output_tokens=1_000,
        max_cost_usd=1.0,
    )


def role_step(role: RoleName, summary: str = "completed") -> TraceStep:
    return TraceStep(
        sequence=1,
        role=role,
        step_type="role",
        input_summary="bounded workflow state",
        output_summary=summary,
        status="completed",
    )


class ScriptedRole:
    def __init__(self, role: RoleName, updates: list[dict[str, object]]) -> None:
        self.role = role
        self.updates = deque(updates)
        self.calls = 0

    def __call__(self, state):
        self.calls += 1
        update = self.updates.popleft()
        return update, role_step(self.role)


def scripted_roles(
    reviews: list[str],
    *,
    planner_usage: Usage | None = None,
    retriever_usage: Usage | None = None,
) -> tuple[RoleSet, dict[RoleName, ScriptedRole]]:
    scripts = {
        RoleName.PLANNER: ScriptedRole(
            RoleName.PLANNER,
            [
                {
                    "plan": PlanOutput(steps=["Inspect the failing implementation."]),
                    "usage": planner_usage or Usage(model_calls=1),
                }
            ],
        ),
        RoleName.RETRIEVER: ScriptedRole(
            RoleName.RETRIEVER,
            [
                {
                    "evidence": [
                        EvidenceItem(path="engine.py", line=1, summary="Wrong return value.")
                    ],
                    "usage": retriever_usage or Usage(model_calls=1),
                }
            ],
        ),
        RoleName.CODER: ScriptedRole(
            RoleName.CODER,
            [
                {
                    "current_diff": "-broken\n+fixed\n",
                    "public_test_result": "exit_code=0",
                    "usage": Usage(model_calls=1),
                }
                for _ in range(2)
            ],
        ),
        RoleName.REVIEWER: ScriptedRole(
            RoleName.REVIEWER,
            [
                {
                    "review_feedback": ReviewOutput(status=status, feedback=status),
                    "usage": Usage(model_calls=1),
                }
                for status in reviews
            ],
        ),
    }
    return (
        RoleSet(
            plan=scripts[RoleName.PLANNER],
            retrieve=scripts[RoleName.RETRIEVER],
            code=scripts[RoleName.CODER],
            review=scripts[RoleName.REVIEWER],
        ),
        scripts,
    )


def test_fixed_visits_roles_in_order_once(case, tmp_path, budget):
    roles, _ = scripted_roles(["approved"])

    result = FixedMultiAgentArchitecture(roles=roles).run(
        case, tmp_path, budget, RunContext(run_id="fixed-approved")
    )

    assert result.architecture is ArchitectureKind.FIXED
    assert result.status is RunStatus.SUCCEEDED
    assert result.stop_reason == "review_approved"
    assert [step.role for step in result.steps if step.step_type == "role"] == [
        "planner",
        "retriever",
        "coder",
        "reviewer",
    ]
    assert [step.sequence for step in result.steps] == [1, 2, 3, 4]
    assert result.route_count == 4


def test_fixed_allows_exactly_one_coder_rework(case, tmp_path, budget):
    roles, scripts = scripted_roles(["needs_changes", "needs_changes"])

    result = FixedMultiAgentArchitecture(roles=roles).run(
        case, tmp_path, budget, RunContext(run_id="fixed-rework")
    )

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "review_loop_exhausted"
    assert [step.role for step in result.steps if step.step_type == "role"] == [
        "planner",
        "retriever",
        "coder",
        "reviewer",
        "coder",
        "reviewer",
    ]
    assert result.route_count == 6
    assert scripts[RoleName.CODER].calls == 2
    assert scripts[RoleName.REVIEWER].calls == 2


def test_workflow_schemas_enforce_all_state_caps():
    with pytest.raises(ValidationError):
        PlanOutput(steps=[str(index) for index in range(7)])
    with pytest.raises(ValidationError):
        EvidenceItem(path="engine.py", line=1, summary="x" * 2_001)
    with pytest.raises(ValidationError):
        EvidenceBundle(
            items=[
                EvidenceItem(path=f"file-{index}.py", line=1, summary="evidence")
                for index in range(21)
            ]
        )

    state = {
        "case_id": "case-1",
        "issue": "issue",
        "plan": None,
        "evidence": [],
        "current_diff": "x" * 20_001,
        "public_test_result": "",
        "review_feedback": None,
        "usage": Usage(),
        "role_usage": {},
        "role_history": [],
        "rework_count": 0,
        "route_count": 0,
        "stop_reason": None,
    }
    with pytest.raises(ValidationError):
        validate_workflow_state(state)

    state["current_diff"] = ""
    state["role_history"] = [RoleName.CODER] * 51
    with pytest.raises(ValidationError):
        validate_workflow_state(state)


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (
            PlanOutput,
            {"steps": ["Inspect"], "tool_calls": [{"tool": "read_file", "arguments": {}}]},
        ),
        (
            EvidenceBundle,
            {"items": [], "tool_calls": [{"tool": "apply_patch", "arguments": {}}]},
        ),
        (
            CoderOutput,
            {
                "current_diff": "",
                "public_test_result": "",
                "tool_calls": [{"tool": "search", "arguments": {}}],
            },
        ),
        (
            ReviewOutput,
            {
                "status": "approved",
                "feedback": "small diff",
                "tool_calls": [{"tool": "apply_patch", "arguments": {}}],
            },
        ),
    ],
)
def test_role_output_schemas_reject_tools_outside_each_role_boundary(schema, payload):
    with pytest.raises(ValidationError):
        schema.model_validate(payload)


@dataclass
class SandboxResult:
    returncode: int = 0
    output: str = "1 passed"
    timed_out: bool = False


class Sandbox:
    def run(self, workspace, command, timeout_seconds):
        return SandboxResult()


class RecordingTools(ToolExecutor):
    def __init__(self, workspace, case, sandbox):
        super().__init__(workspace, case, sandbox)
        self.calls = []

    def execute(self, action, timeout_seconds=60):
        self.calls.append(action)
        return super().execute(action, timeout_seconds=timeout_seconds)


class ProductionRoleModel:
    def complete(self, system_prompt, payload, schema):
        if schema is PlanOutput:
            value = PlanOutput(steps=["Inspect engine.py", "Apply a minimal replacement"])
        elif schema is EvidenceBundle:
            value = EvidenceBundle(
                items=[],
                tool_calls=[
                    {"tool": "search", "arguments": {"query": "broken"}},
                    {"tool": "read_file", "arguments": {"path": "engine.py"}},
                ],
            )
        elif schema is CoderOutput:
            value = CoderOutput(
                current_diff="-return 'broken'\n+return 'fixed'\n",
                public_test_result="",
                tool_calls=[
                    {"tool": "read_file", "arguments": {"path": "engine.py"}},
                    {
                        "tool": "apply_patch",
                        "arguments": {
                            "path": "engine.py",
                            "old_text": "return 'broken'\n",
                            "new_text": "return 'fixed'\n",
                        },
                    },
                    {
                        "tool": "run_tests",
                        "arguments": {"command": "python -c 'raise SystemExit(0)'"},
                    },
                ],
            )
        else:
            value = ReviewOutput(status="approved", feedback="The patch is focused.")
        return StructuredCompletion(value=value, usage=Usage(model_calls=1))


def test_production_roles_use_only_their_allowlisted_tool_executor_boundary(case, tmp_path):
    target = tmp_path / "engine.py"
    target.write_text("return 'broken'\n", encoding="utf-8")
    roles = RoleSet.production(ProductionRoleModel(), ToolExecutor(tmp_path, case, Sandbox()))
    state = {
        "case_id": case.id,
        "issue": case.issue,
        "plan": None,
        "evidence": [],
        "current_diff": "",
        "public_test_result": "",
        "review_feedback": None,
        "usage": Usage(),
        "role_usage": {},
        "role_history": [],
        "rework_count": 0,
        "route_count": 0,
        "stop_reason": None,
    }

    plan_update, _ = roles.plan(state)
    state.update(plan_update)
    evidence_update, _ = roles.retrieve(state)
    state.update(evidence_update)
    code_update, _ = roles.code(state)
    state.update(code_update)
    review_update, _ = roles.review(state)

    assert plan_update["usage"].model_calls == 1
    assert len(evidence_update["evidence"]) == 2
    assert code_update["usage"].tool_calls == 3
    assert code_update["usage"].patch_attempts == 1
    assert code_update["public_test_result"].startswith("exit_code=0")
    assert review_update["review_feedback"].status == "approved"
    assert target.read_text(encoding="utf-8") == "return 'fixed'\n"


class ClaimedPublicTestModel:
    def complete(self, system_prompt, payload, schema):
        assert schema is CoderOutput
        return StructuredCompletion(
            value=CoderOutput(
                current_diff="-broken\n+fixed\n",
                public_test_result="exit_code=0\nfabricated",
            ),
            usage=Usage(model_calls=1),
        )


def test_production_coder_ignores_unexecuted_model_test_claim(case, tmp_path):
    roles = RoleSet.production(ClaimedPublicTestModel(), ToolExecutor(tmp_path, case, Sandbox()))
    state = validate_workflow_state(
        {
            "case_id": case.id,
            "issue": case.issue,
            "plan": PlanOutput(steps=["Repair engine.py"]),
            "evidence": [EvidenceItem(path="engine.py", line=1, summary="Wrong return value.")],
            "current_diff": "",
            "public_test_result": "",
            "review_feedback": None,
            "usage": Usage(),
            "role_usage": {},
            "role_history": [],
            "rework_count": 0,
            "route_count": 0,
            "stop_reason": None,
        }
    )

    update, _ = roles.code(state)

    assert update["public_test_result"] == ""
    assert update["usage"].model_calls == 1
    assert update["usage"].tool_calls == 0


def test_public_test_result_is_explicitly_bounded(case, tmp_path):
    class LargeOutputSandbox:
        def run(self, workspace, command, timeout_seconds):
            return SandboxResult(returncode=0, output="x" * 10_000, timed_out=False)

    class TestModel:
        def complete(self, system_prompt, payload, schema):
            return StructuredCompletion(
                value=CoderOutput(
                    current_diff="-broken\n+fixed\n",
                    tool_calls=[
                        CoderToolCall(
                            tool="run_tests",
                            arguments={"command": case.verify_command},
                        )
                    ],
                ),
                usage=Usage(model_calls=1),
            )

    roles = RoleSet.production(TestModel(), ToolExecutor(tmp_path, case, LargeOutputSandbox()))
    state = validate_workflow_state(
        {
            "case_id": case.id,
            "issue": case.issue,
            "plan": PlanOutput(steps=["Test"]),
            "evidence": [EvidenceItem(path="engine.py", summary="evidence")],
            "current_diff": "",
            "public_test_result": "",
            "review_feedback": None,
            "usage": Usage(),
            "role_usage": {},
            "role_history": [],
            "rework_count": 0,
            "route_count": 0,
            "stop_reason": None,
        }
    )

    update, _ = roles.code(state)

    assert len(update["public_test_result"]) == 2_000


class ToolHeavyModel:
    def complete(self, system_prompt, payload, schema):
        if schema is PlanOutput:
            value = PlanOutput(steps=["Inspect the implementation"])
        elif schema is EvidenceBundle:
            value = EvidenceBundle(
                tool_calls=[
                    {"tool": "search", "arguments": {"query": "broken"}},
                    {"tool": "read_file", "arguments": {"path": "engine.py"}},
                ]
            )
        else:
            raise AssertionError("Coder must not start after Retriever exhausts tools")
        return StructuredCompletion(value=value, usage=Usage(model_calls=1))


def test_production_retriever_stops_before_tool_limit_overrun(case, tmp_path, budget):
    (tmp_path / "engine.py").write_text("return 'broken'\n", encoding="utf-8")
    tools = RecordingTools(tmp_path, case, Sandbox())

    result = FixedMultiAgentArchitecture(model=ToolHeavyModel(), tools=tools).run(
        case,
        tmp_path,
        budget.model_copy(update={"max_tool_calls": 1}),
        RunContext(run_id="fixed-production-tool-budget"),
    )

    assert result.status is RunStatus.BUDGET_EXHAUSTED
    assert result.stop_reason == "tool_budget_exhausted"
    assert result.usage.tool_calls == 1
    assert [call.tool for call in tools.calls] == ["search"]


class PatchHeavyModel:
    def complete(self, system_prompt, payload, schema):
        if schema is PlanOutput:
            value = PlanOutput(steps=["Repair engine.py"])
        elif schema is EvidenceBundle:
            value = EvidenceBundle()
        elif schema is CoderOutput:
            value = CoderOutput(
                current_diff="bounded diff",
                tool_calls=[
                    {
                        "tool": "apply_patch",
                        "arguments": {
                            "path": "engine.py",
                            "old_text": "return 'broken'\n",
                            "new_text": "return 'halfway'\n",
                        },
                    },
                    {
                        "tool": "apply_patch",
                        "arguments": {
                            "path": "engine.py",
                            "old_text": "return 'halfway'\n",
                            "new_text": "return 'fixed'\n",
                        },
                    },
                ],
            )
        else:
            raise AssertionError("Reviewer must not start after Coder exhausts patches")
        return StructuredCompletion(value=value, usage=Usage(model_calls=1))


def test_production_coder_stops_before_patch_limit_overrun(case, tmp_path, budget):
    target = tmp_path / "engine.py"
    target.write_text("return 'broken'\n", encoding="utf-8")
    tools = RecordingTools(tmp_path, case, Sandbox())

    result = FixedMultiAgentArchitecture(model=PatchHeavyModel(), tools=tools).run(
        case,
        tmp_path,
        budget.model_copy(update={"max_patch_attempts": 1}),
        RunContext(run_id="fixed-production-patch-budget"),
    )

    assert result.status is RunStatus.BUDGET_EXHAUSTED
    assert result.stop_reason == "patch_budget_exhausted"
    assert result.usage.patch_attempts == 1
    assert [call.tool for call in tools.calls] == ["apply_patch"]
    assert target.read_text(encoding="utf-8") == "return 'halfway'\n"


def test_fixed_normalizes_malformed_injected_role_output(case, tmp_path, budget):
    roles, _ = scripted_roles(["approved"])
    roles = RoleSet(
        plan=lambda state: (None, None),
        retrieve=roles.retrieve,
        code=roles.code,
        review=roles.review,
    )

    result = FixedMultiAgentArchitecture(roles=roles).run(
        case, tmp_path, budget, RunContext(run_id="fixed-malformed-role")
    )

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "invalid_role_output"
    assert [step.role for step in result.steps] == ["planner"]


def test_fixed_normalizes_malformed_reviewer_feedback(case, tmp_path, budget):
    roles, _ = scripted_roles(["approved"])
    roles = RoleSet(
        plan=roles.plan,
        retrieve=roles.retrieve,
        code=roles.code,
        review=lambda state: (
            {"review_feedback": object(), "usage": Usage(model_calls=1)},
            role_step(RoleName.REVIEWER),
        ),
    )

    result = FixedMultiAgentArchitecture(roles=roles).run(
        case, tmp_path, budget, RunContext(run_id="fixed-malformed-review")
    )

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "invalid_role_output"
    assert [step.role for step in result.steps] == [
        "planner",
        "retriever",
        "coder",
        "reviewer",
    ]


def test_fixed_checks_workspace_of_prebuilt_production_roles(case, tmp_path, budget):
    executor_workspace = tmp_path / "executor"
    executor_workspace.mkdir()
    requested_workspace = tmp_path / "requested"
    requested_workspace.mkdir()
    roles = RoleSet.production(
        ProductionRoleModel(), ToolExecutor(executor_workspace, case, Sandbox())
    )

    result = FixedMultiAgentArchitecture(roles=roles).run(
        case,
        requested_workspace,
        budget,
        RunContext(run_id="fixed-prebuilt-workspace-mismatch"),
    )

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "workspace_mismatch"


def test_fixed_checks_role_workspace_when_separate_tools_are_also_supplied(case, tmp_path, budget):
    role_workspace = tmp_path / "roles"
    role_workspace.mkdir()
    requested_workspace = tmp_path / "requested"
    requested_workspace.mkdir()
    roles = RoleSet.production(ProductionRoleModel(), ToolExecutor(role_workspace, case, Sandbox()))
    requested_tools = ToolExecutor(requested_workspace, case, Sandbox())

    result = FixedMultiAgentArchitecture(roles=roles, tools=requested_tools).run(
        case,
        requested_workspace,
        budget,
        RunContext(run_id="fixed-dual-executor-workspace-mismatch"),
    )

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "workspace_mismatch"


def test_fixed_rejects_executor_bound_to_a_different_case(case, tmp_path, budget):
    other_case = case.model_copy(
        update={
            "id": "constructed-02",
            "reproduce_command": "python -c 'print(\"different\")'",
            "verify_command": "python -c 'raise SystemExit(2)'",
        }
    )
    roles = RoleSet.production(ProductionRoleModel(), ToolExecutor(tmp_path, other_case, Sandbox()))

    result = FixedMultiAgentArchitecture(roles=roles).run(
        case,
        tmp_path,
        budget,
        RunContext(run_id="fixed-executor-case-mismatch"),
    )

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "case_mismatch"


class SubprocessTimeoutTools(ToolExecutor):
    def __init__(self, workspace, case):
        super().__init__(workspace, case, sandbox=Sandbox())
        self.calls = []

    def execute(self, action, timeout_seconds=60):
        self.calls.append(action)
        raise TimeoutExpired(cmd=action.tool or "unknown", timeout=timeout_seconds)


class RetrieverTimeoutModel:
    def complete(self, system_prompt, payload, schema):
        if schema is PlanOutput:
            value = PlanOutput(steps=["Find the broken implementation"])
        elif schema is EvidenceBundle:
            value = EvidenceBundle(
                tool_calls=[{"tool": "search", "arguments": {"query": "broken"}}]
            )
        else:
            raise AssertionError("Coder must not start after Retriever timeout")
        return StructuredCompletion(value=value, usage=Usage(model_calls=1))


def test_retriever_subprocess_timeout_preserves_model_and_tool_usage(case, tmp_path, budget):
    tools = SubprocessTimeoutTools(tmp_path, case)

    result = FixedMultiAgentArchitecture(model=RetrieverTimeoutModel(), tools=tools).run(
        case,
        tmp_path,
        budget,
        RunContext(run_id="fixed-retriever-subprocess-timeout"),
    )

    assert result.status is RunStatus.TIMED_OUT
    assert result.stop_reason == "time_budget_exhausted"
    assert result.usage.model_calls == 2
    assert result.usage.tool_calls == 1
    assert result.usage.patch_attempts == 0
    assert result.role_usage[RoleName.RETRIEVER].model_calls == 1
    assert result.role_usage[RoleName.RETRIEVER].tool_calls == 1
    assert [step.role for step in result.steps] == ["planner", "retriever"]


class CoderTimeoutModel:
    def complete(self, system_prompt, payload, schema):
        if schema is PlanOutput:
            value = PlanOutput(steps=["Patch the broken implementation"])
        elif schema is EvidenceBundle:
            value = EvidenceBundle()
        elif schema is CoderOutput:
            value = CoderOutput(
                current_diff="bounded attempted diff",
                tool_calls=[
                    {
                        "tool": "apply_patch",
                        "arguments": {"patch": "not executed because subprocess times out"},
                    }
                ],
            )
        else:
            raise AssertionError("Reviewer must not start after Coder timeout")
        return StructuredCompletion(value=value, usage=Usage(model_calls=1))


def test_coder_subprocess_timeout_preserves_model_tool_and_patch_usage(case, tmp_path, budget):
    tools = SubprocessTimeoutTools(tmp_path, case)

    result = FixedMultiAgentArchitecture(model=CoderTimeoutModel(), tools=tools).run(
        case,
        tmp_path,
        budget,
        RunContext(run_id="fixed-coder-subprocess-timeout"),
    )

    assert result.status is RunStatus.TIMED_OUT
    assert result.stop_reason == "time_budget_exhausted"
    assert result.usage.model_calls == 3
    assert result.usage.tool_calls == 1
    assert result.usage.patch_attempts == 1
    assert result.role_usage[RoleName.CODER].model_calls == 1
    assert result.role_usage[RoleName.CODER].tool_calls == 1
    assert result.role_usage[RoleName.CODER].patch_attempts == 1
    assert [step.role for step in result.steps] == ["planner", "retriever", "coder"]


class MixedRetrieverModel:
    def __init__(self):
        self.calls = 0

    def complete(self, system_prompt, payload, schema):
        self.calls += 1
        assert schema is EvidenceBundle
        return StructuredCompletion(
            value=EvidenceBundle(tool_calls=[{"tool": "search", "arguments": {"query": "broken"}}]),
            usage=Usage(model_calls=1),
        )


def test_fixed_rejects_mixed_retriever_bound_to_wrong_workspace(case, tmp_path, budget):
    role_workspace = tmp_path / "hidden-role-workspace"
    role_workspace.mkdir()
    (role_workspace / "engine.py").write_text("broken\n", encoding="utf-8")
    requested_workspace = tmp_path / "requested-workspace"
    requested_workspace.mkdir()
    model = MixedRetrieverModel()
    tools = RecordingTools(role_workspace, case, Sandbox())
    production = RoleSet.production(model, tools)
    injected, scripts = scripted_roles(["approved"])
    mixed = RoleSet(
        plan=injected.plan,
        retrieve=production.retrieve,
        code=injected.code,
        review=injected.review,
    )

    result = FixedMultiAgentArchitecture(roles=mixed).run(
        case,
        requested_workspace,
        budget,
        RunContext(run_id="fixed-mixed-retriever-workspace"),
    )

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "invalid_role_set"
    assert model.calls == 0
    assert tools.calls == []
    assert all(script.calls == 0 for script in scripts.values())
    assert result.steps == []
    assert result.route_count == 0


class RecordingSandbox:
    def __init__(self):
        self.commands = []

    def run(self, workspace, command, timeout_seconds):
        self.commands.append(command)
        return SandboxResult()


class MixedCoderModel:
    def __init__(self, command):
        self.command = command
        self.calls = 0

    def complete(self, system_prompt, payload, schema):
        self.calls += 1
        assert schema is CoderOutput
        return StructuredCompletion(
            value=CoderOutput(
                current_diff="hidden case command",
                tool_calls=[{"tool": "run_tests", "arguments": {"command": self.command}}],
            ),
            usage=Usage(model_calls=1),
        )


def test_fixed_rejects_mixed_coder_bound_to_a_different_case(case, tmp_path, budget):
    other_case = case.model_copy(
        update={
            "id": "constructed-hidden",
            "reproduce_command": "other-reproduce",
            "verify_command": "other-verify",
        }
    )
    sandbox = RecordingSandbox()
    model = MixedCoderModel(other_case.verify_command)
    production = RoleSet.production(model, ToolExecutor(tmp_path, other_case, sandbox))
    injected, scripts = scripted_roles(["approved"])
    mixed = RoleSet(
        plan=injected.plan,
        retrieve=injected.retrieve,
        code=production.code,
        review=injected.review,
    )

    result = FixedMultiAgentArchitecture(roles=mixed).run(
        case,
        tmp_path,
        budget,
        RunContext(run_id="fixed-mixed-coder-case"),
    )

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "invalid_role_set"
    assert model.calls == 0
    assert sandbox.commands == []
    assert all(script.calls == 0 for script in scripts.values())
    assert result.steps == []
    assert result.route_count == 0


class NeverCalledModel:
    def __init__(self):
        self.calls = 0

    def complete(self, system_prompt, payload, schema):
        self.calls += 1
        raise AssertionError("mixed production owners must be rejected before execution")


def test_fixed_rejects_production_callbacks_from_different_owners(case, tmp_path, budget):
    models = [NeverCalledModel() for _ in range(4)]
    production_sets = [
        RoleSet.production(model, ToolExecutor(tmp_path, case, Sandbox())) for model in models
    ]
    mixed = RoleSet(
        plan=production_sets[0].plan,
        retrieve=production_sets[1].retrieve,
        code=production_sets[2].code,
        review=production_sets[3].review,
    )

    result = FixedMultiAgentArchitecture(roles=mixed).run(
        case,
        tmp_path,
        budget,
        RunContext(run_id="fixed-multiple-production-owners"),
    )

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "invalid_role_set"
    assert all(model.calls == 0 for model in models)
    assert result.steps == []
    assert result.route_count == 0


@pytest.mark.parametrize(
    ("planner_usage", "budget_overrides", "expected_reason"),
    [
        (Usage(tool_calls=2), {"max_tool_calls": 1}, "tool_budget_exhausted"),
        (
            Usage(model_calls=1, input_tokens=1_001),
            {"max_input_tokens": 1_000},
            "input_token_budget_exhausted",
        ),
        (Usage(model_calls=1, cost_usd=1.01), {"max_cost_usd": 1.0}, "cost_budget_exhausted"),
    ],
)
def test_fixed_stops_after_a_role_exhausts_the_shared_budget(
    case, tmp_path, budget, planner_usage, budget_overrides, expected_reason
):
    roles, scripts = scripted_roles(["approved"], planner_usage=planner_usage)
    constrained_budget = budget.model_copy(update=budget_overrides)

    result = FixedMultiAgentArchitecture(roles=roles).run(
        case,
        tmp_path,
        constrained_budget,
        RunContext(run_id=f"fixed-{expected_reason}"),
    )

    assert result.status is RunStatus.BUDGET_EXHAUSTED
    assert result.stop_reason == expected_reason
    assert [step.role for step in result.steps] == ["planner"]
    assert scripts[RoleName.RETRIEVER].calls == 0
    assert result.role_usage[RoleName.PLANNER] == planner_usage


def test_fixed_stops_for_time_exhaustion_before_the_next_role(case, tmp_path, budget):
    roles, scripts = scripted_roles(["approved"])
    readings = iter([0.0, 0.0, 61.0, 61.0])

    result = FixedMultiAgentArchitecture(roles=roles, clock=lambda: next(readings)).run(
        case, tmp_path, budget, RunContext(run_id="fixed-time")
    )

    assert result.status is RunStatus.TIMED_OUT
    assert result.stop_reason == "time_budget_exhausted"
    assert [step.role for step in result.steps] == ["planner"]
    assert scripts[RoleName.RETRIEVER].calls == 0


def test_fixed_records_each_roles_wall_time_in_shared_usage(case, tmp_path, budget):
    roles, _ = scripted_roles(["approved"])
    readings = iter([0.0, 0.0, 1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 4.0, 4.0])

    result = FixedMultiAgentArchitecture(roles=roles, clock=lambda: next(readings)).run(
        case, tmp_path, budget, RunContext(run_id="fixed-role-time")
    )

    assert result.usage.duration_ms == 4_000
    assert {role: usage.duration_ms for role, usage in result.role_usage.items()} == {
        RoleName.PLANNER: 1_000,
        RoleName.RETRIEVER: 1_000,
        RoleName.CODER: 1_000,
        RoleName.REVIEWER: 1_000,
    }


def test_fixed_trace_usage_matches_validated_role_delta(case, tmp_path, budget):
    role_usage = Usage(model_calls=1, tool_calls=2, input_tokens=31, output_tokens=7, cost_usd=0.02)
    roles, _ = scripted_roles(["approved"], planner_usage=role_usage)

    result = FixedMultiAgentArchitecture(roles=roles).run(
        case, tmp_path, budget, RunContext(run_id="fixed-trace-usage")
    )

    planner_step = result.steps[0]
    assert planner_step.input_tokens == role_usage.input_tokens
    assert planner_step.output_tokens == role_usage.output_tokens
    assert planner_step.cost_usd == role_usage.cost_usd
