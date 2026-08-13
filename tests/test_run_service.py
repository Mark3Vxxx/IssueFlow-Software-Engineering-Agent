from collections import deque
from inspect import signature
from pathlib import Path
from subprocess import run

from pydantic import BaseModel
from test_reviewer import InvalidParsedReviewModel

from issueflow.agent import AgentResult
from issueflow.architectures.base import (
    ArchitectureKind,
    ArchitectureResult,
    RunContext,
)
from issueflow.architectures.single import SingleArchitecture
from issueflow.models import BenchmarkCase, Budget, RunStatus, TraceStep, Usage
from issueflow.reviewer import Reviewer
from issueflow.run_service import GitWorkspacePreparer, RunService
from issueflow.sandbox import SandboxResult
from issueflow.structured_model import ModelProtocolError, StructuredCompletion
from issueflow.trace_store import TraceStore


def make_case() -> BenchmarkCase:
    return BenchmarkCase(
        id="constructed-01",
        kind="constructed",
        budget_profile="small",
        repository_url="https://github.com/karpathy/micrograd",
        revision="a" * 40,
        license="MIT",
        issue="Unary negation returns the wrong value",
        source_url="https://github.com/karpathy/micrograd",
        reproduce_command="python -m pytest tests/test_regression.py",
        verify_command="python -m pytest tests/test_regression.py",
        fault_patch="patches/constructed-01-fault.patch",
        reference_patch="patches/constructed-01-fix.patch",
        construction_notes="Controlled arithmetic regression.",
    )


def make_budget() -> Budget:
    return Budget(
        max_tool_calls=8,
        max_patch_attempts=2,
        max_seconds=120,
        max_input_tokens=10_000,
        max_output_tokens=2_000,
        max_cost_usd=0.10,
    )


def test_run_service_has_one_architecture_constructor_contract():
    assert list(signature(RunService).parameters) == [
        "catalog",
        "store",
        "workspace_preparer",
        "sandbox",
        "architecture_factory",
        "reviewer",
    ]


class LocalWorkspacePreparer:
    def __init__(self, root: Path) -> None:
        self.root = root

    def prepare(self, case: BenchmarkCase, run_id: str) -> Path:
        workspace = self.root / run_id
        workspace.mkdir(parents=True)
        run(["git", "init", "--quiet"], cwd=workspace, check=True)
        run(
            ["git", "config", "user.email", "issueflow@example.invalid"],
            cwd=workspace,
            check=True,
        )
        run(["git", "config", "user.name", "IssueFlow Test"], cwd=workspace, check=True)
        (workspace / "engine.py").write_text("return 'broken'\n", encoding="utf-8")
        run(["git", "add", "engine.py"], cwd=workspace, check=True)
        run(["git", "commit", "--quiet", "-m", "fault baseline"], cwd=workspace, check=True)
        return workspace


class BrokenWorkspacePreparer:
    def prepare(self, case: BenchmarkCase, run_id: str) -> Path:
        raise RuntimeError("clone failed")


class SequenceSandbox:
    def __init__(self, returncodes: list[int]) -> None:
        self.returncodes = deque(returncodes)
        self.commands: list[str] = []

    def run(self, workspace: Path, command: str, timeout_seconds: int) -> SandboxResult:
        self.commands.append(command)
        returncode = self.returncodes.popleft()
        return SandboxResult(
            returncode=returncode,
            output="expected failure" if returncode else "1 passed",
            timed_out=False,
            duration_ms=5,
        )


class VerificationTimeoutSandbox:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, workspace: Path, command: str, timeout_seconds: int) -> SandboxResult:
        self.calls += 1
        if self.calls == 1:
            return SandboxResult(1, "expected failure", False, 5)
        return SandboxResult(124, "partial verification output", True, 1_000)


class ExplodingArchitectureFactory:
    def __call__(
        self,
        kind: ArchitectureKind,
        case: BenchmarkCase,
        workspace: Path,
    ):
        del kind, case, workspace
        raise AssertionError("architecture must not run when reproduction passes")


class RepairingAgent:
    def run(self, case: BenchmarkCase, workspace: Path, budget: Budget) -> AgentResult:
        (workspace / "engine.py").write_text("return 'fixed'\n", encoding="utf-8")
        return AgentResult(
            status=RunStatus.SUCCEEDED,
            stop_reason="verification_passed",
            tool_calls=2,
            patch_attempts=1,
            steps=[
                TraceStep(
                    sequence=1,
                    role="single_agent",
                    step_type="tool",
                    input_summary="apply_patch: 100 chars",
                    output_summary="patch applied",
                    status="completed",
                )
            ],
        )


class BudgetExhaustedAgent:
    def run(self, case: BenchmarkCase, workspace: Path, budget: Budget) -> AgentResult:
        return AgentResult(
            status=RunStatus.BUDGET_EXHAUSTED,
            stop_reason="tool_budget_exhausted",
            tool_calls=budget.max_tool_calls,
        )


class NeedsChangesModel:
    def review(self, issue: str, diff_text: str, *, timeout_seconds: int):
        class Payload(BaseModel):
            status: str
            reasons: list[str]

        return StructuredCompletion(
            value=Payload(
                status="needs_changes",
                reasons=["Prefer a smaller diff."],
            ),
            usage=Usage(
                model_calls=1,
                input_tokens=37,
                output_tokens=11,
                cost_usd=0.00004,
            ),
        )


def test_successful_run_persists_full_evidence_and_advisory_review(tmp_path):
    case = make_case()
    store = TraceStore(tmp_path / "issueflow.sqlite3")
    sandbox = SequenceSandbox([1, 0])
    service = RunService(
        catalog={case.id: case},
        store=store,
        workspace_preparer=LocalWorkspacePreparer(tmp_path / "workspaces"),
        sandbox=sandbox,
        architecture_factory=lambda _kind, case, workspace: SingleArchitecture(RepairingAgent()),
        reviewer=Reviewer(NeedsChangesModel()),
    )

    result = service.start(case.id, make_budget())

    assert result.status is RunStatus.SUCCEEDED
    assert result.functional_success is True
    assert result.review_status == "needs_changes"
    assert result.review_reasons == ["Prefer a smaller diff."]
    assert sandbox.commands == [case.reproduce_command, case.verify_command]

    exported = store.export_json(result.id)
    assert exported["run"]["status"] == "succeeded"
    assert exported["run"]["review_status"] == "needs_changes"
    assert [step["sequence"] for step in exported["steps"]] == [1, 2, 3, 4, 5]
    assert [step["step_type"] for step in exported["steps"]] == [
        "reproduction",
        "tool",
        "verification",
        "diff",
        "review",
    ]
    assert exported["steps"][0]["duration_ms"] == 5
    assert exported["steps"][2]["duration_ms"] == 5
    assert exported["steps"][-1]["input_tokens"] == 37
    assert exported["steps"][-1]["output_tokens"] == 11
    assert exported["steps"][-1]["cost_usd"] == 0.00004


def test_invalid_parsed_review_persists_usage_without_overriding_success(tmp_path):
    case = make_case()
    store = TraceStore(tmp_path / "issueflow.sqlite3")
    usage = Usage(
        model_calls=1,
        input_tokens=19,
        output_tokens=3,
        cost_usd=0.25,
        duration_ms=17,
    )
    service = RunService(
        catalog={case.id: case},
        store=store,
        workspace_preparer=LocalWorkspacePreparer(tmp_path / "workspaces"),
        sandbox=SequenceSandbox([1, 0]),
        architecture_factory=lambda _kind, selected_case, workspace: SingleArchitecture(
            RepairingAgent()
        ),
        reviewer=Reviewer(InvalidParsedReviewModel(usage)),
    )

    result = service.start(case.id, make_budget())

    assert result.status is RunStatus.BUDGET_EXHAUSTED
    assert result.functional_success is False
    assert result.stop_reason == "cost_budget_exhausted"
    assert result.review_status == "failed"
    assert result.review_reasons == ["invalid_reviewer_response"]
    exported = store.export_json(result.id)
    review_step = exported["steps"][-1]
    assert review_step["step_type"] == "review"
    assert review_step["status"] == "failed"
    assert review_step["duration_ms"] == 17
    assert review_step["input_tokens"] == 19
    assert review_step["output_tokens"] == 3
    assert review_step["cost_usd"] == 0.25
    assert exported["run"]["usage"] == {
        "model_calls": 1,
        "tool_calls": 2,
        "patch_attempts": 1,
        "input_tokens": 19,
        "output_tokens": 3,
        "cost_usd": 0.25,
        "duration_ms": 27,
    }
    assert exported["run"]["role_usage"]["reviewer"] == usage.model_dump()
    assert "provider-secret-detail" not in store.export_json_text(result.id)


def test_run_usage_is_persisted_once_and_includes_outer_reviewer(tmp_path):
    case = make_case()
    store = TraceStore(tmp_path / "issueflow.sqlite3")
    service = RunService(
        catalog={case.id: case},
        store=store,
        workspace_preparer=LocalWorkspacePreparer(tmp_path / "workspaces"),
        sandbox=SequenceSandbox([1, 0]),
        architecture_factory=lambda _kind, selected_case, workspace: SingleArchitecture(
            RepairingAgent()
        ),
        reviewer=Reviewer(NeedsChangesModel()),
    )

    result = service.start(case.id, make_budget())
    exported = store.export_json(result.id)

    assert result.usage == Usage(
        model_calls=1,
        tool_calls=2,
        patch_attempts=1,
        input_tokens=37,
        output_tokens=11,
        cost_usd=0.00004,
        duration_ms=10,
    )
    assert exported["run"]["usage"] == result.usage.model_dump()
    assert exported["run"]["role_usage"] == {
        "single_agent": Usage(tool_calls=2, patch_attempts=1).model_dump(),
        "reviewer": Usage(
            model_calls=1,
            input_tokens=37,
            output_tokens=11,
            cost_usd=0.00004,
        ).model_dump(),
    }


def test_exact_global_time_limit_is_allowed_when_no_advisory_call_remains(tmp_path):
    case = make_case()
    store = TraceStore(tmp_path / "issueflow.sqlite3")

    class ExactDurationSandbox(SequenceSandbox):
        def run(self, workspace, command, timeout_seconds):
            result = super().run(workspace, command, timeout_seconds)
            return result.__class__(
                returncode=result.returncode,
                output=result.output,
                timed_out=result.timed_out,
                duration_ms=500,
            )

    service = RunService(
        catalog={case.id: case},
        store=store,
        workspace_preparer=LocalWorkspacePreparer(tmp_path / "workspaces"),
        sandbox=ExactDurationSandbox([1, 0]),
        architecture_factory=lambda _kind, selected_case, workspace: SingleArchitecture(
            RepairingAgent()
        ),
        reviewer=Reviewer(),
    )

    result = service.start(case.id, make_budget().model_copy(update={"max_seconds": 1}))

    assert result.status is RunStatus.SUCCEEDED
    assert result.functional_success is True
    assert result.usage.duration_ms == 1_000


def test_default_and_explicit_architectures_are_persisted_with_the_same_budget(tmp_path):
    case = make_case()
    budget = make_budget()
    store = TraceStore(tmp_path / "issueflow.sqlite3")
    requested: list[tuple[ArchitectureKind, BenchmarkCase, Path]] = []
    received_budgets: list[Budget] = []

    class RepairArchitecture:
        def __init__(self, kind: ArchitectureKind) -> None:
            self.kind = kind

        def run(
            self,
            selected_case: BenchmarkCase,
            workspace: Path,
            selected_budget: Budget,
            context: RunContext,
        ) -> ArchitectureResult:
            received_budgets.append(selected_budget)
            (workspace / "engine.py").write_text("return 'fixed'\n", encoding="utf-8")
            return ArchitectureResult(
                architecture=self.kind,
                status=RunStatus.SUCCEEDED,
                stop_reason="repair_complete",
                steps=[
                    TraceStep(
                        sequence=1,
                        role="coder",
                        step_type="role",
                        input_summary=context.run_id,
                        output_summary="patch applied",
                        status="completed",
                    )
                ],
            )

    def architecture_factory(kind, selected_case, workspace):
        requested.append((kind, selected_case, workspace))
        return RepairArchitecture(kind)

    service = RunService(
        catalog={case.id: case},
        store=store,
        workspace_preparer=LocalWorkspacePreparer(tmp_path / "workspaces"),
        sandbox=SequenceSandbox([1, 0, 1, 0]),
        architecture_factory=architecture_factory,
        reviewer=Reviewer(),
    )

    default_run = service.start(case.id, budget)
    fixed_run = service.start(case.id, budget, ArchitectureKind.FIXED)

    assert default_run.architecture == "single"
    assert fixed_run.architecture == "fixed"
    assert [item[0] for item in requested] == [
        ArchitectureKind.SINGLE,
        ArchitectureKind.FIXED,
    ]
    assert received_budgets == [budget, budget]
    assert [step["step_type"] for step in store.export_json(fixed_run.id)["steps"]].count(
        "role"
    ) == 1


def test_budget_exhaustion_is_terminal_and_persisted(tmp_path):
    case = make_case()
    store = TraceStore(tmp_path / "issueflow.sqlite3")
    service = RunService(
        catalog={case.id: case},
        store=store,
        workspace_preparer=LocalWorkspacePreparer(tmp_path / "workspaces"),
        sandbox=SequenceSandbox([1]),
        architecture_factory=lambda _kind, case, workspace: SingleArchitecture(
            BudgetExhaustedAgent()
        ),
        reviewer=Reviewer(),
    )

    result = service.start(case.id, make_budget())

    assert result.status is RunStatus.BUDGET_EXHAUSTED
    assert result.stop_reason == "tool_budget_exhausted"
    assert result.functional_success is False
    assert result.review_status == "skipped"
    assert result.review_reasons == ["tool_budget_exhausted"]
    assert store.export_json(result.id)["run"]["status"] == "budget_exhausted"


def test_unhandled_failure_still_finishes_persisted_run(tmp_path):
    case = make_case()
    store = TraceStore(tmp_path / "issueflow.sqlite3")
    service = RunService(
        catalog={case.id: case},
        store=store,
        workspace_preparer=BrokenWorkspacePreparer(),
        sandbox=SequenceSandbox([]),
        architecture_factory=lambda _kind, case, workspace: SingleArchitecture(RepairingAgent()),
        reviewer=Reviewer(),
    )

    result = service.start(case.id, make_budget())

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "run_error:RuntimeError"
    assert result.functional_success is False
    assert result.review_status == "failed"
    assert store.export_json(result.id)["run"]["status"] == "failed"


def test_git_workspace_preparer_injects_fault_as_clean_baseline(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    run(["git", "init", "--quiet"], cwd=source, check=True)
    run(
        ["git", "config", "user.email", "issueflow@example.invalid"],
        cwd=source,
        check=True,
    )
    run(["git", "config", "user.name", "IssueFlow Test"], cwd=source, check=True)
    (source / "engine.py").write_text("return 'fixed'\n", encoding="utf-8")
    run(["git", "add", "engine.py"], cwd=source, check=True)
    run(["git", "commit", "--quiet", "-m", "upstream"], cwd=source, check=True)
    revision = run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()

    catalog_root = tmp_path / "catalog"
    patch_dir = catalog_root / "patches"
    patch_dir.mkdir(parents=True)
    (patch_dir / "fault.patch").write_text(
        """diff --git a/engine.py b/engine.py
--- a/engine.py
+++ b/engine.py
@@ -1 +1 @@
-return 'fixed'
+return 'broken'
""",
        encoding="utf-8",
    )
    case = make_case().model_copy(
        update={
            "repository_url": str(source),
            "revision": revision,
            "fault_patch": "patches/fault.patch",
        }
    )
    preparer = GitWorkspacePreparer(tmp_path / "workspaces", catalog_root)

    workspace = preparer.prepare(case, "run-123")

    assert (workspace / "engine.py").read_text(encoding="utf-8") == "return 'broken'\n"
    status = run(
        ["git", "status", "--porcelain"],
        cwd=workspace,
        capture_output=True,
        check=True,
        text=True,
    )
    assert status.stdout == ""


def test_reproduction_that_unexpectedly_passes_stops_before_agent(tmp_path):
    case = make_case()
    store = TraceStore(tmp_path / "issueflow.sqlite3")
    service = RunService(
        catalog={case.id: case},
        store=store,
        workspace_preparer=LocalWorkspacePreparer(tmp_path / "workspaces"),
        sandbox=SequenceSandbox([0]),
        architecture_factory=ExplodingArchitectureFactory(),
        reviewer=Reviewer(),
    )

    result = service.start(case.id, make_budget())

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "reproduction_did_not_fail"
    assert len(store.export_json(result.id)["steps"]) == 1


def test_verification_timeout_is_persisted_as_timed_out(tmp_path):
    case = make_case()
    store = TraceStore(tmp_path / "issueflow.sqlite3")
    service = RunService(
        catalog={case.id: case},
        store=store,
        workspace_preparer=LocalWorkspacePreparer(tmp_path / "workspaces"),
        sandbox=VerificationTimeoutSandbox(),
        architecture_factory=lambda _kind, case, workspace: SingleArchitecture(RepairingAgent()),
        reviewer=Reviewer(),
    )

    result = service.start(case.id, make_budget())

    assert result.status is RunStatus.TIMED_OUT
    assert result.stop_reason == "verification_timed_out"
    exported = store.export_json(result.id)
    assert exported["steps"][-1]["step_type"] == "verification"
    assert "partial verification output" in exported["steps"][-1]["output_summary"]


def test_reviewer_is_skipped_when_time_budget_has_no_headroom(tmp_path):
    case = make_case()
    store = TraceStore(tmp_path / "issueflow.sqlite3")

    class ExactDurationSandbox(SequenceSandbox):
        def run(self, workspace, command, timeout_seconds):
            result = super().run(workspace, command, timeout_seconds)
            return result.__class__(
                returncode=result.returncode,
                output=result.output,
                timed_out=result.timed_out,
                duration_ms=500,
            )

    class ForbiddenReviewModel:
        def __init__(self) -> None:
            self.calls = 0

        def review(self, issue, diff_text, *, timeout_seconds):
            self.calls += 1
            raise AssertionError("reviewer must be skipped without budget headroom")

    model = ForbiddenReviewModel()
    service = RunService(
        catalog={case.id: case},
        store=store,
        workspace_preparer=LocalWorkspacePreparer(tmp_path / "workspaces"),
        sandbox=ExactDurationSandbox([1, 0]),
        architecture_factory=lambda _kind, selected_case, workspace: SingleArchitecture(
            RepairingAgent()
        ),
        reviewer=Reviewer(model),
    )

    result = service.start(case.id, make_budget().model_copy(update={"max_seconds": 1}))

    assert result.status is RunStatus.SUCCEEDED
    assert result.functional_success is True
    assert result.review_status == "skipped"
    assert result.review_reasons == ["reviewer_skipped_no_budget"]
    assert model.calls == 0


def test_reviewer_timeout_is_normalized_to_time_budget_exhausted(tmp_path):
    case = make_case()
    store = TraceStore(tmp_path / "issueflow.sqlite3")

    class ReviewTimeoutModel:
        def review(self, issue, diff_text, *, timeout_seconds):
            raise ModelProtocolError(
                "reviewer_request_failed",
                Usage(model_calls=1, duration_ms=timeout_seconds * 1_000),
            )

    service = RunService(
        catalog={case.id: case},
        store=store,
        workspace_preparer=LocalWorkspacePreparer(tmp_path / "workspaces"),
        sandbox=SequenceSandbox([1, 0]),
        architecture_factory=lambda _kind, selected_case, workspace: SingleArchitecture(
            RepairingAgent()
        ),
        reviewer=Reviewer(ReviewTimeoutModel()),
    )

    result = service.start(case.id, make_budget().model_copy(update={"max_seconds": 1}))

    assert result.status is RunStatus.TIMED_OUT
    assert result.stop_reason == "time_budget_exhausted"
    assert result.functional_success is False
    assert result.review_status == "failed"
    assert result.review_reasons == ["reviewer_request_failed"]
