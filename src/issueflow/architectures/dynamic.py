"""Bounded Dynamic Supervisor LangGraph workflow."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from time import monotonic
from typing import Literal

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from issueflow.agent import ToolExecutor
from issueflow.architectures.base import (
    ArchitectureKind,
    ArchitectureResult,
    RoleName,
    RunContext,
)
from issueflow.architectures.fixed import _ROLE_UPDATE_KEYS, _add_usage
from issueflow.architectures.roles import (
    RoleCallable,
    RoleSet,
    _model_usage,
    _role_payload,
)
from issueflow.architectures.state import (
    WorkflowState,
    budget_stop_reason,
    validate_workflow_state,
)
from issueflow.models import BenchmarkCase, Budget, RunStatus, TraceStep, Usage
from issueflow.structured_model import ModelProtocolError, StructuredModel

MAX_SUPERVISOR_ROUTES = 12
MAX_REVIEWER_INVOCATIONS = 2

SUPERVISOR_PROMPT = """You are the Supervisor in a bounded software-repair workflow.
Choose exactly one next_role from planner, retriever, coder, reviewer, stop, or fail.
Route Coder only after a plan and source evidence exist. Route Reviewer only after a non-empty
diff exists. Stop only after the registered public verification passed. Give a concise reason.
You cannot execute tools, change budgets, declare functional success, or skip independent
RunService verification.
"""


class SupervisorDecision(BaseModel):
    """One schema-constrained, advisory routing decision."""

    model_config = ConfigDict(extra="forbid")

    next_role: Literal["planner", "retriever", "coder", "reviewer", "stop", "fail"]
    reason: str = Field(min_length=1, max_length=500)


class DynamicSupervisorArchitecture:
    """Route four fixed-boundary roles through one deterministic Supervisor guard."""

    def __init__(
        self,
        model: StructuredModel,
        roles: RoleSet | None = None,
        *,
        tools: ToolExecutor | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if roles is None:
            if tools is None:
                raise ValueError("provide roles or tools for production roles")
            roles = RoleSet.production(model, tools, clock=clock)
        self.model = model
        self.roles = roles
        self.tools = tools
        self.clock = clock

    def run(
        self,
        case: BenchmarkCase,
        workspace: Path,
        budget: Budget,
        context: RunContext,
    ) -> ArchitectureResult:
        """Invoke one bounded graph using the run ID as its checkpoint thread ID."""
        started_at = self.clock()
        steps: list[TraceStep] = []
        latest_usage = Usage()
        latest_role_usage: dict[RoleName, Usage] = {}
        latest_route_count = 0
        next_route = "end"

        def elapsed_seconds() -> float:
            return max(0.0, self.clock() - started_at)

        def finish(
            status: RunStatus,
            stop_reason: str,
            *,
            final_state: WorkflowState | None = None,
        ) -> ArchitectureResult:
            usage = latest_usage if final_state is None else final_state["usage"]
            role_usage = (
                latest_role_usage if final_state is None else final_state["role_usage"]
            )
            route_count = (
                latest_route_count if final_state is None else final_state["route_count"]
            )
            elapsed = elapsed_seconds()
            usage = usage.model_copy(update={"duration_ms": int(elapsed * 1_000)})
            feedback = None if final_state is None else final_state["review_feedback"]
            return ArchitectureResult(
                architecture=ArchitectureKind.DYNAMIC,
                status=status,
                stop_reason=stop_reason,
                steps=steps,
                usage=usage,
                role_usage=role_usage,
                route_count=route_count,
                final_message="" if feedback is None else feedback.feedback,
            )

        if not self.roles.has_valid_composition:
            return finish(RunStatus.FAILED, "invalid_role_set")

        executors = [
            executor
            for executor in (self.roles.executor, self.tools)
            if executor is not None
        ]
        for executor in executors:
            if workspace.resolve() != executor.workspace:
                return finish(RunStatus.FAILED, "workspace_mismatch")
            if executor.case != case:
                return finish(RunStatus.FAILED, "case_mismatch")

        run_roles = self.roles.for_run(budget, elapsed_seconds)
        initial_state = validate_workflow_state(
            {
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
        )

        def supervisor(state: WorkflowState) -> dict[str, object]:
            nonlocal latest_usage, latest_role_usage, latest_route_count, next_route
            next_route = "end"
            if state["stop_reason"] is not None:
                return {}

            elapsed = elapsed_seconds()
            before_reason = budget_stop_reason(state["usage"], budget, elapsed)
            if before_reason is not None:
                return {"stop_reason": before_reason}
            if state["route_count"] >= MAX_SUPERVISOR_ROUTES:
                return {"stop_reason": "supervisor_route_budget_exhausted"}

            supervisor_started_seconds = elapsed
            decision: SupervisorDecision | None = None
            failure_reason: str | None = None
            delta = Usage(model_calls=1)
            try:
                completion = self.model.complete(
                    SUPERVISOR_PROMPT,
                    _supervisor_payload(state, budget, elapsed),
                    SupervisorDecision,
                )
                delta = _model_usage(Usage.model_validate(completion.usage))
                decision = SupervisorDecision.model_validate(completion.value)
            except ModelProtocolError as error:
                delta = _model_usage(error.usage)
                failure_reason = "model_protocol_failure"
            except (AttributeError, TypeError, ValidationError, ValueError):
                failure_reason = "invalid_supervisor_output"

            supervisor_finished_seconds = elapsed_seconds()
            duration_ms = max(
                0,
                int(
                    (supervisor_finished_seconds - supervisor_started_seconds)
                    * 1_000
                ),
            )
            delta = delta.model_copy(update={"duration_ms": duration_ms})
            usage = _add_usage(state["usage"], delta)
            role_usage = dict(state["role_usage"])
            role_usage[RoleName.SUPERVISOR] = _add_usage(
                role_usage.get(RoleName.SUPERVISOR, Usage()), delta
            )
            route_count = state["route_count"] + (decision is not None)

            if failure_reason is None:
                failure_reason = budget_stop_reason(
                    usage, budget, supervisor_finished_seconds
                )
            if failure_reason is None and decision is not None:
                failure_reason = _invalid_route_reason(decision.next_role, state)

            update: dict[str, object] = {
                "usage": usage,
                "role_usage": role_usage,
                "route_count": route_count,
            }
            if failure_reason is not None:
                update["stop_reason"] = failure_reason
            elif decision is not None and decision.next_role == "stop":
                update["stop_reason"] = "supervisor_stopped"
            elif decision is not None and decision.next_role == "fail":
                update["stop_reason"] = "supervisor_failed"
            elif decision is not None:
                next_route = decision.next_role

            summary = (
                failure_reason
                if decision is None
                else decision.reason
            )
            status = (
                "failed"
                if failure_reason is not None
                or (decision is not None and decision.next_role == "fail")
                else "completed"
            )
            steps.append(
                TraceStep(
                    sequence=len(steps) + 1,
                    role=RoleName.SUPERVISOR,
                    step_type="route" if decision is not None else "model",
                    input_summary="bounded workflow state and remaining budget",
                    output_summary=summary,
                    status=status,
                    duration_ms=duration_ms,
                    input_tokens=delta.input_tokens,
                    output_tokens=delta.output_tokens,
                    cost_usd=delta.cost_usd,
                )
            )
            latest_usage = usage
            latest_role_usage = role_usage
            latest_route_count = route_count
            return update

        def role_node(role: RoleName, callback: RoleCallable):
            def execute(state: WorkflowState) -> dict[str, object]:
                nonlocal latest_usage, latest_role_usage, latest_route_count
                role_started_seconds = elapsed_seconds()
                before_reason = budget_stop_reason(
                    state["usage"], budget, role_started_seconds
                )
                if before_reason is not None:
                    return {"stop_reason": before_reason}

                try:
                    result = callback(state)
                    if not isinstance(result, tuple) or len(result) != 2:
                        raise TypeError("role result must contain update and trace step")
                    raw_update, raw_step = result
                    if not isinstance(raw_update, dict):
                        raise TypeError("role update must be a dictionary")
                    if not isinstance(raw_step, TraceStep):
                        raise TypeError("role trace must be a TraceStep")
                    delta = Usage.model_validate(raw_update.get("usage", Usage()))
                except Exception:  # noqa: BLE001 - normalize the injected role boundary
                    raw_update = {"stop_reason": "invalid_role_output"}
                    raw_step = TraceStep(
                        sequence=1,
                        role=role,
                        step_type="role",
                        input_summary="bounded workflow state",
                        output_summary="invalid role output",
                        status="failed",
                    )
                    delta = Usage()

                role_finished_seconds = elapsed_seconds()
                role_duration_ms = max(
                    0,
                    int((role_finished_seconds - role_started_seconds) * 1_000),
                )
                delta = delta.model_copy(update={"duration_ms": role_duration_ms})
                usage = _add_usage(state["usage"], delta)
                role_usage = dict(state["role_usage"])
                role_usage[role] = _add_usage(role_usage.get(role, Usage()), delta)
                history = [*state["role_history"], role]
                rework_count = state["rework_count"]
                if role is RoleName.CODER and RoleName.CODER in state["role_history"]:
                    rework_count += 1

                update = {
                    key: value
                    for key, value in raw_update.items()
                    if key in _ROLE_UPDATE_KEYS[role]
                }
                update.update(
                    {
                        "usage": usage,
                        "role_usage": role_usage,
                        "role_history": history,
                        "rework_count": rework_count,
                    }
                )
                candidate = {**state, **update}
                try:
                    validated = validate_workflow_state(candidate)
                except ValidationError:
                    update = {
                        "usage": usage,
                        "role_usage": role_usage,
                        "role_history": history[:50],
                        "rework_count": rework_count,
                        "stop_reason": "invalid_role_output",
                    }
                    raw_step = raw_step.model_copy(
                        update={
                            "output_summary": "invalid role output",
                            "status": "failed",
                        }
                    )
                    validated = validate_workflow_state({**state, **update})
                else:
                    for key in _ROLE_UPDATE_KEYS[role]:
                        if key in update:
                            update[key] = validated[key]

                stop_reason = update.get("stop_reason")
                budget_reason = budget_stop_reason(
                    usage, budget, role_finished_seconds
                )
                if budget_reason is not None:
                    stop_reason = budget_reason
                if stop_reason is not None:
                    update["stop_reason"] = stop_reason

                steps.append(
                    raw_step.model_copy(
                        update={
                            "sequence": len(steps) + 1,
                            "role": role,
                            "step_type": "role",
                            "duration_ms": role_duration_ms,
                        }
                    )
                )
                latest_usage = validated["usage"]
                latest_role_usage = validated["role_usage"]
                latest_route_count = validated["route_count"]
                return update

            return execute

        graph = StateGraph(WorkflowState)
        graph.add_node("supervisor", supervisor)
        graph.add_node("planner", role_node(RoleName.PLANNER, run_roles.plan))
        graph.add_node("retriever", role_node(RoleName.RETRIEVER, run_roles.retrieve))
        graph.add_node("coder", role_node(RoleName.CODER, run_roles.code))
        graph.add_node("reviewer", role_node(RoleName.REVIEWER, run_roles.review))
        graph.add_edge(START, "supervisor")
        graph.add_conditional_edges(
            "supervisor",
            lambda state: "end" if state["stop_reason"] is not None else next_route,
            {
                "planner": "planner",
                "retriever": "retriever",
                "coder": "coder",
                "reviewer": "reviewer",
                "end": END,
            },
        )
        graph.add_edge("planner", "supervisor")
        graph.add_edge("retriever", "supervisor")
        graph.add_edge("coder", "supervisor")
        graph.add_edge("reviewer", "supervisor")
        compiled = graph.compile(checkpointer=InMemorySaver())

        try:
            final_state = validate_workflow_state(
                compiled.invoke(
                    initial_state,
                    {
                        "configurable": {"thread_id": context.run_id},
                        "recursion_limit": 30,
                    },
                )
            )
        except GraphRecursionError:
            return finish(
                RunStatus.BUDGET_EXHAUSTED,
                "supervisor_route_budget_exhausted",
            )
        except (RuntimeError, TypeError, ValueError):
            return finish(RunStatus.FAILED, "graph_execution_failure")

        reason = final_state["stop_reason"] or "failed"
        return finish(_status_for_reason(reason), reason, final_state=final_state)


def _invalid_route_reason(next_role: str, state: WorkflowState) -> str | None:
    if next_role == "coder" and (state["plan"] is None or not state["evidence"]):
        return "invalid_supervisor_route"
    if next_role == "reviewer" and not state["current_diff"].strip():
        return "invalid_supervisor_route"
    if (
        next_role == "reviewer"
        and state["role_history"].count(RoleName.REVIEWER)
        >= MAX_REVIEWER_INVOCATIONS
    ):
        return "invalid_supervisor_route"
    if next_role == "stop" and not _public_verification_passed(
        state["public_test_result"]
    ):
        return "invalid_supervisor_route"
    return None


def _public_verification_passed(result: str) -> bool:
    lines = result.splitlines()
    return bool(lines) and lines[0].strip() == "exit_code=0"


def _supervisor_payload(
    state: WorkflowState,
    budget: Budget,
    elapsed_seconds: float,
) -> dict[str, object]:
    payload = _role_payload(state)
    usage = state["usage"]
    payload["remaining_budget"] = {
        "tool_calls": max(0, budget.max_tool_calls - usage.tool_calls),
        "patch_attempts": max(0, budget.max_patch_attempts - usage.patch_attempts),
        "seconds": max(0.0, budget.max_seconds - elapsed_seconds),
        "input_tokens": max(0, budget.max_input_tokens - usage.input_tokens),
        "output_tokens": max(0, budget.max_output_tokens - usage.output_tokens),
        "cost_usd": max(0.0, budget.max_cost_usd - usage.cost_usd),
        "supervisor_routes": max(
            0, MAX_SUPERVISOR_ROUTES - state["route_count"]
        ),
        "reviewer_invocations": max(
            0,
            MAX_REVIEWER_INVOCATIONS
            - state["role_history"].count(RoleName.REVIEWER),
        ),
    }
    return payload


def _status_for_reason(reason: str) -> RunStatus:
    if reason == "supervisor_stopped":
        return RunStatus.SUCCEEDED
    if reason == "time_budget_exhausted":
        return RunStatus.TIMED_OUT
    if reason.endswith("_budget_exhausted"):
        return RunStatus.BUDGET_EXHAUSTED
    return RunStatus.FAILED
