"""Reusable structured role callables for Fixed and Dynamic workflows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from subprocess import TimeoutExpired
from time import monotonic
from typing import TypeAlias

from pydantic import BaseModel

from issueflow.agent import ModelAction, ToolExecutor
from issueflow.architectures.base import RoleName
from issueflow.architectures.state import (
    CoderOutput,
    EvidenceBundle,
    EvidenceItem,
    PlanOutput,
    ReviewOutput,
    WorkflowState,
    budget_stop_reason,
)
from issueflow.models import Budget, TraceStep, Usage
from issueflow.structured_model import ModelProtocolError, StructuredModel

RoleUpdate: TypeAlias = dict[str, object]
RoleCallable: TypeAlias = Callable[[WorkflowState], tuple[RoleUpdate, TraceStep]]


@dataclass(frozen=True)
class _RoleFailure:
    update: RoleUpdate
    step: TraceStep


PLANNER_PROMPT = """You are the Planner in a bounded software-repair workflow.
Return at most six concrete steps with target files, validation conditions, and risks.
You have no tools. Do not request file reads, patches, tests, shell commands, or network access.
"""

RETRIEVER_PROMPT = """You are the Retriever in a bounded software-repair workflow.
Return compact source evidence. You may request only search or read_file through tool_calls.
Never request patches, tests, shell commands, network access, credentials, or hidden tests.
"""

CODER_PROMPT = """You are the Coder in a bounded software-repair workflow.
Produce a minimal repair. You may request only read_file, apply_patch, or run_tests through
tool_calls. Tests must use a command registered by the benchmark case. Never use shell or network.
"""

REVIEWER_PROMPT = """You are the advisory Reviewer in a bounded repair workflow.
Assess the issue, plan, evidence, bounded diff, and public-test summary. Return approved,
needs_changes, or failed with concise feedback. You have no tools and cannot apply patches.
Your opinion cannot declare deterministic functional success.
"""


@dataclass(frozen=True)
class RoleSet:
    """Four injectable state-to-update role callables with stable method names."""

    plan: RoleCallable
    retrieve: RoleCallable
    code: RoleCallable
    review: RoleCallable

    @classmethod
    def production(
        cls,
        model: StructuredModel,
        tools: ToolExecutor,
        clock: Callable[[], float] = monotonic,
        budget: Budget | None = None,
        elapsed_seconds: Callable[[], float] | None = None,
    ) -> RoleSet:
        """Build the four production roles from shared model and tool boundaries."""
        roles = _ProductionRoles(
            model=model,
            tools=tools,
            clock=clock,
            budget=budget,
            elapsed_seconds=elapsed_seconds,
        )
        return cls(
            plan=roles.plan,
            retrieve=roles.retrieve,
            code=roles.code,
            review=roles.review,
        )

    def for_run(
        self,
        budget: Budget,
        elapsed_seconds: Callable[[], float],
    ) -> RoleSet:
        """Bind a production set to one case budget; injected callables stay unchanged."""
        owner = self._production_owner()
        if owner is None:
            return self
        return self.production(
            owner.model,
            owner.tools,
            clock=owner.clock,
            budget=budget,
            elapsed_seconds=elapsed_seconds,
        )

    @property
    def workspace(self):
        """Expose a production set's executor workspace for the architecture safety check."""
        executor = self.executor
        return None if executor is None else executor.workspace

    @property
    def executor(self) -> ToolExecutor | None:
        """Expose the executor actually bound to all four production callables."""
        owner = self._production_owner()
        return None if owner is None else owner.tools

    @property
    def has_valid_composition(self) -> bool:
        """Accept fully injected roles or four production methods from one owner only."""
        owners = self._callback_owners()
        production_owners = [owner for owner in owners if isinstance(owner, _ProductionRoles)]
        if not production_owners:
            return True
        return len(production_owners) == 4 and all(
            owner is production_owners[0] for owner in production_owners[1:]
        )

    def _production_owner(self) -> _ProductionRoles | None:
        owners = self._callback_owners()
        owner = owners[0]
        if any(candidate is not owner for candidate in owners[1:]):
            return None
        return owner if isinstance(owner, _ProductionRoles) else None

    def _callback_owners(self) -> list[object | None]:
        return [
            getattr(callback, "__self__", None)
            for callback in (self.plan, self.retrieve, self.code, self.review)
        ]


@dataclass
class _ProductionRoles:
    model: StructuredModel
    tools: ToolExecutor
    clock: Callable[[], float]
    budget: Budget | None = None
    elapsed_seconds: Callable[[], float] | None = None

    def plan(self, state: WorkflowState) -> tuple[RoleUpdate, TraceStep]:
        started_at = self.clock()
        completion = self._complete(RoleName.PLANNER, PLANNER_PROMPT, state, PlanOutput)
        if isinstance(completion, _RoleFailure):
            return completion.update, completion.step
        output, usage = completion
        stop_reason = self._budget_reason(state, usage)
        if stop_reason is not None:
            return (
                {"plan": output, "usage": usage, "stop_reason": stop_reason},
                self._step(RoleName.PLANNER, stop_reason, usage, started_at, failed=True),
            )
        return (
            {"plan": output, "usage": usage},
            self._step(RoleName.PLANNER, "repair plan produced", usage, started_at),
        )

    def retrieve(self, state: WorkflowState) -> tuple[RoleUpdate, TraceStep]:
        started_at = self.clock()
        completion = self._complete(RoleName.RETRIEVER, RETRIEVER_PROMPT, state, EvidenceBundle)
        if isinstance(completion, _RoleFailure):
            return completion.update, completion.step
        output, usage = completion
        evidence = list(output.items)
        stop_reason = self._budget_reason(state, usage)
        for call in output.tool_calls:
            if stop_reason is not None:
                break
            stop_reason = self._tool_budget_reason(state, usage, call.tool)
            if stop_reason is not None:
                break
            usage = _add_usage(usage, _tool_usage(call.tool))
            try:
                observation = self.tools.execute(
                    ModelAction(tool=call.tool, arguments=call.arguments),
                    timeout_seconds=self._tool_timeout_seconds(),
                )
            except (TimeoutError, TimeoutExpired):
                stop_reason = "time_budget_exhausted"
                break
            except (
                FileNotFoundError,
                NotImplementedError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ):
                stop_reason = "retrieval_failure"
                break
            if len(evidence) < 20:
                line = call.arguments.get("start_line", 1)
                evidence.append(
                    EvidenceItem(
                        path=str(call.arguments.get("path", call.tool)),
                        line=line if isinstance(line, int) and line > 0 else 1,
                        summary=observation[:2_000],
                    )
                )
        update: RoleUpdate = {"evidence": evidence, "usage": usage}
        if stop_reason is not None:
            update["stop_reason"] = stop_reason
        return (
            update,
            self._step(
                RoleName.RETRIEVER,
                stop_reason or f"{len(evidence)} evidence items",
                usage,
                started_at,
                failed=stop_reason is not None,
            ),
        )

    def code(self, state: WorkflowState) -> tuple[RoleUpdate, TraceStep]:
        started_at = self.clock()
        completion = self._complete(RoleName.CODER, CODER_PROMPT, state, CoderOutput)
        if isinstance(completion, _RoleFailure):
            return completion.update, completion.step
        output, usage = completion
        public_test_result = ""
        stop_reason = self._budget_reason(state, usage)
        for call in output.tool_calls:
            if stop_reason is not None:
                break
            stop_reason = self._tool_budget_reason(state, usage, call.tool)
            if stop_reason is not None:
                break
            usage = _add_usage(usage, _tool_usage(call.tool))
            try:
                observation = self.tools.execute(
                    ModelAction(tool=call.tool, arguments=call.arguments),
                    timeout_seconds=self._tool_timeout_seconds(),
                )
            except (TimeoutError, TimeoutExpired):
                stop_reason = "time_budget_exhausted"
                break
            except (
                FileNotFoundError,
                NotImplementedError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ):
                stop_reason = (
                    "patch_application_failure"
                    if call.tool == "apply_patch"
                    else "coder_tool_failure"
                )
                break
            if call.tool == "run_tests":
                public_test_result = observation
        update: RoleUpdate = {
            "current_diff": output.current_diff,
            "public_test_result": public_test_result,
            "usage": usage,
        }
        if stop_reason is not None:
            update["stop_reason"] = stop_reason
        return (
            update,
            self._step(
                RoleName.CODER,
                stop_reason or "bounded patch attempt completed",
                usage,
                started_at,
                failed=stop_reason is not None,
            ),
        )

    def review(self, state: WorkflowState) -> tuple[RoleUpdate, TraceStep]:
        started_at = self.clock()
        completion = self._complete(RoleName.REVIEWER, REVIEWER_PROMPT, state, ReviewOutput)
        if isinstance(completion, _RoleFailure):
            return completion.update, completion.step
        output, usage = completion
        stop_reason = self._budget_reason(state, usage)
        if stop_reason is not None:
            return (
                {
                    "review_feedback": output,
                    "usage": usage,
                    "stop_reason": stop_reason,
                },
                self._step(RoleName.REVIEWER, stop_reason, usage, started_at, failed=True),
            )
        return (
            {"review_feedback": output, "usage": usage},
            self._step(RoleName.REVIEWER, output.feedback, usage, started_at),
        )

    def _complete(
        self,
        role: RoleName,
        prompt: str,
        state: WorkflowState,
        schema: type[BaseModel],
    ) -> tuple[BaseModel, Usage] | _RoleFailure:
        started_at = self.clock()
        try:
            completion = self.model.complete(prompt, _role_payload(state), schema)
        except ModelProtocolError as error:
            usage = _model_usage(error.usage)
            return _RoleFailure(
                update={"usage": usage, "stop_reason": "model_protocol_failure"},
                step=self._step(
                    role,
                    "model protocol failure",
                    usage,
                    started_at,
                    failed=True,
                ),
            )
        return completion.value, _model_usage(completion.usage)

    def _budget_reason(self, state: WorkflowState, role_usage: Usage) -> str | None:
        if self.budget is None or self.elapsed_seconds is None:
            return None
        return budget_stop_reason(
            _add_usage(state["usage"], role_usage),
            self.budget,
            self.elapsed_seconds(),
        )

    def _tool_budget_reason(
        self,
        state: WorkflowState,
        role_usage: Usage,
        tool: str,
    ) -> str | None:
        reason = self._budget_reason(state, role_usage)
        if reason is not None or self.budget is None:
            return reason
        usage = _add_usage(state["usage"], role_usage)
        if usage.tool_calls >= self.budget.max_tool_calls:
            return "tool_budget_exhausted"
        if tool == "apply_patch" and usage.patch_attempts >= self.budget.max_patch_attempts:
            return "patch_budget_exhausted"
        return None

    def _tool_timeout_seconds(self) -> int:
        if self.budget is None or self.elapsed_seconds is None:
            return 60
        return max(1, int(self.budget.max_seconds - self.elapsed_seconds()))

    def _step(
        self,
        role: RoleName,
        summary: str,
        usage: Usage,
        started_at: float,
        *,
        failed: bool = False,
    ) -> TraceStep:
        return TraceStep(
            sequence=1,
            role=role,
            step_type="role",
            input_summary="bounded workflow state",
            output_summary=summary[:2_000],
            status="failed" if failed else "completed",
            duration_ms=max(0, int((self.clock() - started_at) * 1_000)),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=usage.cost_usd,
        )


def _role_payload(state: WorkflowState) -> dict[str, object]:
    """Expose bounded state only; no checkpoint metadata or credentials are retained."""
    return {
        "case_id": state["case_id"],
        "issue": state["issue"],
        "plan": None if state["plan"] is None else state["plan"].model_dump(mode="json"),
        "evidence": [item.model_dump(mode="json") for item in state["evidence"]],
        "current_diff": state["current_diff"],
        "public_test_result": state["public_test_result"],
        "review_feedback": (
            None
            if state["review_feedback"] is None
            else state["review_feedback"].model_dump(mode="json")
        ),
    }


def _model_usage(usage: Usage) -> Usage:
    return usage.model_copy(update={"model_calls": 1})


def _tool_usage(tool: str) -> Usage:
    return Usage(
        tool_calls=1,
        patch_attempts=1 if tool == "apply_patch" else 0,
    )


def _add_usage(left: Usage, right: Usage) -> Usage:
    return Usage(
        **{field: getattr(left, field) + getattr(right, field) for field in Usage.model_fields}
    )
