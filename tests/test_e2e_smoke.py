"""Replayable end-to-end acceptance test for the phase-one pipeline."""

import json
import os
from collections import deque
from pathlib import Path
from subprocess import run

from issueflow.agent import ModelAction, SingleAgent, ToolExecutor
from issueflow.architectures.single import SingleArchitecture
from issueflow.models import BenchmarkCase, Budget, RunStatus
from issueflow.reviewer import Reviewer
from issueflow.run_service import GitWorkspacePreparer, RunService
from issueflow.sandbox import DockerSandbox
from issueflow.trace_store import TraceStore


class ScriptedRepairModel:
    """Keep the external-model boundary deterministic while exercising the full pipeline."""

    def __init__(self, verify_command: str) -> None:
        self.actions = deque(
            [
                ModelAction(tool="search", arguments={"query": "def __neg__"}),
                ModelAction(
                    tool="read_file",
                    arguments={"path": "micrograd/engine.py", "start_line": 1, "end_line": 12},
                ),
                ModelAction(
                    tool="apply_patch",
                    arguments={
                        "path": "micrograd/engine.py",
                        "old_text": "        return self\n",
                        "new_text": "        return self * -1\n",
                    },
                ),
                ModelAction(tool="run_tests", arguments={"command": verify_command}),
            ]
        )

    def next_action(self, issue: str, history: list[dict[str, object]]) -> ModelAction:
        return self.actions.popleft()


def _make_local_benchmark(tmp_path: Path) -> tuple[BenchmarkCase, Path]:
    source = tmp_path / "source"
    package = source / "micrograd"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "engine.py").write_text(
        """class Value:
    def __init__(self, data):
        self.data = data

    def __mul__(self, other):
        return Value(self.data * other)

    def __neg__(self):
        return self * -1
""",
        encoding="utf-8",
    )
    run(["git", "init", "--quiet"], cwd=source, check=True)
    run(["git", "config", "user.email", "issueflow@example.invalid"], cwd=source, check=True)
    run(["git", "config", "user.name", "IssueFlow E2E"], cwd=source, check=True)
    run(["git", "add", "micrograd"], cwd=source, check=True)
    run(["git", "commit", "--quiet", "-m", "fixed upstream"], cwd=source, check=True)
    revision = run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()

    catalog_root = tmp_path / "catalog"
    patches = catalog_root / "patches"
    patches.mkdir(parents=True)
    (patches / "fault.patch").write_text(
        """diff --git a/micrograd/engine.py b/micrograd/engine.py
--- a/micrograd/engine.py
+++ b/micrograd/engine.py
@@ -8,2 +8,2 @@ class Value:
     def __neg__(self):
-        return self * -1
+        return self
""",
        encoding="utf-8",
    )
    verify_command = (
        'python -c "from micrograd.engine import Value; assert (-Value(2.0)).data == -2.0"'
    )
    case = BenchmarkCase(
        id="constructed-e2e",
        dataset_split="compatibility",
        repository_id="micrograd",
        environment_id="micrograd",
        difficulty="small",
        issue_category="numerical",
        kind="constructed",
        budget_profile="small",
        repository_url=str(source),
        revision=revision,
        license="MIT",
        issue="Unary negation must return the sign-reversed value.",
        source_url="https://github.com/karpathy/micrograd",
        reproduce_command=verify_command,
        verify_command=verify_command,
        fault_patch="patches/fault.patch",
        reference_patch="patches/reference.patch",
        construction_notes="Local fixture derived from constructed-01 for offline replay.",
    )
    return case, catalog_root


def _budget() -> Budget:
    return Budget(
        max_tool_calls=6,
        max_patch_attempts=1,
        max_seconds=60,
        max_input_tokens=1_000,
        max_output_tokens=1_000,
        max_cost_usd=0.01,
    )


def test_full_pipeline_replays_in_docker_and_exports_evidence(tmp_path):
    case, catalog_root = _make_local_benchmark(tmp_path)
    store = TraceStore(tmp_path / "issueflow.sqlite3")
    sandbox = DockerSandbox()
    service = RunService(
        catalog={case.id: case},
        store=store,
        workspace_preparer=GitWorkspacePreparer(tmp_path / "workspaces", catalog_root),
        sandbox=sandbox,
        architecture_factory=lambda _kind, selected_case, workspace: SingleArchitecture(
            SingleAgent(
                model=ScriptedRepairModel(selected_case.verify_command),
                tools=ToolExecutor(workspace, selected_case, sandbox),
            )
        ),
        reviewer=Reviewer(),
    )

    result = service.start(case.id, _budget())

    assert result.status is RunStatus.SUCCEEDED
    assert result.functional_success is True
    assert result.review_status == "skipped"
    exported = store.export_json(result.id)
    assert exported["run"]["stop_reason"] == "functional_success"
    assert [step["sequence"] for step in exported["steps"]] == list(
        range(1, len(exported["steps"]) + 1)
    )
    assert [step["step_type"] for step in exported["steps"]] == [
        "reproduction",
        "tool",
        "tool",
        "tool",
        "tool",
        "verification",
        "diff",
        "review",
    ]
    assert exported["steps"][0]["status"] == "failed_as_expected"
    assert exported["steps"][5]["status"] == "passed"
    assert "return self * -1" in exported["steps"][6]["output_summary"]
    exported_text = store.export_json_text(result.id)
    assert json.loads(exported_text) == exported
    assert "DEEPSEEK_API_KEY" not in exported_text
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    assert not api_key or api_key not in exported_text


def test_recorded_live_agent_evidence_is_complete_and_credential_safe():
    evidence_path = (
        Path(__file__).parents[1] / "artifacts" / "phase-1" / "constructed-01-live-run.json"
    )

    evidence_text = evidence_path.read_text(encoding="utf-8")
    evidence = json.loads(evidence_text)

    assert evidence["run"]["case_id"] == "constructed-01"
    assert evidence["run"]["status"] == "succeeded"
    assert evidence["run"]["functional_success"] is True
    assert evidence["run"]["review_status"] == "approved"
    assert [step["sequence"] for step in evidence["steps"]] == list(range(1, 9))
    assert [step["step_type"] for step in evidence["steps"]] == [
        "reproduction",
        "tool",
        "tool",
        "tool",
        "tool",
        "verification",
        "diff",
        "review",
    ]
    assert sum(step["input_tokens"] for step in evidence["steps"]) == 3_570
    assert sum(step["output_tokens"] for step in evidence["steps"]) == 334
    assert sum(step["cost_usd"] for step in evidence["steps"]) == 0.0001894032
    assert "DEEPSEEK_API_KEY" not in evidence_text
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    assert not api_key or api_key not in evidence_text
