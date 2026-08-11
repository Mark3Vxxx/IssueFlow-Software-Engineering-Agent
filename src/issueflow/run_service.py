"""Orchestrate one reproducible benchmark repair run."""

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from issueflow.agent import AgentResult
from issueflow.models import BenchmarkCase, Budget, RunRecord, RunStatus, TraceStep
from issueflow.reviewer import Reviewer
from issueflow.sandbox import SandboxResult
from issueflow.trace_store import TraceStore


class WorkspacePreparer(Protocol):
    """Create an isolated faulty workspace for one run."""

    def prepare(self, case: BenchmarkCase, run_id: str) -> Path: ...


class SandboxRunner(Protocol):
    """Execute a registered command inside the sandbox."""

    def run(self, workspace: Path, command: str, timeout_seconds: int) -> SandboxResult: ...


class AgentRunner(Protocol):
    """Run a configured single agent in one prepared workspace."""

    def run(self, case: BenchmarkCase, workspace: Path, budget: Budget) -> AgentResult: ...


AgentFactory = Callable[[BenchmarkCase, Path], AgentRunner]


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
        sandbox: SandboxRunner,
        agent_factory: AgentFactory,
        reviewer: Reviewer,
    ) -> None:
        self.catalog = catalog
        self.store = store
        self.workspace_preparer = workspace_preparer
        self.sandbox = sandbox
        self.agent_factory = agent_factory
        self.reviewer = reviewer

    def start(self, case_id: str, budget: Budget) -> RunRecord:
        """Execute and persist one benchmark repair attempt."""
        case = self.catalog[case_id]
        run_id = f"run-{uuid4().hex}"
        self.store.create_run(RunRecord(id=run_id, case_id=case.id))
        self.store.start_run(run_id)
        sequence = 0

        def append(
            step_type: str,
            output: str,
            status: str,
            input_summary: str,
            duration_ms: int = 0,
        ) -> None:
            nonlocal sequence
            sequence += 1
            self.store.append_step(
                run_id,
                TraceStep(
                    sequence=sequence,
                    role="single_agent",
                    step_type=step_type,
                    input_summary=input_summary,
                    output_summary=output,
                    status=status,
                    duration_ms=duration_ms,
                ),
            )

        try:
            workspace = self.workspace_preparer.prepare(case, run_id)
            reproduction = self.sandbox.run(
                workspace, case.reproduce_command, timeout_seconds=budget.max_seconds
            )
            append(
                "reproduction",
                self._command_output(reproduction),
                "failed_as_expected" if reproduction.returncode != 0 else "unexpected_pass",
                case.reproduce_command,
                reproduction.duration_ms,
            )
            if reproduction.timed_out:
                self.store.finish_run(
                    run_id,
                    RunStatus.TIMED_OUT,
                    "reproduction_timed_out",
                    functional_success=False,
                    review_status="skipped",
                    review_reasons=["reproduction_timed_out"],
                )
                return self.store.get_run(run_id)
            if reproduction.returncode == 0:
                self.store.finish_run(
                    run_id,
                    RunStatus.FAILED,
                    "reproduction_did_not_fail",
                    functional_success=False,
                    review_status="skipped",
                    review_reasons=["reproduction_did_not_fail"],
                )
                return self.store.get_run(run_id)

            agent_result = self.agent_factory(case, workspace).run(case, workspace, budget)
            for agent_step in agent_result.steps:
                sequence += 1
                self.store.append_step(
                    run_id,
                    agent_step.model_copy(update={"sequence": sequence}),
                )

            if agent_result.status in {
                RunStatus.BUDGET_EXHAUSTED,
                RunStatus.TIMED_OUT,
                RunStatus.FAILED,
            }:
                self.store.finish_run(
                    run_id,
                    agent_result.status,
                    agent_result.stop_reason,
                    functional_success=False,
                    review_status="skipped",
                    review_reasons=[agent_result.stop_reason],
                )
                return self.store.get_run(run_id)

            verification = self.sandbox.run(
                workspace, case.verify_command, timeout_seconds=budget.max_seconds
            )
            append(
                "verification",
                self._command_output(verification),
                "passed" if verification.returncode == 0 else "failed",
                case.verify_command,
                verification.duration_ms,
            )
            if verification.timed_out:
                self.store.finish_run(
                    run_id,
                    RunStatus.TIMED_OUT,
                    "verification_timed_out",
                    functional_success=False,
                    review_status="skipped",
                    review_reasons=["verification_timed_out"],
                )
                return self.store.get_run(run_id)
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
            )
            append(
                "review",
                json.dumps(review.model_dump(), ensure_ascii=False),
                review.status,
                "deterministic gates and advisory review",
            )
            terminal_status = RunStatus.SUCCEEDED if review.functional_success else RunStatus.FAILED
            stop_reason = (
                "functional_success"
                if review.functional_success
                else review.reasons[0]
                if review.reasons
                else "review_failed"
            )
            self.store.finish_run(
                run_id,
                terminal_status,
                stop_reason,
                functional_success=review.functional_success,
                review_status=review.status,
                review_reasons=review.reasons,
            )
        except Exception as error:  # noqa: BLE001 - all runs must reach a persisted terminal state.
            stop_reason = f"run_error:{type(error).__name__}"
            self.store.finish_run(
                run_id,
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
