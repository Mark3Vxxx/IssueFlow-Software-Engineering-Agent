"""Qualification verdicts, rejection reasons, and local-git acceptance."""

import subprocess
from pathlib import Path

from issueflow.benchmark_validation import (
    ReplayResult,
    qualify_case,
)
from issueflow.models import BenchmarkCase, DatasetSplit
from issueflow.sandbox import SandboxResult


def make_strict_case(**updates) -> BenchmarkCase:
    values = {
        "id": "mingpt-h01",
        "dataset_split": "strict",
        "repository_id": "mingpt",
        "environment_id": "mingpt",
        "kind": "historical",
        "budget_profile": "small",
        "difficulty": "small",
        "issue_category": "model_training",
        "repository_url": "https://github.com/karpathy/minGPT",
        "revision": "b" * 40,
        "license": "MIT",
        "issue": "A historical training-loop bug.",
        "source_url": "https://github.com/karpathy/minGPT/commit/fix",
        "reproduce_command": "python -c 'raise SystemExit(1)'",
        "verify_command": "python -c 'raise SystemExit(0)'",
        "reference_patch": "mingpt-h01/reference.patch",
        "construction_notes": "Historical upstream repair.",
        "hidden_test_path": "mingpt-h01/test_hidden.py",
        "hidden_verify_command": "python -m pytest /issueflow-hidden/test_hidden.py",
        "fixed_revision": "c" * 40,
    }
    values.update(updates)
    return BenchmarkCase(**values)


class StaticSandbox:
    def __init__(self, returncodes: list[int]) -> None:
        self.returncodes = returncodes
        self.calls = 0

    def run(self, workspace, command, timeout_seconds):
        self.calls += 1
        returncode = self.returncodes[min(self.calls - 1, len(self.returncodes) - 1)]
        return SandboxResult(returncode, "output", False, 5)


class StaticHiddenVerifier:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode

    def verify(self, case, workspace, sandbox, timeout_seconds):
        return SandboxResult(self.returncode, "hidden", False, 5)


class TempWorkspacePreparer:
    def __init__(self, root: Path) -> None:
        self.root = root

    def prepare(self, case, run_id):
        workspace = self.root / run_id
        workspace.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "--quiet"], cwd=workspace, check=True)
        (workspace / "engine.py").write_text("value = 'broken'\n", encoding="utf-8")
        subprocess.run(["git", "add", "engine.py"], cwd=workspace, check=True)
        subprocess.run(["git", "commit", "--quiet", "-m", "fault"], cwd=workspace, check=True)
        return workspace


def test_replay_result_schema_and_qualification_acceptance(tmp_path):
    patch_root = tmp_path / "cases" / "mingpt-h01"
    patch_root.mkdir(parents=True)
    (patch_root / "reference.patch").write_text(
        "diff --git a/engine.py b/engine.py\n"
        "--- a/engine.py\n+++ b/engine.py\n"
        "@@ -1 +1 @@\n-value = 'broken'\n+value = 'fixed'\n",
        encoding="utf-8",
    )

    result = qualify_case(
        make_strict_case(),
        workspace_preparer=TempWorkspacePreparer(tmp_path / "workspaces"),
        sandbox=StaticSandbox([1, 0, 1, 0, 1, 0]),
        hidden_verifier=StaticHiddenVerifier(0),
        patch_root=patch_root.parent,
        replays=3,
    )

    assert result.accepted_split is DatasetSplit.STRICT
    assert result.reasons == []
    assert len(result.replays) == 3
    assert result.replays[0] == ReplayResult(
        replay=1,
        reproduction="FAIL_AS_EXPECTED",
        public_verification="PASS",
        hidden_verification="PASS",
    )


def test_qualification_reports_license_missing(tmp_path):
    result = qualify_case(
        make_strict_case(license=""),
        workspace_preparer=TempWorkspacePreparer(tmp_path / "workspaces"),
        sandbox=StaticSandbox([1, 0]),
        hidden_verifier=StaticHiddenVerifier(0),
        patch_root=tmp_path,
        replays=1,
    )

    assert result.accepted_split is None
    assert "license_missing" in result.reasons


def test_qualification_reports_public_verification_failure(tmp_path):
    patch_root = tmp_path / "cases" / "mingpt-h01"
    patch_root.mkdir(parents=True)
    (patch_root / "reference.patch").write_text(
        "diff --git a/engine.py b/engine.py\n"
        "--- a/engine.py\n+++ b/engine.py\n"
        "@@ -1 +1 @@\n-value = 'broken'\n+value = 'fixed'\n",
        encoding="utf-8",
    )

    result = qualify_case(
        make_strict_case(),
        workspace_preparer=TempWorkspacePreparer(tmp_path / "workspaces"),
        sandbox=StaticSandbox([1, 1, 1, 1]),
        hidden_verifier=StaticHiddenVerifier(0),
        patch_root=patch_root.parent,
        replays=2,
    )

    assert result.accepted_split is None
    assert "public_verification_failed" in result.reasons
