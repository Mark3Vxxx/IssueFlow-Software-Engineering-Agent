from __future__ import annotations

import json

import pytest

from issueflow.agent import ToolExecutor
from issueflow.architectures.base import ArchitectureKind, RoleName, RunContext
from issueflow.architectures.direct import DirectArchitecture, DirectPatch
from issueflow.models import BenchmarkCase, Budget, RunStatus, Usage
from issueflow.structured_model import ModelProtocolError, StructuredCompletion


class StubStructuredModel:
    def __init__(
        self,
        patch: DirectPatch | None = None,
        usage: Usage | None = None,
        error: ModelProtocolError | None = None,
    ) -> None:
        self.patch = patch or DirectPatch(
            path="engine.py",
            old_text="return 'broken'\n",
            new_text="return 'fixed'\n",
            explanation="Correct the returned value.",
        )
        self.usage = usage or Usage(
            model_calls=1,
            input_tokens=80,
            output_tokens=20,
            cost_usd=0.01,
        )
        self.error = error
        self.calls: list[tuple[str, dict[str, object], type[DirectPatch]]] = []

    def complete(self, system_prompt, payload, schema):
        self.calls.append((system_prompt, payload, schema))
        if self.error is not None:
            raise self.error
        return StructuredCompletion(value=self.patch, usage=self.usage)


@pytest.fixture
def case() -> BenchmarkCase:
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
        reference_patch="patches/constructed-01-fix.patch",
        construction_notes="Controlled arithmetic regression.",
        fault_patch="patches/constructed-01-fault.patch",
    )


@pytest.fixture
def budget() -> Budget:
    return Budget(
        max_tool_calls=2,
        max_patch_attempts=1,
        max_seconds=60,
        max_input_tokens=1_000,
        max_output_tokens=1_000,
        max_cost_usd=1.0,
    )


def test_direct_uses_one_model_call_and_one_patch(case, tmp_path, budget):
    target = tmp_path / "engine.py"
    target.write_text("return 'broken'\n", encoding="utf-8")
    model = StubStructuredModel()
    tools = ToolExecutor(tmp_path, case, sandbox=None)

    result = DirectArchitecture(model=model, tools=tools).run(
        case, tmp_path, budget, RunContext(run_id="run-direct")
    )

    assert result.architecture is ArchitectureKind.DIRECT
    assert result.status is RunStatus.SUCCEEDED
    assert result.stop_reason == "patch_applied"
    assert result.usage.model_calls == 1
    assert result.usage.tool_calls == 1
    assert result.usage.patch_attempts == 1
    assert result.role_usage[RoleName.DIRECT] == result.usage
    assert [step.role for step in result.steps] == ["direct", "direct"]
    assert target.read_text(encoding="utf-8") == "return 'fixed'\n"
    assert len(model.calls) == 1
    assert model.calls[0][2] is DirectPatch


def test_direct_does_not_retry_an_invalid_patch(case, tmp_path, budget):
    target = tmp_path / "engine.py"
    target.write_text("return 'broken'\n", encoding="utf-8")
    model = StubStructuredModel(
        patch=DirectPatch(
            path="engine.py",
            old_text="not present",
            new_text="return 'fixed'\n",
            explanation="Attempt a replacement.",
        )
    )

    result = DirectArchitecture(
        model=model,
        tools=ToolExecutor(tmp_path, case, sandbox=None),
    ).run(case, tmp_path, budget, RunContext(run_id="run-direct"))

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "patch_application_failure"
    assert result.usage.model_calls == 1
    assert result.usage.patch_attempts == 1
    assert len(model.calls) == 1
    assert target.read_text(encoding="utf-8") == "return 'broken'\n"


def test_direct_repository_map_is_deterministic_and_bounded(case, tmp_path, budget):
    for index in range(130):
        (tmp_path / f"file-{index:03}.py").write_text(f"value = {index}\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("do not include", encoding="utf-8")
    model = StubStructuredModel(
        patch=DirectPatch(
            path="file-000.py",
            old_text="value = 0\n",
            new_text="value = -1\n",
            explanation="Change the first value.",
        )
    )

    result = DirectArchitecture(
        model=model,
        tools=ToolExecutor(tmp_path, case, sandbox=None),
    ).run(case, tmp_path, budget, RunContext(run_id="run-direct"))

    assert result.status is RunStatus.SUCCEEDED
    payload = model.calls[0][1]
    repository_map = payload["repository_map"]
    assert payload["issue"] == case.issue
    assert len(repository_map) == 120
    assert [entry["path"] for entry in repository_map] == [
        f"file-{index:03}.py" for index in range(120)
    ]
    assert all(len(entry["preview"]) <= 400 for entry in repository_map)
    assert "ignored.txt" not in {entry["path"] for entry in repository_map}
    assert len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))) <= 20_000


def test_direct_repository_map_caps_total_payload_and_each_preview(case, tmp_path, budget):
    for index in range(80):
        (tmp_path / f"large-{index:03}.md").write_text("x" * 1_000, encoding="utf-8")
    model = StubStructuredModel(
        patch=DirectPatch(
            path="large-000.md",
            old_text="x" * 10,
            new_text="fixed",
            explanation="Shorten the first file.",
        )
    )

    DirectArchitecture(model=model, tools=ToolExecutor(tmp_path, case, None)).run(
        case, tmp_path, budget, RunContext(run_id="run-direct")
    )

    payload = model.calls[0][1]
    assert len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))) <= 20_000
    assert all(len(entry["preview"]) == 400 for entry in payload["repository_map"])
    assert len(payload["repository_map"]) < 80


@pytest.mark.parametrize(
    ("usage", "expected_reason"),
    [
        (Usage(model_calls=1, input_tokens=1_001), "input_token_budget_exhausted"),
        (Usage(model_calls=1, output_tokens=1_001), "output_token_budget_exhausted"),
        (Usage(model_calls=1, cost_usd=1.01), "cost_budget_exhausted"),
    ],
)
def test_direct_enforces_model_budgets_before_applying_patch(
    case, tmp_path, budget, usage, expected_reason
):
    target = tmp_path / "engine.py"
    target.write_text("return 'broken'\n", encoding="utf-8")
    model = StubStructuredModel(usage=usage)

    result = DirectArchitecture(model, ToolExecutor(tmp_path, case, None)).run(
        case, tmp_path, budget, RunContext(run_id="run-direct")
    )

    assert result.status is RunStatus.BUDGET_EXHAUSTED
    assert result.stop_reason == expected_reason
    assert result.usage.model_calls == 1
    assert result.usage.patch_attempts == 0
    assert target.read_text(encoding="utf-8") == "return 'broken'\n"


def test_direct_normalizes_model_protocol_failure_without_leaking_error(case, tmp_path, budget):
    secret = "top-secret-provider-detail"
    model = StubStructuredModel(
        error=ModelProtocolError(secret, Usage(model_calls=1, input_tokens=7))
    )

    result = DirectArchitecture(model, ToolExecutor(tmp_path, case, None)).run(
        case, tmp_path, budget, RunContext(run_id="run-direct")
    )

    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "model_protocol_failure"
    assert result.usage.model_calls == 1
    assert result.usage.input_tokens == 7
    assert secret not in result.model_dump_json()


def test_direct_charges_protocol_failure_usage_against_budget(case, tmp_path, budget):
    model = StubStructuredModel(
        error=ModelProtocolError(
            "invalid_structured_response",
            Usage(model_calls=1, input_tokens=1_001),
        )
    )

    result = DirectArchitecture(model, ToolExecutor(tmp_path, case, None)).run(
        case, tmp_path, budget, RunContext(run_id="run-direct")
    )

    assert result.status is RunStatus.BUDGET_EXHAUSTED
    assert result.stop_reason == "input_token_budget_exhausted"
    assert result.usage.input_tokens == 1_001


def test_direct_normalizes_patch_timeout_as_time_budget_exhaustion(case, tmp_path, budget):
    class TimedOutTools:
        workspace = tmp_path.resolve()

        def execute(self, action, timeout_seconds=60):
            raise TimeoutError("provider detail")

    result = DirectArchitecture(StubStructuredModel(), TimedOutTools()).run(
        case, tmp_path, budget, RunContext(run_id="run-direct")
    )

    assert result.status is RunStatus.TIMED_OUT
    assert result.stop_reason == "time_budget_exhausted"
    assert result.usage.tool_calls == 1
    assert result.usage.patch_attempts == 1
    assert "provider detail" not in result.model_dump_json()


def test_direct_stops_on_time_budget_before_applying_patch(case, tmp_path, budget):
    target = tmp_path / "engine.py"
    target.write_text("return 'broken'\n", encoding="utf-8")
    model = StubStructuredModel()
    readings = iter([0.0, 0.0, 61.0, 61.0])

    result = DirectArchitecture(
        model,
        ToolExecutor(tmp_path, case, None),
        clock=lambda: next(readings),
    ).run(case, tmp_path, budget, RunContext(run_id="run-direct"))

    assert result.status is RunStatus.TIMED_OUT
    assert result.stop_reason == "time_budget_exhausted"
    assert result.usage.model_calls == 1
    assert result.usage.patch_attempts == 0
    assert target.read_text(encoding="utf-8") == "return 'broken'\n"
