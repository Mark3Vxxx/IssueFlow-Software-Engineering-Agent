from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from pydantic import SecretStr
from streamlit.testing.v1 import AppTest

from issueflow.config import Settings
from issueflow.models import BenchmarkCase, Budget, RunRecord, RunStatus
from issueflow.ui import RunSession, build_runtime, make_case_view, make_run_view


def test_case_view_distinguishes_constructed_samples_from_historical_repairs():
    case = BenchmarkCase(
        id="constructed-01",
        kind="constructed",
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
        kind="historical",
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
        kind="constructed",
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
            "status": "succeeded",
            "stop_reason": "functional_success",
            "functional_success": True,
            "review_status": "approved",
            "review_reasons": ["Focused fix."],
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

    assert view.metrics == {
        "duration_ms": 40,
        "tool_calls": 2,
        "input_tokens": 180,
        "output_tokens": 50,
        "cost_usd": 0.00003,
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
        def start(self, case_id: str, budget: Budget) -> RunRecord:
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
        def start(self, case_id: str, budget: Budget) -> RunRecord:
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
    catalog = load_catalog(Path("benchmarks/micrograd.yaml"))


class EmptyStore:
    pass


render_app(EmptyStore(), StaticService())
"""

    app = AppTest.from_string(script).run()

    assert not app.exception
    assert app.title[0].value == "IssueFlow 单 Agent 修复工作台"
    assert len(app.selectbox[0].options) == 5
    assert app.selectbox[0].options[0] == "historical-01 · 历史修复样本"
    assert app.selectbox[0].options[1] == "constructed-01 · 自建边界样本"
    assert app.button[0].label == "开始真实修复"


def test_workbench_runs_selected_case_and_renders_persisted_evidence():
    script = """
from concurrent.futures import Future
from pathlib import Path

from issueflow.benchmark import load_catalog
from issueflow.models import RunRecord, RunStatus
from issueflow.ui import render_app


class StaticService:
    catalog = load_catalog(Path("benchmarks/micrograd.yaml"))

    def start(self, case_id, budget):
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
                "status": "succeeded",
                "stop_reason": "functional_success",
                "functional_success": True,
                "review_status": "approved",
                "review_reasons": ["Focused fix."],
            },
            "steps": [
                {
                    "sequence": 1,
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

    def export_json_text(self, run_id):
        return '{"run":{"id":"run-123"}}'


def immediate_submit(function, *args):
    future = Future()
    future.set_result(function(*args))
    return future


render_app(StaticStore(), StaticService(), submit=immediate_submit)
"""
    app = AppTest.from_string(script).run()

    app.button[0].click().run()

    assert not app.exception
    assert app.success[0].value == "功能验证通过"
    assert [(metric.label, metric.value) for metric in app.metric] == [
        ("总耗时", "0.04 秒"),
        ("工具调用", "0"),
        ("输入 Token", "180"),
        ("输出 Token", "50"),
        ("估算成本", "$0.000030"),
    ]
    assert app.code[0].value == "diff --git a/engine.py b/engine.py"
    assert app.download_button[0].label == "下载 JSON 轨迹"


def test_runtime_wires_each_cases_registered_commands_into_its_agent(tmp_path):
    settings = Settings(
        api_key=SecretStr("test-key"),
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
    )

    store, service = build_runtime(Path.cwd(), tmp_path / "data", settings)
    case = service.catalog["constructed-01"]
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    agent = service.agent_factory(case, workspace)

    assert store.database_path == tmp_path / "data" / "issueflow.sqlite3"
    assert agent.model.test_commands == (case.reproduce_command,)


def test_app_explains_how_to_configure_a_missing_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    script_path = Path(__file__).parents[1] / "src/issueflow/ui.py"
    app = AppTest.from_file(script_path).run()

    assert not app.exception
    assert app.warning[0].value == ("请先在终端设置 DEEPSEEK_API_KEY，然后重新启动工作台。")
