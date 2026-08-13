"""Hidden-test isolation from Agent workspaces and validator-only mounting."""

from pathlib import Path

import pytest

from issueflow.agent import ModelAction, ToolExecutor
from issueflow.hidden_validation import HiddenVerifier
from issueflow.models import BenchmarkCase
from issueflow.sandbox import build_hidden_docker_command


def make_strict_case() -> BenchmarkCase:
    return BenchmarkCase(
        id="mingpt-h01",
        dataset_split="strict",
        repository_id="mingpt",
        environment_id="mingpt",
        kind="historical",
        budget_profile="small",
        difficulty="small",
        issue_category="model_training",
        repository_url="https://github.com/karpathy/minGPT",
        revision="b" * 40,
        license="MIT",
        issue="A historical training-loop bug.",
        source_url="https://github.com/karpathy/minGPT/commit/fix",
        reproduce_command="python -m pytest tests/test_train.py",
        verify_command="python -m pytest tests/test_train.py",
        reference_patch="patches/mingpt-h01-fix.patch",
        construction_notes="Historical upstream repair.",
        hidden_test_path="mingpt-h01/test_hidden.py",
        hidden_verify_command="python -m pytest /issueflow-hidden/test_hidden.py",
        fixed_revision="c" * 40,
    )


def test_hidden_docker_command_mounts_one_read_only_validator(tmp_path):
    hidden = tmp_path / "hidden" / "mingpt-h01" / "test_hidden.py"
    hidden.parent.mkdir(parents=True)
    hidden.write_text("def test_hidden(): pass\n", encoding="utf-8")

    command = build_hidden_docker_command(
        workspace=tmp_path / "workspace",
        command="python -m pytest /issueflow-hidden/test_hidden.py",
        timeout_seconds=60,
        image_name="issueflow-mingpt:phase2",
        hidden_test_path=hidden,
    )

    assert any("/issueflow-hidden/test_hidden.py:ro" in item for item in command)
    assert not any(str(hidden.parent) + ":/workspace" in item for item in command)


def test_hidden_verifier_rejects_paths_outside_hidden_root(tmp_path):
    verifier = HiddenVerifier(tmp_path / "hidden")

    with pytest.raises(ValueError, match="hidden root"):
        verifier.resolve_hidden_test("../escape.py")


def test_hidden_verifier_resolves_path_inside_root(tmp_path):
    verifier = HiddenVerifier(tmp_path / "hidden")

    resolved = verifier.resolve_hidden_test("mingpt-h01/test_hidden.py")

    assert resolved == (tmp_path / "hidden" / "mingpt-h01" / "test_hidden.py").resolve()


def test_tool_executor_rejects_hidden_verify_command():
    case = make_strict_case()
    executor = ToolExecutor(Path("/tmp/workspace"), case, None)

    action = ModelAction(
        tool="run_tests",
        arguments={"command": case.hidden_verify_command},
    )

    with pytest.raises(ValueError, match="not registered"):
        executor.execute(action)
