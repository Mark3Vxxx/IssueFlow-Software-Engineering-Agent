"""Orchestrate one reproducible benchmark repair run."""

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from issueflow.architectures.base import (
    ArchitectureKind,
    ArchitectureRunner,
    RunContext,
)
from issueflow.models import BenchmarkCase, Budget, RunRecord, RunStatus, TraceStep, Usage
from issueflow.reviewer import Reviewer
from issueflow.sandbox import SandboxResult
from issueflow.trace_store import TraceStore


class WorkspacePreparer(Protocol):
    """Create an isolated faulty workspace for one run."""

    def prepare(self, case: BenchmarkCase, run_id: str) -> Path: ...


class SandboxRunner(Protocol):
    """Execute a registered command inside the sandbox."""

    def run(self, workspace: Path, command: str, timeout_seconds: int) -> SandboxResult: ...


class SandboxFactory(Protocol):
    """Resolve one case's environment into a case-scoped sandbox."""

    def for_case(self, case: BenchmarkCase) -> SandboxRunner: ...


ArchitectureFactory = Callable[
    [ArchitectureKind, BenchmarkCase, Path, SandboxRunner], ArchitectureRunner
]


class GitWorkspacePreparer:
    """Clone a pinned repository and establish the faulty state as the baseline."""

    def __init__(self, workspace_root: Path, catalog_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.catalog_root = catalog_root.resolve()

    def prepare(self, case: BenchmarkCase, run_id: str) -> Path:
        """Create a clean, isolated Git workspace at the benchmark's faulty state."""
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        workspace = self.workspace_root / run_id
        self._run_git(
            ["git", "clone", "--quiet", case.repository_url, str(workspace)],
            self.workspace_root,
        )
        self._run_git(["git", "checkout", "--quiet", case.revision], workspace)
        if case.fault_patch:
            patch_path = (self.catalog_root / case.fault_patch).resolve()
            try:
                patch_path.relative_to(self.catalog_root)
            except ValueError as error:
                raise ValueError("fault patch must stay inside catalog root") from error
            self._run_git(["git", "apply", str(patch_path)], workspace)
            self._run_git(["git", "add", "--all"], workspace)
            self._run_git(
                [
                    "git",
                    "-c",
                    "user.name=IssueFlow",
                    "-c",
                    "user.email=issueflow@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "IssueFlow fault baseline",
                ],
                workspace,
            )
        return workspace.resolve()

    @staticmethod
    def _run_git(command: list[str], cwd: Path) -> None:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "git command failed")


class RunService:
    """Coordinate workspace, sandbox, agent, review, and persistent evidence."""

    def __init__(
        self,
        catalog: dict[str, BenchmarkCase],
        store: TraceStore,
        workspace_preparer: WorkspacePreparer,
        sandbox_factory: SandboxFactory,
        architecture_factory: ArchitectureFactory,
        reviewer: Reviewer,
    ) -> None:
        self.catalog = catalog
        self.store = store
        self.workspace_preparer = workspace_preparer
        self.sandbox_factory = sandbox_factory
        self.architecture_factory = architecture_factory
        self.reviewer = reviewer

    def start(
        self,
        case_id: str,
        budget: Budget,
        architecture: ArchitectureKind = ArchitectureKind.SINGLE,
    ) -> RunRecord:
        """Execute and persist one benchmark repair attempt."""
        case = self.catalog[case_id]
        architecture = ArchitectureKind(architecture)
        run_id = f"run-{uuid4().hex}"
        self.store.create_run(
            RunRecord(
                id=run_id,
                case_id=case.id,
                architecture=architecture.value,
            )
        )
        self.store.start_run(run_id)
        sequence = 0
        total_usage = Usage()
        role_usage: dict[str, Usage] = {}

        def finish(
            status: RunStatus,
            stop_reason: str,
            *,
            functional_success: bool,
            review_status: str,
            review_reasons: list[str],
        ) -> RunRecord:
            self.store.finish_run(
                run_id,
                status,
                stop_reason,
                functional_success=functional_success,
                review_status=review_status,
                review_reasons=review_reasons,
                usage=total_usage,
                role_usage=role_usage,
            )
            return self.store.get_run(run_id)

        def append(
            step_type: str,
            output: str,
            status: str,
            input_summary: str,
            duration_ms: int = 0,
            *,
            role: str = "system",
            input_tokens: int = 0,
            output_tokens: int = 0,
            cost_usd: float = 0.0,
        ) -> None:
            nonlocal sequence
            sequence += 1
            self.store.append_step(
                run_id,
                TraceStep(
                    sequence=sequence,
                    role=role,
                    step_type=step_type,
                    input_summary=input_summary,
                    output_summary=output,
                    status=status,
                    duration_ms=duration_ms,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost_usd,
                ),
            )

        try:
            sandbox = self.sandbox_factory.for_case(case)
            workspace = self.workspace_preparer.prepare(case, run_id)
            reproduction = sandbox.run(
                workspace, case.reproduce_command, timeout_seconds=budget.max_seconds
            )
            total_usage = _add_usage(
                total_usage,
                Usage(duration_ms=reproduction.duration_ms),
            )
            append(
                "reproduction",
                self._command_output(reproduction),
                "failed_as_expected" if reproduction.returncode != 0 else "unexpected_pass",
                case.reproduce_command,
                reproduction.duration_ms,
            )
            if reproduction.timed_out:
                return finish(
                    RunStatus.TIMED_OUT,
                    "reproduction_timed_out",
                    functional_success=False,
                    review_status="skipped",
                    review_reasons=["reproduction_timed_out"],
                )
            if reproduction.returncode == 0:
                return finish(
                    RunStatus.FAILED,
                    "reproduction_did_not_fail",
                    functional_success=False,
                    review_status="skipped",
                    review_reasons=["reproduction_did_not_fail"],
                )
            if total_usage.duration_ms >= budget.max_seconds * 1_000:
                return finish(
                    RunStatus.TIMED_OUT,
                    "time_budget_exhausted",
                    functional_success=False,
                    review_status="skipped",
                    review_reasons=["time_budget_exhausted"],
                )

            architecture_result = self.architecture_factory(
                architecture, case, workspace, sandbox
            ).run(
                case,
                workspace,
                budget,
                RunContext(run_id=run_id),
            )
            for architecture_step in architecture_result.steps:
                sequence += 1
                self.store.append_step(
                    run_id,
                    architecture_step.model_copy(update={"sequence": sequence}),
                )

            total_usage = _add_usage(total_usage, architecture_result.usage)
            role_usage = _merge_role_usage(role_usage, architecture_result.role_usage)
            global_reason = _budget_overrun_reason(total_usage, budget)
            if global_reason is not None:
                return finish(
                    _status_for_budget_reason(global_reason),
                    global_reason,
                    functional_success=False,
                    review_status="skipped",
                    review_reasons=[global_reason],
                )

            if architecture_result.status in {
                RunStatus.BUDGET_EXHAUSTED,
                RunStatus.TIMED_OUT,
                RunStatus.FAILED,
            }:
                return finish(
                    architecture_result.status,
                    architecture_result.stop_reason,
                    functional_success=False,
                    review_status="skipped",
                    review_reasons=[architecture_result.stop_reason],
                )
            if total_usage.duration_ms >= budget.max_seconds * 1_000:
                return finish(
                    RunStatus.TIMED_OUT,
                    "time_budget_exhausted",
                    functional_success=False,
                    review_status="skipped",
                    review_reasons=["time_budget_exhausted"],
                )

            verification = sandbox.run(
                workspace,
                case.verify_command,
                timeout_seconds=_remaining_seconds(total_usage, budget),
            )
            total_usage = _add_usage(
                total_usage,
                Usage(duration_ms=verification.duration_ms),
            )
            append(
                "verification",
                self._command_output(verification),
                "passed" if verification.returncode == 0 else "failed",
                case.verify_command,
                verification.duration_ms,
            )
            if verification.timed_out:
                return finish(
                    RunStatus.TIMED_OUT,
                    "verification_timed_out",
                    functional_success=False,
                    review_status="skipped",
                    review_reasons=["verification_timed_out"],
                )
            global_reason = _budget_overrun_reason(total_usage, budget)
            if global_reason is not None:
                return finish(
                    _status_for_budget_reason(global_reason),
                    global_reason,
                    functional_success=False,
                    review_status="skipped",
                    review_reasons=[global_reason],
                )
            diff_text = self._workspace_diff(workspace)
            append(
                "diff",
                diff_text,
                "completed" if diff_text.strip() else "empty",
                "git diff --binary HEAD",
            )
            review = self.reviewer.evaluate(
                issue=case.issue,
                reproduction_exit_code=reproduction.returncode,
                verification_exit_code=verification.returncode,
                diff_text=diff_text,
                budget_exhausted=False,
                timeout_seconds=_advisory_timeout(total_usage, budget),
            )
            review_delta = review.usage
            append(
                "review",
                json.dumps(review.model_dump(), ensure_ascii=False),
                review.status,
                "deterministic gates and advisory review",
                review_delta.duration_ms,
                role="reviewer",
                input_tokens=review_delta.input_tokens,
                output_tokens=review_delta.output_tokens,
                cost_usd=review_delta.cost_usd,
            )
            total_usage = _add_usage(total_usage, review_delta)
            if review_delta != Usage():
                role_usage["reviewer"] = _add_usage(
                    role_usage.get("reviewer", Usage()), review_delta
                )
            global_reason = _budget_overrun_reason(total_usage, budget)
            if global_reason is not None:
                return finish(
                    _status_for_budget_reason(global_reason),
                    global_reason,
                    functional_success=False,
                    review_status=review.status,
                    review_reasons=review.reasons,
                )
            terminal_status = RunStatus.SUCCEEDED if review.functional_success else RunStatus.FAILED
            stop_reason = (
                "functional_success"
                if review.functional_success
                else review.reasons[0]
                if review.reasons
                else "review_failed"
            )
            return finish(
                terminal_status,
                stop_reason,
                functional_success=review.functional_success,
                review_status=review.status,
                review_reasons=review.reasons,
            )
        except Exception as error:  # noqa: BLE001 - all runs must reach a persisted terminal state.
            stop_reason = f"run_error:{type(error).__name__}"
            return finish(
                RunStatus.FAILED,
                stop_reason,
                functional_success=False,
                review_status="failed",
                review_reasons=[stop_reason],
            )
        return self.store.get_run(run_id)

    @staticmethod
    def _command_output(result: SandboxResult) -> str:
        return f"exit_code={result.returncode}\n{result.output}".rstrip()

    @staticmethod
    def _workspace_diff(workspace: Path) -> str:
        intent = subprocess.run(
            ["git", "add", "--intent-to-add", "."],
            cwd=workspace,
            capture_output=True,
            check=False,
            text=True,
        )
        if intent.returncode != 0:
            raise RuntimeError(intent.stderr.strip() or "git add --intent-to-add failed")
        completed = subprocess.run(
            ["git", "diff", "--binary", "--no-ext-diff", "HEAD"],
            cwd=workspace,
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "git diff failed")
        return completed.stdout


def _add_usage(left: Usage, right: Usage) -> Usage:
    return Usage(
        **{field: getattr(left, field) + getattr(right, field) for field in Usage.model_fields}
    )


def _merge_role_usage(
    current: dict[str, Usage],
    additions: dict[object, Usage],
) -> dict[str, Usage]:
    merged = dict(current)
    for role, usage in additions.items():
        name = str(role)
        merged[name] = _add_usage(merged.get(name, Usage()), usage)
    return merged


def _budget_overrun_reason(usage: Usage, budget: Budget) -> str | None:
    if usage.duration_ms > budget.max_seconds * 1_000:
        return "time_budget_exhausted"
    if usage.tool_calls > budget.max_tool_calls:
        return "tool_budget_exhausted"
    if usage.patch_attempts > budget.max_patch_attempts:
        return "patch_budget_exhausted"
    if usage.input_tokens > budget.max_input_tokens:
        return "input_token_budget_exhausted"
    if usage.output_tokens > budget.max_output_tokens:
        return "output_token_budget_exhausted"
    if usage.cost_usd > budget.max_cost_usd:
        return "cost_budget_exhausted"
    return None


def _status_for_budget_reason(reason: str) -> RunStatus:
    if reason == "time_budget_exhausted":
        return RunStatus.TIMED_OUT
    return RunStatus.BUDGET_EXHAUSTED


def _remaining_seconds(usage: Usage, budget: Budget) -> int:
    remaining_ms = budget.max_seconds * 1_000 - usage.duration_ms
    return max(1, (remaining_ms + 999) // 1_000)


def _no_budget_headroom(usage: Usage, budget: Budget) -> bool:
    """Whether no budget dimension can absorb one advisory reviewer call."""
    return (
        usage.duration_ms >= budget.max_seconds * 1_000
        or usage.input_tokens >= budget.max_input_tokens
        or usage.output_tokens >= budget.max_output_tokens
        or usage.cost_usd >= budget.max_cost_usd
    )


def _advisory_timeout(usage: Usage, budget: Budget) -> int | None:
    """Return the remaining seconds for an advisory call, or None when none remain."""
    if _no_budget_headroom(usage, budget):
        return None
    return _remaining_seconds(usage, budget)
