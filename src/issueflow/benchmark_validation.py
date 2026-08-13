"""Three-replay qualification and immutable result schema for strict cases."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, PositiveInt

from issueflow.models import BenchmarkCase, DatasetSplit

ReproductionStatus = Literal["FAIL_AS_EXPECTED", "UNEXPECTED_PASS", "ERROR"]
VerificationStatus = Literal["PASS", "FAIL", "NOT_RUN"]
HiddenStatus = Literal["PASS", "FAIL", "NOT_REQUIRED", "NOT_RUN"]


class ReplayResult(BaseModel):
    """The outcome of one clean-workspace replay of a candidate case."""

    replay: PositiveInt
    reproduction: ReproductionStatus
    public_verification: VerificationStatus
    hidden_verification: HiddenStatus


class QualificationResult(BaseModel):
    """Immutable evidence that one case passed or failed the strict gates."""

    case_id: str
    accepted_split: DatasetSplit | None
    reasons: list[str]
    replays: list[ReplayResult]
    environment_id: str
    revision: str
    reference_patch_sha256: str


class WorkspacePreparer(Protocol):
    def prepare(self, case: BenchmarkCase, run_id: str) -> Path: ...


class SandboxRunner(Protocol):
    def run(self, workspace: Path, command: str, timeout_seconds: int): ...


class HiddenVerifier(Protocol):
    def verify(self, case: BenchmarkCase, workspace: Path, sandbox, timeout_seconds: int): ...


def qualify_case(
    case: BenchmarkCase,
    *,
    workspace_preparer: WorkspacePreparer,
    sandbox: SandboxRunner,
    hidden_verifier: HiddenVerifier | None,
    patch_root: Path,
    replays: int = 3,
) -> QualificationResult:
    """Run the strict gates across several clean workspaces and record the verdict."""
    reasons: list[str] = []
    if not case.license.strip() or not case.source_url.strip():
        reasons.append("license_missing")

    reference_patch_path = patch_root / case.reference_patch
    if reference_patch_path.is_file():
        reference_patch_sha256 = _sha256_file(reference_patch_path)
    else:
        reference_patch_sha256 = ""
        reasons.append("reference_patch_failed")
    replay_results: list[ReplayResult] = []
    for replay_number in range(1, replays + 1):
        replay, replay_reasons = _run_replay(
            case,
            replay_number,
            workspace_preparer,
            sandbox,
            hidden_verifier,
            patch_root,
        )
        replay_results.append(replay)
        reasons.extend(replay_reasons)

    reasons = list(dict.fromkeys(reasons))
    accepted = case.dataset_split if not reasons else None
    return QualificationResult(
        case_id=case.id,
        accepted_split=accepted,
        reasons=reasons,
        replays=replay_results,
        environment_id=case.environment_id,
        revision=case.revision,
        reference_patch_sha256=reference_patch_sha256,
    )


def _run_replay(
    case: BenchmarkCase,
    replay_number: int,
    workspace_preparer: WorkspacePreparer,
    sandbox: SandboxRunner,
    hidden_verifier: HiddenVerifier | None,
    patch_root: Path,
) -> tuple[ReplayResult, list[str]]:
    """Run one clean-workspace replay and return its result plus failure reasons."""
    hidden_default = "NOT_REQUIRED" if case.dataset_split is not DatasetSplit.STRICT else "NOT_RUN"
    run_id = f"qualify-{case.id}-{replay_number}"
    try:
        workspace = workspace_preparer.prepare(case, run_id)
    except Exception:  # noqa: BLE001 - normalize any prep failure to environment_failed
        return (
            ReplayResult(
                replay=replay_number,
                reproduction="ERROR",
                public_verification="NOT_RUN",
                hidden_verification=hidden_default,
            ),
            ["environment_failed"],
        )

    reproduction = sandbox.run(workspace, case.reproduce_command, timeout_seconds=120)
    if reproduction.returncode == 0:
        return (
            ReplayResult(
                replay=replay_number,
                reproduction="UNEXPECTED_PASS",
                public_verification="NOT_RUN",
                hidden_verification=hidden_default,
            ),
            ["reproduction_unstable"],
        )

    if _apply_patch(workspace, patch_root / case.reference_patch) != 0:
        return (
            ReplayResult(
                replay=replay_number,
                reproduction="FAIL_AS_EXPECTED",
                public_verification="NOT_RUN",
                hidden_verification=hidden_default,
            ),
            ["reference_patch_failed"],
        )

    public = sandbox.run(workspace, case.verify_command, timeout_seconds=120)
    if public.returncode != 0:
        return (
            ReplayResult(
                replay=replay_number,
                reproduction="FAIL_AS_EXPECTED",
                public_verification="FAIL",
                hidden_verification=hidden_default,
            ),
            ["public_verification_failed"],
        )

    hidden_status = hidden_default
    reasons: list[str] = []
    if case.dataset_split is DatasetSplit.STRICT:
        if hidden_verifier is None:
            reasons.append("environment_failed")
        else:
            hidden = hidden_verifier.verify(case, workspace, sandbox, timeout_seconds=120)
            hidden_status = "PASS" if hidden.returncode == 0 else "FAIL"
            if hidden_status == "FAIL":
                reasons.append("hidden_verification_failed")

    if _answer_leaked(workspace, case):
        reasons.append("answer_leakage")

    return (
        ReplayResult(
            replay=replay_number,
            reproduction="FAIL_AS_EXPECTED",
            public_verification="PASS",
            hidden_verification=hidden_status,
        ),
        reasons,
    )


def _apply_patch(workspace: Path, patch_path: Path) -> int:
    completed = subprocess.run(
        ["git", "apply", str(patch_path.resolve())],
        cwd=workspace,
        capture_output=True,
        check=False,
        text=True,
    )
    return completed.returncode


def _answer_leaked(workspace: Path, case: BenchmarkCase) -> bool:
    """Return True when the workspace contains the hidden test or reference patch file."""
    forbidden = {Path(case.reference_patch).name}
    if case.hidden_test_path:
        forbidden.add(Path(case.hidden_test_path).name)
    for path in workspace.rglob("*"):
        if path.is_file() and path.name in forbidden:
            return True
    return False


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
