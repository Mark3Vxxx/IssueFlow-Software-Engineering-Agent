from collections import deque
from dataclasses import dataclass

import pytest

from issueflow.agent import ModelAction, SingleAgent, ToolExecutor
from issueflow.models import BenchmarkCase, Budget, RunStatus


class FakeModel:
    def __init__(self, actions: list[ModelAction]) -> None:
        self.actions = deque(actions)

    def next_action(self, issue: str, history: list[dict[str, object]]) -> ModelAction:
        return self.actions.popleft()


@dataclass
class FakeSandboxResult:
    returncode: int
    output: str
    timed_out: bool = False


class FakeSandbox:
    def __init__(
        self, returncode: int = 0, output: str = "1 passed", timed_out: bool = False
    ) -> None:
        self.returncode = returncode
        self.output = output
        self.timed_out = timed_out
        self.commands: list[str] = []

    def run(self, workspace, command: str, timeout_seconds: int) -> FakeSandboxResult:
        self.commands.append(command)
        return FakeSandboxResult(
            returncode=self.returncode,
            output=self.output,
            timed_out=self.timed_out,
        )


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
        reproduce_command="python -c 'raise SystemExit(1)'",
        verify_command="python -c 'raise SystemExit(0)'",
        fault_patch="patches/constructed-01-fault.patch",
        reference_patch="patches/constructed-01-fix.patch",
        construction_notes="Controlled arithmetic regression.",
    )


def make_budget(max_tool_calls: int = 2, **overrides) -> Budget:
    values = {
        "max_tool_calls": max_tool_calls,
        "max_patch_attempts": 1,
        "max_seconds": 60,
        "max_input_tokens": 1_000,
        "max_output_tokens": 1_000,
        "max_cost_usd": 1.0,
    }
    values.update(overrides)
    return Budget(**values)


def test_agent_rejects_a_disallowed_tool(tmp_path):
    model = FakeModel([ModelAction(tool="shell", arguments={"command": "curl example.com"})])
    agent = SingleAgent(model=model, tools=ToolExecutor(tmp_path, make_case(), sandbox=None))

    result = agent.run(make_case(), tmp_path, make_budget())

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "disallowed_tool:shell"


def test_agent_stops_when_tool_budget_is_exhausted(tmp_path):
    (tmp_path / "engine.py").write_text("class Value:\n    pass\n", encoding="utf-8")
    model = FakeModel([ModelAction(tool="search", arguments={"query": "Value"}) for _ in range(3)])
    agent = SingleAgent(model=model, tools=ToolExecutor(tmp_path, make_case(), sandbox=None))

    result = agent.run(make_case(), tmp_path, make_budget(max_tool_calls=2))

    assert result.status is RunStatus.BUDGET_EXHAUSTED
    assert result.stop_reason == "tool_budget_exhausted"
    assert len(result.steps) == 2


def test_read_file_returns_requested_lines(tmp_path):
    (tmp_path / "engine.py").write_text("first\nsecond\nthird\n", encoding="utf-8")
    tools = ToolExecutor(tmp_path, make_case(), sandbox=None)

    output = tools.execute(
        ModelAction(
            tool="read_file",
            arguments={"path": "engine.py", "start_line": 2, "end_line": 3},
        )
    )

    assert output == "2: second\n3: third"


def test_read_file_rejects_paths_outside_workspace(tmp_path):
    tools = ToolExecutor(tmp_path, make_case(), sandbox=None)

    try:
        tools.execute(ModelAction(tool="read_file", arguments={"path": "../secret.txt"}))
    except ValueError as error:
        assert str(error) == "path must stay inside workspace"
    else:
        raise AssertionError("workspace traversal should be rejected")


def replacement_patch() -> str:
    return """diff --git a/engine.py b/engine.py
--- a/engine.py
+++ b/engine.py
@@ -1 +1 @@
-return 'broken'
+return 'fixed'
"""


def test_apply_patch_changes_only_workspace_file(tmp_path):
    target = tmp_path / "engine.py"
    target.write_text("return 'broken'\n", encoding="utf-8")
    tools = ToolExecutor(tmp_path, make_case(), sandbox=None)

    output = tools.execute(
        ModelAction(tool="apply_patch", arguments={"patch": replacement_patch()})
    )

    assert output == "patch applied"
    assert target.read_text(encoding="utf-8") == "return 'fixed'\n"


def test_apply_patch_rejects_workspace_traversal(tmp_path):
    tools = ToolExecutor(tmp_path, make_case(), sandbox=None)
    unsafe_patch = """--- a/../secret.txt
+++ b/../secret.txt
@@ -1 +1 @@
-secret
+exposed
"""

    try:
        tools.execute(ModelAction(tool="apply_patch", arguments={"patch": unsafe_patch}))
    except ValueError as error:
        assert str(error) == "path must stay inside workspace"
    else:
        raise AssertionError("patch traversal should be rejected")


def test_agent_stops_at_patch_attempt_budget(tmp_path):
    (tmp_path / "engine.py").write_text("return 'broken'\n", encoding="utf-8")
    actions = [
        ModelAction(tool="apply_patch", arguments={"patch": replacement_patch()}),
        ModelAction(tool="apply_patch", arguments={"patch": replacement_patch()}),
    ]
    agent = SingleAgent(
        model=FakeModel(actions),
        tools=ToolExecutor(tmp_path, make_case(), sandbox=None),
    )

    result = agent.run(make_case(), tmp_path, make_budget(max_tool_calls=3))

    assert result.status is RunStatus.BUDGET_EXHAUSTED
    assert result.stop_reason == "patch_budget_exhausted"
    assert result.patch_attempts == 1


def test_run_tests_uses_sandbox_for_registered_command(tmp_path):
    sandbox = FakeSandbox()
    case = make_case()
    tools = ToolExecutor(tmp_path, case, sandbox=sandbox)

    output = tools.execute(
        ModelAction(tool="run_tests", arguments={"command": case.verify_command})
    )

    assert sandbox.commands == [case.verify_command]
    assert output == "exit_code=0\n1 passed"


def test_run_tests_rejects_unregistered_command(tmp_path):
    sandbox = FakeSandbox()
    tools = ToolExecutor(tmp_path, make_case(), sandbox=sandbox)

    try:
        tools.execute(ModelAction(tool="run_tests", arguments={"command": "cat ~/.ssh/id_rsa"}))
    except ValueError as error:
        assert str(error) == "test command is not registered for this case"
    else:
        raise AssertionError("an unregistered command should be rejected")

    assert sandbox.commands == []


def test_agent_enforces_input_token_budget_before_executing_tool(tmp_path):
    model = FakeModel(
        [ModelAction(tool="search", arguments={"query": "Value"}, input_tokens=1_001)]
    )
    agent = SingleAgent(model=model, tools=ToolExecutor(tmp_path, make_case(), None))

    result = agent.run(make_case(), tmp_path, make_budget())

    assert result.status is RunStatus.BUDGET_EXHAUSTED
    assert result.stop_reason == "input_token_budget_exhausted"
    assert result.tool_calls == 0
    assert result.input_tokens == 1_001


def test_agent_normalizes_invalid_tool_arguments(tmp_path):
    model = FakeModel([ModelAction(tool="read_file", arguments={"path": "../secret.txt"})])
    agent = SingleAgent(model=model, tools=ToolExecutor(tmp_path, make_case(), None))

    result = agent.run(make_case(), tmp_path, make_budget())

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "invalid_arguments:read_file"
    assert result.steps[-1].status == "failed"


def test_agent_completes_search_read_patch_and_verification(tmp_path):
    (tmp_path / "engine.py").write_text("return 'broken'\n", encoding="utf-8")
    case = make_case()
    sandbox = FakeSandbox(returncode=0, output="1 passed")
    model = FakeModel(
        [
            ModelAction(tool="search", arguments={"query": "broken"}),
            ModelAction(tool="read_file", arguments={"path": "engine.py"}),
            ModelAction(tool="apply_patch", arguments={"patch": replacement_patch()}),
            ModelAction(tool="run_tests", arguments={"command": case.verify_command}),
        ]
    )
    agent = SingleAgent(model=model, tools=ToolExecutor(tmp_path, case, sandbox))

    result = agent.run(case, tmp_path, make_budget(max_tool_calls=5))

    assert result.status is RunStatus.SUCCEEDED
    assert result.stop_reason == "verification_passed"
    assert result.tool_calls == 4
    assert result.patch_attempts == 1
    assert [step.status for step in result.steps] == ["completed"] * 4
    assert (tmp_path / "engine.py").read_text(encoding="utf-8") == "return 'fixed'\n"


@pytest.mark.parametrize(
    ("usage", "budget_override", "expected_reason"),
    [
        ({"output_tokens": 1_001}, {}, "output_token_budget_exhausted"),
        ({"cost_usd": 1.01}, {}, "cost_budget_exhausted"),
    ],
)
def test_agent_enforces_remaining_model_budgets(tmp_path, usage, budget_override, expected_reason):
    action = ModelAction(tool="search", arguments={"query": "Value"}, **usage)
    agent = SingleAgent(
        model=FakeModel([action]),
        tools=ToolExecutor(tmp_path, make_case(), None),
    )

    result = agent.run(make_case(), tmp_path, make_budget(**budget_override))

    assert result.status is RunStatus.BUDGET_EXHAUSTED
    assert result.stop_reason == expected_reason
    assert result.tool_calls == 0


def test_agent_stops_when_total_time_budget_is_exhausted(tmp_path):
    readings = iter([0.0, 0.0, 61.0, 61.0])
    agent = SingleAgent(
        model=FakeModel([ModelAction(tool="search", arguments={"query": "Value"})]),
        tools=ToolExecutor(tmp_path, make_case(), None),
        clock=lambda: next(readings),
    )

    result = agent.run(make_case(), tmp_path, make_budget())

    assert result.status is RunStatus.TIMED_OUT
    assert result.stop_reason == "time_budget_exhausted"
    assert result.tool_calls == 0


def test_agent_normalizes_sandbox_timeout(tmp_path):
    case = make_case()
    model = FakeModel([ModelAction(tool="run_tests", arguments={"command": case.verify_command})])
    sandbox = FakeSandbox(returncode=124, output="partial", timed_out=True)
    agent = SingleAgent(model=model, tools=ToolExecutor(tmp_path, case, sandbox))

    result = agent.run(case, tmp_path, make_budget())

    assert result.status is RunStatus.TIMED_OUT
    assert result.stop_reason == "tool_timeout:run_tests"
    assert result.steps[-1].output_summary == "partial"


def test_tool_rejects_unexpected_arguments(tmp_path):
    tools = ToolExecutor(tmp_path, make_case(), None)

    with pytest.raises(ValueError, match="unexpected arguments for search"):
        tools.execute(
            ModelAction(
                tool="search",
                arguments={"query": "Value", "command": "unsafe"},
            )
        )


def test_agent_rejects_mismatched_workspace(tmp_path):
    other_workspace = tmp_path / "other"
    other_workspace.mkdir()
    agent = SingleAgent(
        model=FakeModel([ModelAction(tool="search", arguments={"query": "Value"})]),
        tools=ToolExecutor(tmp_path, make_case(), None),
    )

    result = agent.run(make_case(), other_workspace, make_budget())

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "workspace_mismatch"


def test_search_treats_leading_dash_query_as_plain_text(tmp_path):
    (tmp_path / "engine.py").write_text("ordinary text\n", encoding="utf-8")
    tools = ToolExecutor(tmp_path, make_case(), None)

    output = tools.execute(ModelAction(tool="search", arguments={"query": "--files"}))

    assert output == "no matches"


def test_agent_rejects_model_finish_without_verification(tmp_path):
    agent = SingleAgent(
        model=FakeModel([ModelAction(message="Done")]),
        tools=ToolExecutor(tmp_path, make_case(), None),
    )

    result = agent.run(make_case(), tmp_path, make_budget())

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "model_finished_without_verification"
    assert result.final_message == "Done"


def test_apply_patch_accepts_model_update_file_envelope(tmp_path):
    target = tmp_path / "micrograd" / "engine.py"
    target.parent.mkdir()
    target.write_text(
        "class Value:\n\n    def __neg__(self): # -self\n        return self\n",
        encoding="utf-8",
    )
    tools = ToolExecutor(tmp_path, make_case(), None)
    model_patch = """*** Begin Patch
*** Update File: micrograd/engine.py
@@
     def __neg__(self): # -self
-        return self
+        return self * -1
*** End Patch
"""

    output = tools.execute(ModelAction(tool="apply_patch", arguments={"patch": model_patch}))

    assert output == "patch applied"
    assert "return self * -1" in target.read_text(encoding="utf-8")


def test_model_update_file_envelope_rejects_workspace_traversal(tmp_path):
    tools = ToolExecutor(tmp_path, make_case(), None)
    model_patch = """*** Begin Patch
*** Update File: ../secret.txt
@@
-secret
+exposed
*** End Patch
"""

    with pytest.raises(ValueError, match="path must stay inside workspace"):
        tools.execute(ModelAction(tool="apply_patch", arguments={"patch": model_patch}))


def structured_patch(path: str, old_text: str, new_text: str) -> ModelAction:
    return ModelAction(
        tool="apply_patch",
        arguments={"path": path, "old_text": old_text, "new_text": new_text},
    )


def test_structured_patch_replaces_exactly_one_match(tmp_path):
    target = tmp_path / "micrograd" / "engine.py"
    target.parent.mkdir()
    target.write_text("def negate():\n    return self\n", encoding="utf-8")
    tools = ToolExecutor(tmp_path, make_case(), None)

    output = tools.execute(
        structured_patch(
            "micrograd/engine.py",
            "    return self\n",
            "    return self * -1\n",
        )
    )

    assert output == "patch applied"
    assert target.read_text(encoding="utf-8") == "def negate():\n    return self * -1\n"


def test_structured_patch_rejects_zero_matches(tmp_path):
    (tmp_path / "engine.py").write_text("return self\n", encoding="utf-8")
    tools = ToolExecutor(tmp_path, make_case(), None)

    with pytest.raises(ValueError, match="old_text must match exactly once: found 0"):
        tools.execute(structured_patch("engine.py", "missing\n", "replacement\n"))


def test_structured_patch_rejects_multiple_matches(tmp_path):
    (tmp_path / "engine.py").write_text("same\nsame\n", encoding="utf-8")
    tools = ToolExecutor(tmp_path, make_case(), None)

    with pytest.raises(ValueError, match="old_text must match exactly once: found 2"):
        tools.execute(structured_patch("engine.py", "same\n", "changed\n"))


def test_structured_patch_rejects_workspace_traversal(tmp_path):
    tools = ToolExecutor(tmp_path, make_case(), None)

    with pytest.raises(ValueError, match="path must stay inside workspace"):
        tools.execute(structured_patch("../secret.txt", "secret", "exposed"))
