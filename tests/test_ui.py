from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from pydantic import SecretStr
from streamlit.testing.v1 import AppTest

from issueflow.architectures.base import ArchitectureKind
from issueflow.benchmark import load_catalog
from issueflow.budget import budget_for_case
from issueflow.config import Settings
from issueflow.models import BenchmarkCase, Budget, RunRecord, RunStatus
from issueflow.ui import (
    RunSession,
    build_runtime,
    describe_stop_reason,
    format_budget_summary,
    make_case_view,
    make_run_view,
)


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("patch_budget_exhausted", "补丁次数预算已用尽"),
        ("tool_budget_exhausted", "工具调用预算已用尽"),
        ("time_budget_exhausted", "运行时间预算已用尽"),
        ("input_token_budget_exhausted", "输入 Token 预算已用尽"),
        ("output_token_budget_exhausted", "输出 Token 预算已用尽"),
        ("cost_budget_exhausted", "成本预算已用尽"),
    ],
)
def test_stop_reason_describes_each_budget_boundary(reason, expected):
    assert describe_stop_reason(reason) == expected


def test_stop_reason_redacts_unknown_technical_text():
    assert describe_stop_reason("DEEPSEEK_API_KEY=secret") == "[REDACTED]"


def test_budget_summary_contains_profile_and_every_limit():
    case = load_catalog(Path("benchmarks/catalogs/compatibility.yaml"))["historical-01"]
    budget = budget_for_case(case)

    assert format_budget_summary(case, budget) == (
        "预算档位：medium · 工具 18 次 · 补丁 4 次 · 450 秒 · "
        "输入 50,000 Token · 输出 8,000 Token · 最高 $0.10"
    )


def test_case_view_distinguishes_constructed_samples_from_historical_repairs():
    case = BenchmarkCase(
        id="constructed-01",
        dataset_split="compatibility",
        repository_id="micrograd",
        environment_id="micrograd",
        difficulty="small",
        issue_category="numerical",
        kind="constructed",
        budget_profile="small",
        repository_url="https://github.com/karpathy/micrograd",
        revision="a" * 40,
        license="MIT",
        issue="Unary negation returns the wrong value.",
        source_url="https://github.com/karpathy/micrograd",
        reproduce_command="python -m pytest",
        verify_command="python -m pytest",
        fault_patch="patches/constructed-01-fault.patch",
        reference_patch="patches/constructed-01-fix.patch",
        construction_notes="Controlled arithmetic regression.",
    )

    view = make_case_view(case, latest_run=None)

    assert view.origin_label == "自建边界样本"
    assert view.latest_status_label == "尚未运行"


def test_case_view_labels_historical_repairs_explicitly():
    case = BenchmarkCase(
        id="historical-01",
        dataset_split="compatibility",
        repository_id="micrograd",
        environment_id="micrograd",
        difficulty="medium",
        issue_category="model_training",
        kind="historical",
        budget_profile="medium",
        repository_url="https://github.com/karpathy/micrograd",
        revision="a" * 40,
        license="MIT",
        issue="Shared graphs leave gradients at zero.",
        source_url="https://github.com/karpathy/micrograd/commit/fix",
        reproduce_command="python -m pytest",
        verify_command="python -m pytest",
        reference_patch="patches/historical-01-fix.patch",
        construction_notes="Historical public repair.",
    )

    view = make_case_view(case, latest_run=None)

    assert view.origin_label == "历史修复样本"


def test_case_view_translates_latest_run_status_for_the_workbench():
    case = BenchmarkCase(
        id="constructed-01",
        dataset_split="compatibility",
        repository_id="micrograd",
        environment_id="micrograd",
        difficulty="small",
        issue_category="numerical",
        kind="constructed",
        budget_profile="small",
        repository_url="https://github.com/karpathy/micrograd",
        revision="a" * 40,
        license="MIT",
        issue="Unary negation returns the wrong value.",
        source_url="https://github.com/karpathy/micrograd",
        reproduce_command="python -m pytest",
        verify_command="python -m pytest",
        fault_patch="patches/constructed-01-fault.patch",
        reference_patch="patches/constructed-01-fix.patch",
        construction_notes="Controlled arithmetic regression.",
    )
    latest_run = RunRecord(
        id="run-123",
        case_id=case.id,
        status=RunStatus.SUCCEEDED,
        functional_success=True,
    )

    view = make_case_view(case, latest_run=latest_run)

    assert view.latest_status_label == "成功"


def test_run_view_aggregates_efficiency_metrics_from_persisted_steps():
    trace = {
        "run": {
            "id": "run-123",
            "case_id": "constructed-01",
            "status": "failed",
            "stop_reason": "patch_budget_exhausted",
            "functional_success": False,
            "review_status": "approved",
            "review_reasons": ["Focused fix."],
            "usage": {
                "model_calls": 2,
                "tool_calls": 2,
                "patch_attempts": 1,
                "input_tokens": 180,
                "output_tokens": 50,
                "cost_usd": 0.00003,
                "duration_ms": 40,
            },
        },
        "steps": [
            {
                "sequence": 1,
                "role": "single_agent",
                "step_type": "reproduction",
                "input_summary": "python -c assertion",
                "output_summary": "exit_code=1",
                "status": "failed_as_expected",
                "duration_ms": 12,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
            },
            {
                "sequence": 2,
                "role": "single_agent",
                "step_type": "tool",
                "input_summary": "search: {'query': '__neg__'}",
                "output_summary": "engine.py:72",
                "status": "completed",
                "duration_ms": 8,
                "input_tokens": 100,
                "output_tokens": 30,
                "cost_usd": 0.00002,
            },
            {
                "sequence": 3,
                "role": "single_agent",
                "step_type": "tool",
                "input_summary": "run_tests: {'command': 'python -c assertion'}",
                "output_summary": "exit_code=0",
                "status": "completed",
                "duration_ms": 20,
                "input_tokens": 80,
                "output_tokens": 20,
                "cost_usd": 0.00001,
            },
        ],
        "artifacts": [],
    }

    view = make_run_view(trace)

    assert view.stop_reason_label == "补丁次数预算已用尽"
    assert view.metrics == {
        "duration_ms": 40,
        "tool_calls": 2,
        "input_tokens": 180,
        "output_tokens": 50,
        "cost_usd": 0.00003,
    }
    assert view.architecture_label == "Single Agent"
    assert view.role_call_counts == {"single_agent": 2}
    assert view.route_count == 0


def test_run_view_uses_authoritative_run_usage_without_summing_steps_twice():
    trace = {
        "run": {
            "architecture": "fixed",
            "status": "succeeded",
            "usage": {
                "model_calls": 4,
                "tool_calls": 3,
                "patch_attempts": 1,
                "input_tokens": 100,
                "output_tokens": 20,
                "cost_usd": 0.001,
                "duration_ms": 80,
            },
        },
        "steps": [
            {
                "sequence": 1,
                "role": "planner",
                "step_type": "role",
                "input_summary": "bounded workflow state",
                "output_summary": "plan",
                "status": "completed",
                "duration_ms": 80,
                "input_tokens": 100,
                "output_tokens": 20,
                "cost_usd": 0.001,
            },
            {
                "sequence": 2,
                "role": "coder",
                "step_type": "tool",
                "input_summary": "apply_patch",
                "output_summary": "done",
                "status": "completed",
                "duration_ms": 80,
                "input_tokens": 100,
                "output_tokens": 20,
                "cost_usd": 0.001,
            },
        ],
        "artifacts": [],
    }

    assert make_run_view(trace).metrics == {
        "duration_ms": 80,
        "tool_calls": 3,
        "input_tokens": 100,
        "output_tokens": 20,
        "cost_usd": 0.001,
    }


def test_run_view_builds_a_redacted_human_readable_timeline():
    trace = {
        "run": {
            "id": "run-123",
            "case_id": "constructed-01",
            "status": "succeeded",
            "stop_reason": "functional_success",
            "functional_success": True,
            "review_status": "approved",
            "review_reasons": ["DEEPSEEK_API_KEY=secret-value"],
        },
        "steps": [
            {
                "sequence": 1,
                "role": "single_agent",
                "step_type": "tool",
                "input_summary": "read_file: {'path': 'engine.py'}",
                "output_summary": "Authorization: Bearer sk-private-token",
                "status": "completed",
                "duration_ms": 4,
                "input_tokens": 20,
                "output_tokens": 10,
                "cost_usd": 0.00001,
            },
            {
                "sequence": 2,
                "role": "single_agent",
                "step_type": "diff",
                "input_summary": "git diff --binary HEAD",
                "output_summary": "diff --git a/engine.py b/engine.py",
                "status": "completed",
                "duration_ms": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
            },
        ],
        "artifacts": [],
    }

    view = make_run_view(trace)

    assert view.status_label == "成功"
    assert [item.title for item in view.timeline] == ["读取文件", "代码改动"]
    assert view.timeline[0].output_summary == "[REDACTED]"
    assert view.review_reasons == ("[REDACTED]",)
    assert view.diff_text == "diff --git a/engine.py b/engine.py"
    assert "secret-value" not in repr(view)
    assert "sk-private-token" not in repr(view)


def test_run_view_does_not_count_skipped_review_or_failed_route_as_calls():
    trace = {
        "run": {
            "architecture": "dynamic",
            "status": "failed",
            "review_status": "skipped",
            "review_reasons": [],
        },
        "steps": [
            {
                "sequence": 1,
                "role": "supervisor",
                "step_type": "model",
                "input_summary": "bounded workflow state",
                "output_summary": "invalid supervisor output",
                "status": "failed",
                "duration_ms": 0,
                "input_tokens": 10,
                "output_tokens": 2,
                "cost_usd": 0.00001,
            },
            {
                "sequence": 2,
                "role": "reviewer",
                "step_type": "review",
                "input_summary": "deterministic gates",
                "output_summary": "skipped",
                "status": "skipped",
                "duration_ms": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
            },
        ],
        "artifacts": [],
    }

    view = make_run_view(trace)

    assert view.role_call_counts == {"supervisor": 1}
    assert view.route_count == 0


def test_run_session_prevents_parallel_runs_and_returns_the_finished_record():
    entered = Event()
    release = Event()
    expected = RunRecord(
        id="run-123",
        case_id="constructed-01",
        status=RunStatus.SUCCEEDED,
        functional_success=True,
    )

    class BlockingService:
        def start(
            self,
            case_id: str,
            budget: Budget,
            architecture: ArchitectureKind,
        ) -> RunRecord:
            assert architecture is ArchitectureKind.SINGLE
            entered.set()
            release.wait(timeout=1)
            return expected

    budget = Budget(
        max_tool_calls=12,
        max_patch_attempts=2,
        max_seconds=300,
        max_input_tokens=30_000,
        max_output_tokens=6_000,
        max_cost_usd=0.05,
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        session = RunSession(BlockingService(), executor.submit)
        session.start("constructed-01", budget)
        assert entered.wait(timeout=1)
        assert session.is_running is True

        with pytest.raises(RuntimeError, match="already running"):
            session.start("constructed-01", budget)

        release.set()
    result = session.poll()

    assert result == expected
    assert session.is_running is False


def test_completed_run_stays_active_until_the_page_collects_its_result():
    expected = RunRecord(
        id="run-123",
        case_id="constructed-01",
        status=RunStatus.SUCCEEDED,
    )

    class ImmediateService:
        def start(
            self,
            case_id: str,
            budget: Budget,
            architecture: ArchitectureKind,
        ) -> RunRecord:
            assert architecture is ArchitectureKind.SINGLE
            return expected

    def immediate_submit(function, *args):
        future = Future()
        future.set_result(function(*args))
        return future

    session = RunSession(ImmediateService(), immediate_submit)
    session.start(
        "constructed-01",
        Budget(
            max_tool_calls=12,
            max_patch_attempts=2,
            max_seconds=300,
            max_input_tokens=30_000,
            max_output_tokens=6_000,
            max_cost_usd=0.05,
        ),
    )

    assert session.is_running is True
    assert session.poll() == expected
    assert session.is_running is False


def test_workbench_renders_all_five_cases_with_clear_origin_labels():
    script = """
from pathlib import Path

from issueflow.benchmark import load_catalog
from issueflow.ui import render_app


class StaticService:
    catalog = load_catalog(Path("benchmarks/catalogs/compatibility.yaml"))


class EmptyStore:
    pass


render_app(EmptyStore(), StaticService())
"""

    app = AppTest.from_string(script).run()

    assert not app.exception
    assert app.title[0].value == "IssueFlow Agent 架构工作台"
    assert len(app.selectbox[0].options) == 5
    assert app.selectbox[0].options[0] == "historical-01 · 历史修复样本"
    assert app.selectbox[0].options[1] == "constructed-01 · 自建边界样本"
    assert app.selectbox[1].options == [
        "Direct",
        "Single Agent",
        "Fixed Multi-Agent",
        "Dynamic Supervisor",
    ]
    assert app.selectbox[1].value == "Single Agent"
    assert app.button[0].label == "开始真实修复"


def test_workbench_runs_selected_case_and_renders_persisted_evidence():
    script = """
from concurrent.futures import Future
from pathlib import Path

from issueflow.benchmark import load_catalog
from issueflow.architectures.base import ArchitectureKind
from issueflow.models import RunRecord, RunStatus
from issueflow.ui import render_app


class StaticService:
    catalog = load_catalog(Path("benchmarks/catalogs/compatibility.yaml"))

    def start(self, case_id, budget, architecture):
        assert case_id == "historical-01"
        assert architecture is ArchitectureKind.FIXED
        assert budget.max_tool_calls == 18
        assert budget.max_patch_attempts == 4
        assert budget.max_seconds == 450
        return RunRecord(
            id="run-123",
            case_id=case_id,
            status=RunStatus.SUCCEEDED,
            stop_reason="functional_success",
            functional_success=True,
            review_status="approved",
            review_reasons=["Focused fix."],
        )


class StaticStore:
    def export_json(self, run_id):
        return {
            "run": {
                "id": run_id,
                "case_id": "historical-01",
                "architecture": "fixed",
                "status": "succeeded",
                "stop_reason": "functional_success",
                "functional_success": True,
                "review_status": "approved",
                "review_reasons": ["Focused fix."],
            },
            "steps": [
                {
                    "sequence": 1,
                    "role": "planner",
                    "step_type": "role",
                    "input_summary": "bounded workflow state",
                    "output_summary": "plan complete",
                    "status": "completed",
                    "duration_ms": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0.0,
                },
                {
                    "sequence": 2,
                    "role": "single_agent",
                    "step_type": "verification",
                    "input_summary": "python -c assertion",
                    "output_summary": "exit_code=0",
                    "status": "passed",
                    "duration_ms": 40,
                    "input_tokens": 180,
                    "output_tokens": 50,
                    "cost_usd": 0.00003,
                },
                {
                    "sequence": 3,
                    "role": "single_agent",
                    "step_type": "diff",
                    "input_summary": "git diff --binary HEAD",
                    "output_summary": "diff --git a/engine.py b/engine.py",
                    "status": "completed",
                    "duration_ms": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0.0,
                },
            ],
            "artifacts": [],
        }

    def export_json_text(self, run_id):
        return '{"run":{"id":"run-123"}}'


def immediate_submit(function, *args):
    future = Future()
    future.set_result(function(*args))
    return future


render_app(StaticStore(), StaticService(), submit=immediate_submit)
"""
    app = AppTest.from_string(script).run()

    assert any(
        caption.value
        == (
            "预算档位：medium · 工具 18 次 · 补丁 4 次 · 450 秒 · "
            "输入 50,000 Token · 输出 8,000 Token · 最高 $0.10"
        )
        for caption in app.caption
    )
    app.selectbox[1].select("Fixed Multi-Agent").run()
    app.button[0].click().run()

    assert not app.exception
    assert app.success[0].value == "功能验证通过"
    assert any(markdown.value == "**Agent 架构：** Fixed Multi-Agent" for markdown in app.markdown)
    assert any(markdown.value == "**角色调用：** planner × 1" for markdown in app.markdown)
    assert [(metric.label, metric.value) for metric in app.metric] == [
        ("总耗时", "0.04 秒"),
        ("工具调用", "0"),
        ("路由次数", "1"),
        ("输入 Token", "180"),
        ("输出 Token", "50"),
        ("估算成本", "$0.000030"),
    ]
    assert app.code[0].value == "diff --git a/engine.py b/engine.py"
    assert app.download_button[0].label == "下载 JSON 轨迹"


def test_workbench_explains_the_exact_exhausted_budget():
    script = """
from concurrent.futures import Future
from pathlib import Path

from issueflow.benchmark import load_catalog
from issueflow.models import RunRecord, RunStatus
from issueflow.ui import render_app


class StaticService:
    catalog = load_catalog(Path("benchmarks/catalogs/compatibility.yaml"))

    def start(self, case_id, budget, architecture):
        return RunRecord(
            id="run-budget",
            case_id=case_id,
            status=RunStatus.BUDGET_EXHAUSTED,
            stop_reason="patch_budget_exhausted",
            functional_success=False,
            review_status="skipped",
            review_reasons=["patch_budget_exhausted"],
        )


class StaticStore:
    def export_json(self, run_id):
        return {
            "run": {
                "id": run_id,
                "case_id": "historical-01",
                "status": "budget_exhausted",
                "stop_reason": "patch_budget_exhausted",
                "functional_success": False,
                "review_status": "skipped",
                "review_reasons": ["patch_budget_exhausted"],
            },
            "steps": [],
            "artifacts": [],
        }

    def export_json_text(self, run_id):
        return '{"run":{"id":"run-budget"}}'


def immediate_submit(function, *args):
    future = Future()
    future.set_result(function(*args))
    return future


render_app(StaticStore(), StaticService(), submit=immediate_submit)
"""
    app = AppTest.from_string(script).run()

    app.button[0].click().run()

    assert not app.exception
    assert app.error[0].value == "运行结束：预算已用尽"
    assert app.warning[0].value == "停止原因：补丁次数预算已用尽"


def test_runtime_wires_each_cases_registered_commands_into_its_agent(tmp_path):
    settings = Settings(
        api_key=SecretStr("test-key"),
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        temperature=1.25,
    )

    store, service = build_runtime(Path.cwd(), tmp_path / "data", settings)
    case = service.catalog["constructed-01"]
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    runner = service.architecture_factory(
        ArchitectureKind.SINGLE, case, workspace, service.sandbox_factory.for_case(case)
    )
    agent = runner.agent

    assert store.database_path == tmp_path / "data" / "issueflow.sqlite3"
    assert agent.model.test_commands == (case.reproduce_command,)
    assert agent.model.temperature == 1.25


def test_app_explains_how_to_configure_a_missing_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    script_path = Path(__file__).parents[1] / "src/issueflow/ui.py"
    app = AppTest.from_file(script_path).run()

    assert not app.exception
    assert app.warning[0].value == ("请先在终端设置 DEEPSEEK_API_KEY，然后重新启动工作台。")
