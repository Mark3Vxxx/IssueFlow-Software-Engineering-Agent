"""Offline four-arm acceptance through the real IssueFlow outer pipeline."""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from subprocess import run

import pytest

from issueflow.agent import ModelAction
from issueflow.architectures.base import ArchitectureKind, ArchitectureRunner, RunContext
from issueflow.architectures.direct import DirectPatch
from issueflow.architectures.dynamic import SupervisorDecision
from issueflow.architectures.factory import ArchitectureFactory
from issueflow.architectures.state import (
    CoderOutput,
    CoderToolCall,
    EvidenceBundle,
    EvidenceItem,
    PlanOutput,
    RetrievalToolCall,
    ReviewOutput,
)
from issueflow.models import BenchmarkCase, Budget, RunStatus, Usage
from issueflow.reviewer import Reviewer
from issueflow.run_service import GitWorkspacePreparer, RunService
from issueflow.sandbox import DockerSandbox
from issueflow.structured_model import StructuredCompletion
from issueflow.trace_store import TraceStore

SHARED_BUDGET = Budget(
    max_tool_calls=8,
    max_patch_attempts=1,
    max_seconds=60,
    max_input_tokens=1_000,
    max_output_tokens=1_000,
    max_cost_usd=0.01,
)

EXPECTED_ARCHITECTURE_STEPS = {
    ArchitectureKind.DIRECT: [("model", "direct"), ("tool", "direct")],
    ArchitectureKind.SINGLE: [
        ("tool", "single_agent"),
        ("tool", "single_agent"),
        ("tool", "single_agent"),
        ("tool", "single_agent"),
    ],
    ArchitectureKind.FIXED: [
        ("role", "planner"),
        ("role", "retriever"),
        ("role", "coder"),
        ("role", "reviewer"),
    ],
    ArchitectureKind.DYNAMIC: [
        ("route", "supervisor"),
        ("role", "planner"),
        ("route", "supervisor"),
        ("role", "retriever"),
        ("route", "supervisor"),
        ("role", "coder"),
        ("route", "supervisor"),
        ("role", "reviewer"),
        ("route", "supervisor"),
    ],
}


class ScriptedSingleModel:
    """Exercise the phase-one agent loop without a network model call."""

    def __init__(self, verify_command: str) -> None:
        self.actions = deque(
            [
                ModelAction(tool="search", arguments={"query": "def __neg__"}),
                ModelAction(
                    tool="read_file",
                    arguments={
                        "path": "micrograd/engine.py",
                        "start_line": 1,
                        "end_line": 12,
                    },
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
        del issue, history
        return self.actions.popleft()


class ScriptedStructuredModel:
    """Drive Direct and both LangGraph arms through their real tool boundaries."""

    def __init__(self, verify_command: str) -> None:
        self.verify_command = verify_command

    def complete(self, system_prompt, payload, schema):
        del system_prompt
        if schema is DirectPatch:
            value = DirectPatch(
                path="micrograd/engine.py",
                old_text="        return self\n",
                new_text="        return self * -1\n",
                explanation="Restore sign reversal.",
            )
        elif schema is PlanOutput:
            value = PlanOutput(
                steps=["Inspect unary negation and apply the smallest repair."],
                target_files=["micrograd/engine.py"],
                validation_conditions=["The registered regression command passes."],
            )
        elif schema is EvidenceBundle:
            value = EvidenceBundle(
                items=[
                    EvidenceItem(
                        path="micrograd/engine.py",
                        line=8,
                        summary="Unary negation returns the original value.",
                    )
                ],
                tool_calls=[
                    RetrievalToolCall(
                        tool="read_file",
                        arguments={
                            "path": "micrograd/engine.py",
                            "start_line": 7,
                            "end_line": 9,
                        },
                    )
                ],
            )
        elif schema is CoderOutput:
            value = CoderOutput(
                current_diff=("-        return self\n+        return self * -1\n"),
                tool_calls=[
                    CoderToolCall(
                        tool="apply_patch",
                        arguments={
                            "path": "micrograd/engine.py",
                            "old_text": "        return self\n",
                            "new_text": "        return self * -1\n",
                        },
                    ),
                    CoderToolCall(
                        tool="run_tests",
                        arguments={"command": self.verify_command},
                    ),
                ],
            )
        elif schema is ReviewOutput:
            value = ReviewOutput(status="approved", feedback="The repair is focused.")
        elif schema is SupervisorDecision:
            if payload["plan"] is None:
                next_role = "planner"
            elif not payload["evidence"]:
                next_role = "retriever"
            elif not str(payload["current_diff"]).strip():
                next_role = "coder"
            elif payload["review_feedback"] is None:
                next_role = "reviewer"
            else:
                next_role = "stop"
            value = SupervisorDecision(
                next_role=next_role,
                reason=f"scripted route to {next_role}",
            )
        else:  # pragma: no cover - a new production role must extend this acceptance script.
            raise AssertionError(f"unexpected structured schema: {schema}")
        return StructuredCompletion(value=value, usage=Usage(model_calls=1))


class RecordingRunner:
    """Observe the shared budget at the architecture contract boundary."""

    def __init__(self, runner: ArchitectureRunner, received_budgets: list[Budget]) -> None:
        self.runner = runner
        self.received_budgets = received_budgets

    def run(self, case, workspace, budget, context: RunContext):
        self.received_budgets.append(budget)
        return self.runner.run(case, workspace, budget, context)


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


@pytest.mark.parametrize("architecture", list(ArchitectureKind), ids=lambda item: item.value)
def test_each_architecture_repairs_through_the_same_real_outer_pipeline(
    tmp_path: Path,
    architecture: ArchitectureKind,
):
    case, catalog_root = _make_local_benchmark(tmp_path)
    store = TraceStore(tmp_path / "issueflow.sqlite3")
    sandbox = DockerSandbox()
    received_budgets: list[Budget] = []
    factory = ArchitectureFactory(
        single_model_factory=lambda selected_case: ScriptedSingleModel(
            selected_case.verify_command
        ),
        structured_model=ScriptedStructuredModel(case.verify_command),
        sandbox=sandbox,
    )

    def architecture_factory(kind, selected_case, workspace):
        return RecordingRunner(
            factory.create(kind, selected_case, workspace),
            received_budgets,
        )

    service = RunService(
        catalog={case.id: case},
        store=store,
        workspace_preparer=GitWorkspacePreparer(tmp_path / "workspaces", catalog_root),
        sandbox=sandbox,
        architecture_factory=architecture_factory,
        reviewer=Reviewer(),
    )

    result = service.start(case.id, SHARED_BUDGET, architecture)

    assert received_budgets == [SHARED_BUDGET]
    assert received_budgets[0] is SHARED_BUDGET
    assert received_budgets[0].model_dump() == {
        "max_tool_calls": 8,
        "max_patch_attempts": 1,
        "max_seconds": 60,
        "max_input_tokens": 1_000,
        "max_output_tokens": 1_000,
        "max_cost_usd": 0.01,
    }
    assert result.status is RunStatus.SUCCEEDED
    assert result.functional_success is True
    assert result.stop_reason == "functional_success"
    assert result.architecture == architecture.value

    exported = store.export_json(result.id)
    architecture_steps = [
        (step["step_type"], step["role"])
        for step in exported["steps"]
        if step["step_type"] not in {"reproduction", "verification", "diff", "review"}
    ]
    assert architecture_steps == EXPECTED_ARCHITECTURE_STEPS[architecture]
    assert exported["run"]["architecture"] == architecture.value
    assert exported["steps"][0]["status"] == "failed_as_expected"
    assert exported["steps"][-3]["status"] == "passed"
    assert "return self * -1" in exported["steps"][-2]["output_summary"]
    assert [step["sequence"] for step in exported["steps"]] == list(
        range(1, len(exported["steps"]) + 1)
    )
    exported_text = store.export_json_text(result.id)
    assert json.loads(exported_text) == exported
    assert store.export_json(result.id) == exported
