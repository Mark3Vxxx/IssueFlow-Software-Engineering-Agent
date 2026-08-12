from __future__ import annotations

from collections import deque

import pytest
from pydantic import ValidationError

from issueflow.agent import ToolExecutor
from issueflow.architectures.base import ArchitectureKind, RoleName, RunContext
from issueflow.architectures.dynamic import (
    DynamicSupervisorArchitecture,
    SupervisorDecision,
)
from issueflow.architectures.roles import RoleSet
from issueflow.architectures.state import (
    CoderOutput,
    EvidenceBundle,
    EvidenceItem,
    PlanOutput,
    ReviewOutput,
)
from issueflow.models import BenchmarkCase, Budget, RunStatus, TraceStep, Usage
from issueflow.structured_model import (
    ModelProtocolError,
    StructuredCompletion,
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
        return self.updates.popleft(), role_step(self.role)


def scripted_roles(
    *,
    planner_calls: int = 1,
    evidence: list[EvidenceItem] | None = None,
    public_test_result: str = "exit_code=0\n1 passed",
    reviewer_calls: int = 2,
    planner_usage: Usage | None = None,
) -> tuple[RoleSet, dict[RoleName, ScriptedRole]]:
    scripts = {
        RoleName.PLANNER: ScriptedRole(
            RoleName.PLANNER,
            [
                {
                    "plan": PlanOutput(steps=["Inspect the failing implementation."]),
                    "usage": planner_usage or Usage(model_calls=1),
                }
                for _ in range(planner_calls)
            ],
        ),
        RoleName.RETRIEVER: ScriptedRole(
            RoleName.RETRIEVER,
            [
                {
                    "evidence": evidence
                    if evidence is not None
                    else [EvidenceItem(path="engine.py", line=1, summary="Wrong return value.")],
                    "usage": Usage(model_calls=1),
                }
            ],
        ),
        RoleName.CODER: ScriptedRole(
            RoleName.CODER,
            [
                {
                    "current_diff": "-broken\n+fixed\n",
                    "public_test_result": public_test_result,
                    "usage": Usage(model_calls=1),
                }
            ],
        ),
        RoleName.REVIEWER: ScriptedRole(
            RoleName.REVIEWER,
            [
                {
                    "review_feedback": ReviewOutput(
                        status="approved", feedback="The patch is focused."
                    ),
                    "usage": Usage(model_calls=1),
                }
                for _ in range(reviewer_calls)
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


class ScriptedSupervisor:
    def __init__(
        self,
        routes: list[str],
        *,
        usage: Usage | None = None,
        error: ModelProtocolError | None = None,
    ) -> None:
        self.routes = deque(routes)
        self.usage = usage or Usage(model_calls=1)
        self.error = error
        self.calls: list[dict[str, object]] = []

    def complete(self, system_prompt, payload, schema):
        assert schema is SupervisorDecision
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        route = self.routes.popleft()
        return StructuredCompletion(
            value=SupervisorDecision(next_role=route, reason=f"route to {route}"),
            usage=self.usage,
        )


def run_dynamic(model, roles, case, tmp_path, budget, run_id="dynamic-test"):
    return DynamicSupervisorArchitecture(model=model, roles=roles).run(
        case, tmp_path, budget, RunContext(run_id=run_id)
    )


def test_supervisor_decision_rejects_extra_control_fields():
    with pytest.raises(ValidationError):
        SupervisorDecision.model_validate(
            {
                "next_role": "planner",
                "reason": "plan first",
                "tools": [{"tool": "run_tests"}],
                "budget": {"max_seconds": 9_999},
            }
        )


def test_dynamic_records_each_supervisor_decision(case, tmp_path, budget):
    roles, _ = scripted_roles()
    supervisor = ScriptedSupervisor(["planner", "retriever", "coder", "reviewer", "stop"])

    result = run_dynamic(supervisor, roles, case, tmp_path, budget)

    assert result.architecture is ArchitectureKind.DYNAMIC
    assert result.status is RunStatus.SUCCEEDED
    assert result.stop_reason == "supervisor_stopped"
    assert [step.role for step in result.steps if step.step_type == "route"] == [
        "supervisor",
        "supervisor",
        "supervisor",
        "supervisor",
        "supervisor",
    ]
    assert [step.role for step in result.steps if step.step_type == "role"] == [
        "planner",
        "retriever",
        "coder",
        "reviewer",
    ]
    assert [step.sequence for step in result.steps] == list(range(1, 10))
    assert result.route_count == 5
    assert result.usage.model_calls == 9
    assert result.role_usage[RoleName.SUPERVISOR].model_calls == 5
    assert all("messages" not in payload for payload in supervisor.calls)


@pytest.mark.parametrize(
    "routes",
    [
        ["coder"],
        ["planner", "coder"],
        ["retriever", "coder"],
    ],
)
def test_dynamic_rejects_coder_before_plan_and_evidence(case, tmp_path, budget, routes):
    roles, scripts = scripted_roles(evidence=[])

    result = run_dynamic(
        ScriptedSupervisor(routes), roles, case, tmp_path, budget, run_id=str(routes)
    )

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "invalid_supervisor_route"
    assert scripts[RoleName.CODER].calls == 0


def test_dynamic_rejects_reviewer_before_non_empty_diff(case, tmp_path, budget):
    roles, scripts = scripted_roles()

    result = run_dynamic(ScriptedSupervisor(["reviewer"]), roles, case, tmp_path, budget)

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "invalid_supervisor_route"
    assert scripts[RoleName.REVIEWER].calls == 0


def test_dynamic_rejects_a_third_reviewer_invocation(case, tmp_path, budget):
    roles, scripts = scripted_roles()
    supervisor = ScriptedSupervisor(
        ["planner", "retriever", "coder", "reviewer", "reviewer", "reviewer"]
    )

    result = run_dynamic(supervisor, roles, case, tmp_path, budget)

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "invalid_supervisor_route"
    assert scripts[RoleName.REVIEWER].calls == 2
    assert result.route_count == 6


def test_dynamic_rejects_stop_before_public_verification_succeeds(case, tmp_path, budget):
    roles, scripts = scripted_roles()

    result = run_dynamic(ScriptedSupervisor(["stop"]), roles, case, tmp_path, budget)

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "invalid_supervisor_route"
    assert all(script.calls == 0 for script in scripts.values())


class ClaimedVerificationModel:
    def __init__(self) -> None:
        self.routes = deque(["planner", "retriever", "coder", "stop"])

    def complete(self, system_prompt, payload, schema):
        if schema is SupervisorDecision:
            route = self.routes.popleft()
            value = SupervisorDecision(next_role=route, reason=f"route to {route}")
        elif schema is PlanOutput:
            value = PlanOutput(steps=["Inspect engine.py"])
        elif schema is EvidenceBundle:
            value = EvidenceBundle(
                items=[EvidenceItem(path="engine.py", line=1, summary="Wrong return value.")]
            )
        elif schema is CoderOutput:
            value = CoderOutput(
                current_diff="-broken\n+fixed\n",
                public_test_result="exit_code=0\nfabricated",
            )
        else:
            raise AssertionError(f"unexpected schema: {schema}")
        return StructuredCompletion(value=value, usage=Usage(model_calls=1))


def test_dynamic_rejects_unexecuted_production_model_verification_claim(case, tmp_path, budget):
    model = ClaimedVerificationModel()
    tools = ToolExecutor(tmp_path, case, Sandbox())

    result = DynamicSupervisorArchitecture(model=model, tools=tools).run(
        case,
        tmp_path,
        budget,
        RunContext(run_id="dynamic-unexecuted-verification-claim"),
    )

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "invalid_supervisor_route"
    assert result.usage.tool_calls == 0
    assert result.route_count == 4


def test_dynamic_stops_at_twelve_routes(case, tmp_path, budget):
    roles, scripts = scripted_roles(planner_calls=12)
    supervisor = ScriptedSupervisor(["planner"] * 13)

    result = run_dynamic(supervisor, roles, case, tmp_path, budget)

    assert result.status is RunStatus.BUDGET_EXHAUSTED
    assert result.stop_reason == "supervisor_route_budget_exhausted"
    assert result.route_count == 12
    assert len(supervisor.calls) == 12
    assert scripts[RoleName.PLANNER].calls == 12


def test_dynamic_charges_supervisor_usage_to_the_shared_budget(case, tmp_path, budget):
    roles, scripts = scripted_roles()
    supervisor = ScriptedSupervisor(["planner"], usage=Usage(model_calls=1, input_tokens=1_001))

    result = run_dynamic(supervisor, roles, case, tmp_path, budget)

    assert result.status is RunStatus.BUDGET_EXHAUSTED
    assert result.stop_reason == "input_token_budget_exhausted"
    assert result.usage.input_tokens == 1_001
    assert result.role_usage[RoleName.SUPERVISOR].input_tokens == 1_001
    assert scripts[RoleName.PLANNER].calls == 0


def test_dynamic_stops_after_a_role_exhausts_the_shared_budget(case, tmp_path, budget):
    roles, scripts = scripted_roles(planner_usage=Usage(model_calls=1, output_tokens=1_001))
    supervisor = ScriptedSupervisor(["planner", "retriever"])

    result = run_dynamic(supervisor, roles, case, tmp_path, budget)

    assert result.status is RunStatus.BUDGET_EXHAUSTED
    assert result.stop_reason == "output_token_budget_exhausted"
    assert len(supervisor.calls) == 1
    assert scripts[RoleName.RETRIEVER].calls == 0
    assert result.role_usage[RoleName.PLANNER].output_tokens == 1_001


def test_dynamic_normalizes_malformed_role_output(case, tmp_path, budget):
    roles, _ = scripted_roles()
    roles = RoleSet(
        plan=lambda state: (None, None),
        retrieve=roles.retrieve,
        code=roles.code,
        review=roles.review,
    )

    result = run_dynamic(ScriptedSupervisor(["planner"]), roles, case, tmp_path, budget)

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "invalid_role_output"
    assert [step.role for step in result.steps] == ["supervisor", "planner"]


def test_dynamic_normalizes_supervisor_protocol_failure_without_leaking_details(
    case, tmp_path, budget
):
    roles, scripts = scripted_roles()
    secret = "top-secret-provider-detail"
    supervisor = ScriptedSupervisor(
        [],
        error=ModelProtocolError(secret, Usage(model_calls=1, input_tokens=7, output_tokens=3)),
    )

    result = run_dynamic(supervisor, roles, case, tmp_path, budget)

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "model_protocol_failure"
    assert result.usage.model_calls == 1
    assert result.usage.input_tokens == 7
    assert result.role_usage[RoleName.SUPERVISOR].output_tokens == 3
    assert result.steps[0].step_type == "model"
    assert secret not in result.model_dump_json()
    assert all(script.calls == 0 for script in scripts.values())


class MalformedDecisionSupervisor:
    def complete(self, system_prompt, payload, schema):
        class Completion:
            def __init__(self) -> None:
                self.value = {"next_role": "planner", "reason": ""}
                self.usage = Usage(
                    model_calls=1,
                    input_tokens=31,
                    output_tokens=17,
                    cost_usd=0.25,
                )

        return Completion()


def test_dynamic_preserves_usage_from_malformed_supervisor_decision(case, tmp_path, budget):
    roles, scripts = scripted_roles()

    result = run_dynamic(MalformedDecisionSupervisor(), roles, case, tmp_path, budget)

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "invalid_supervisor_output"
    assert result.route_count == 0
    assert result.usage.model_calls == 1
    assert result.usage.input_tokens == 31
    assert result.usage.output_tokens == 17
    assert result.usage.cost_usd == 0.25
    assert result.role_usage[RoleName.SUPERVISOR].model_calls == 1
    assert result.role_usage[RoleName.SUPERVISOR].input_tokens == 31
    assert result.role_usage[RoleName.SUPERVISOR].output_tokens == 17
    assert result.role_usage[RoleName.SUPERVISOR].cost_usd == 0.25
    assert result.steps[0].step_type == "model"
    assert all(script.calls == 0 for script in scripts.values())


def test_dynamic_trace_usage_matches_validated_role_delta(case, tmp_path, budget):
    role_usage = Usage(
        model_calls=1,
        tool_calls=2,
        input_tokens=31,
        output_tokens=7,
        cost_usd=0.02,
    )
    roles, _ = scripted_roles(planner_usage=role_usage)
    supervisor = ScriptedSupervisor(["planner", "retriever", "coder", "reviewer", "stop"])

    result = run_dynamic(supervisor, roles, case, tmp_path, budget)

    planner_step = next(step for step in result.steps if step.role == RoleName.PLANNER)
    assert planner_step.input_tokens == role_usage.input_tokens
    assert planner_step.output_tokens == role_usage.output_tokens
    assert planner_step.cost_usd == role_usage.cost_usd


class Sandbox:
    def run(self, workspace, command, timeout_seconds):
        raise AssertionError("invalid execution context must not reach the sandbox")


class ProductionModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system_prompt, payload, schema):
        self.calls += 1
        if schema is SupervisorDecision:
            value = SupervisorDecision(next_role="planner", reason="plan")
        elif schema is PlanOutput:
            value = PlanOutput(steps=["Inspect engine.py"])
        elif schema is EvidenceBundle:
            value = EvidenceBundle()
        elif schema is CoderOutput:
            value = CoderOutput(current_diff="", public_test_result="")
        else:
            value = ReviewOutput(status="approved", feedback="focused")
        return StructuredCompletion(value=value, usage=Usage(model_calls=1))


def test_dynamic_checks_workspace_of_prebuilt_production_roles(case, tmp_path, budget):
    role_workspace = tmp_path / "roles"
    role_workspace.mkdir()
    requested_workspace = tmp_path / "requested"
    requested_workspace.mkdir()
    role_model = ProductionModel()
    supervisor = ScriptedSupervisor(["planner"])
    roles = RoleSet.production(role_model, ToolExecutor(role_workspace, case, Sandbox()))

    result = DynamicSupervisorArchitecture(model=supervisor, roles=roles).run(
        case,
        requested_workspace,
        budget,
        RunContext(run_id="dynamic-workspace-mismatch"),
    )

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "workspace_mismatch"
    assert supervisor.calls == []
    assert role_model.calls == 0


def test_dynamic_rejects_mixed_production_and_injected_roles(case, tmp_path, budget):
    production_model = ProductionModel()
    production = RoleSet.production(production_model, ToolExecutor(tmp_path, case, Sandbox()))
    injected, scripts = scripted_roles()
    mixed = RoleSet(
        plan=injected.plan,
        retrieve=production.retrieve,
        code=injected.code,
        review=injected.review,
    )
    supervisor = ScriptedSupervisor(["retriever"])

    result = run_dynamic(supervisor, mixed, case, tmp_path, budget)

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "invalid_role_set"
    assert supervisor.calls == []
    assert production_model.calls == 0
    assert all(script.calls == 0 for script in scripts.values())
