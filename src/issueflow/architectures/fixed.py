"""Fixed Planner → Retriever → Coder → Reviewer LangGraph workflow."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from time import monotonic

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from issueflow.agent import ToolExecutor
from issueflow.architectures.base import (
    ArchitectureKind,
    ArchitectureResult,
    RoleName,
    RunContext,
)
from issueflow.architectures.roles import RoleCallable, RoleSet
from issueflow.architectures.state import (
    WorkflowState,
    budget_stop_reason,
    validate_workflow_state,
)
from issueflow.models import BenchmarkCase, Budget, RunStatus, TraceStep, Usage
from issueflow.structured_model import StructuredModel

_ROLE_UPDATE_KEYS: dict[RoleName, frozenset[str]] = {
    RoleName.PLANNER: frozenset({"plan", "stop_reason"}),
    RoleName.RETRIEVER: frozenset({"evidence", "stop_reason"}),
    RoleName.CODER: frozenset({"current_diff", "public_test_result", "stop_reason"}),
    RoleName.REVIEWER: frozenset({"review_feedback", "stop_reason"}),
}


class FixedMultiAgentArchitecture:
    """Run the four exact roles with one optional Reviewer-requested rework."""

    def __init__(
        self,
        roles: RoleSet | None = None,
        *,
        model: StructuredModel | None = None,
        tools: ToolExecutor | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if roles is None:
            if model is None or tools is None:
                raise ValueError("provide roles or both model and tools")
            roles = RoleSet.production(model, tools, clock=clock)
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

        def elapsed_seconds() -> float:
            return max(0.0, self.clock() - started_at)

        def finish(
            status: RunStatus,
            stop_reason: str,
            *,
            final_state: WorkflowState | None = None,
        ) -> ArchitectureResult:
            usage = latest_usage if final_state is None else final_state["usage"]
            role_usage = latest_role_usage if final_state is None else final_state["role_usage"]
            route_count = latest_route_count if final_state is None else final_state["route_count"]
            elapsed = elapsed_seconds()
            usage = usage.model_copy(update={"duration_ms": int(elapsed * 1_000)})
            feedback = None if final_state is None else final_state["review_feedback"]
            return ArchitectureResult(
                architecture=ArchitectureKind.FIXED,
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
            executor for executor in (self.roles.executor, self.tools) if executor is not None
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

        def node(role: RoleName, callback: RoleCallable):
            def execute(state: WorkflowState) -> dict[str, object]:
                nonlocal latest_usage, latest_role_usage, latest_route_count
                role_started_seconds = elapsed_seconds()
                before_reason = budget_stop_reason(state["usage"], budget, role_started_seconds)
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
                    0, int((role_finished_seconds - role_started_seconds) * 1_000)
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
                        "route_count": state["route_count"] + 1,
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
                        "route_count": state["route_count"] + 1,
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
                budget_reason = budget_stop_reason(usage, budget, role_finished_seconds)
                if budget_reason is not None:
                    stop_reason = budget_reason
                elif role is RoleName.REVIEWER and stop_reason is None:
                    feedback = update.get("review_feedback")
                    status = None if feedback is None else feedback.status
                    if status == "approved":
                        stop_reason = "review_approved"
                    elif status == "failed":
                        stop_reason = "failed"
                    elif status == "needs_changes" and rework_count > 0:
                        stop_reason = "review_loop_exhausted"
                if stop_reason is not None:
                    update["stop_reason"] = stop_reason

                step = raw_step.model_copy(
                    update={
                        "sequence": len(steps) + 1,
                        "role": role,
                        "step_type": "role",
                        "duration_ms": role_duration_ms,
                        "input_tokens": delta.input_tokens,
                        "output_tokens": delta.output_tokens,
                        "cost_usd": delta.cost_usd,
                    }
                )
                steps.append(step)
                latest_usage = validated["usage"]
                latest_role_usage = validated["role_usage"]
                latest_route_count = validated["route_count"]
                return update

            return execute

        graph = StateGraph(WorkflowState)
        graph.add_node("planner", node(RoleName.PLANNER, run_roles.plan))
        graph.add_node("retriever", node(RoleName.RETRIEVER, run_roles.retrieve))
        graph.add_node("coder", node(RoleName.CODER, run_roles.code))
        graph.add_node("reviewer", node(RoleName.REVIEWER, run_roles.review))
        graph.add_edge(START, "planner")
        graph.add_conditional_edges(
            "planner",
            _continue_or_end,
            {"continue": "retriever", "end": END},
        )
        graph.add_conditional_edges(
            "retriever",
            _continue_or_end,
            {"continue": "coder", "end": END},
        )
        graph.add_conditional_edges(
            "coder",
            _continue_or_end,
            {"continue": "reviewer", "end": END},
        )
        graph.add_conditional_edges(
            "reviewer",
            _review_route,
            {"coder": "coder", "end": END},
        )
        compiled = graph.compile(checkpointer=InMemorySaver())

        try:
            final_state = validate_workflow_state(
                compiled.invoke(
                    initial_state,
                    {
                        "configurable": {"thread_id": context.run_id},
                        "recursion_limit": 12,
                    },
                )
            )
        except GraphRecursionError:
            return finish(RunStatus.FAILED, "review_loop_exhausted")
        except (RuntimeError, TypeError, ValueError):
            return finish(RunStatus.FAILED, "graph_execution_failure")

        reason = final_state["stop_reason"] or "failed"
        return finish(_status_for_reason(reason), reason, final_state=final_state)


def _continue_or_end(state: WorkflowState) -> str:
    return "end" if state["stop_reason"] is not None else "continue"


def _review_route(state: WorkflowState) -> str:
    if state["stop_reason"] is not None:
        return "end"
    feedback = state["review_feedback"]
    if feedback is not None and feedback.status == "needs_changes" and state["rework_count"] == 0:
        return "coder"
    return "end"


def _status_for_reason(reason: str) -> RunStatus:
    if reason == "review_approved":
        return RunStatus.SUCCEEDED
    if reason == "time_budget_exhausted":
        return RunStatus.TIMED_OUT
    if reason.endswith("_budget_exhausted"):
        return RunStatus.BUDGET_EXHAUSTED
    return RunStatus.FAILED


def _add_usage(left: Usage, right: Usage) -> Usage:
    return Usage(
        **{field: getattr(left, field) + getattr(right, field) for field in Usage.model_fields}
    )
