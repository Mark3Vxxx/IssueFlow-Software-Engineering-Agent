"""Streamlit view models and case workbench."""

import os
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol

import streamlit as st

from issueflow.agent import DeepSeekModelClient, SingleAgent, ToolExecutor
from issueflow.benchmark import load_catalog
from issueflow.config import Settings
from issueflow.models import BenchmarkCase, Budget, RunRecord
from issueflow.reviewer import DeepSeekReviewClient, Reviewer
from issueflow.run_service import GitWorkspacePreparer, RunService
from issueflow.sandbox import DockerSandbox
from issueflow.trace_store import TraceStore, redact

STATUS_LABELS = {
    "queued": "排队中",
    "running": "运行中",
    "succeeded": "成功",
    "failed": "失败",
    "timed_out": "已超时",
    "budget_exhausted": "预算已用尽",
}

DEFAULT_BUDGET = Budget(
    max_tool_calls=12,
    max_patch_attempts=2,
    max_seconds=300,
    max_input_tokens=30_000,
    max_output_tokens=6_000,
    max_cost_usd=0.05,
)


class RunStarter(Protocol):
    """Service boundary used by the asynchronous UI session."""

    def start(self, case_id: str, budget: Budget) -> RunRecord: ...


SubmitRun = Callable[..., Future[RunRecord]]


class RunSession:
    """Keep at most one benchmark future attached to a browser session."""

    def __init__(self, service: RunStarter, submit: SubmitRun) -> None:
        self.service = service
        self.submit = submit
        self._future: Future[RunRecord] | None = None

    @property
    def is_running(self) -> bool:
        """Whether a submitted benchmark still occupies this browser session."""
        return self._future is not None

    def start(self, case_id: str, budget: Budget) -> None:
        """Submit one run and reject accidental parallel starts."""
        if self._future is not None:
            raise RuntimeError("a benchmark is already running")
        self._future = self.submit(self.service.start, case_id, budget)

    def poll(self) -> RunRecord | None:
        """Return and clear a completed result without blocking the page."""
        if self._future is None or not self._future.done():
            return None
        future = self._future
        self._future = None
        return future.result()


@dataclass(frozen=True)
class CaseView:
    """User-facing metadata for one benchmark case."""

    id: str
    issue: str
    origin_label: str
    latest_status_label: str


@dataclass(frozen=True)
class TimelineItem:
    """One redacted event rendered in chronological order."""

    sequence: int
    title: str
    step_type: str
    status: str
    input_summary: str
    output_summary: str
    duration_ms: int


@dataclass(frozen=True)
class RunView:
    """Display-ready summary of one persisted run."""

    status_label: str
    functional_success: bool | None
    stop_reason: str
    review_status: str
    review_reasons: tuple[str, ...]
    metrics: dict[str, int | float]
    timeline: tuple[TimelineItem, ...]
    diff_text: str


def make_case_view(case: BenchmarkCase, latest_run: RunRecord | None) -> CaseView:
    """Translate domain metadata without blurring sample provenance."""
    origin_label = "历史修复样本" if case.kind == "historical" else "自建边界样本"
    latest_status_label = (
        "尚未运行" if latest_run is None else STATUS_LABELS[latest_run.status.value]
    )
    return CaseView(
        id=case.id,
        issue=case.issue,
        origin_label=origin_label,
        latest_status_label=latest_status_label,
    )


def make_run_view(trace: dict[str, object]) -> RunView:
    """Translate immutable trace evidence into a redacted workbench view."""
    run = trace.get("run")
    if not isinstance(run, dict):
        raise TypeError("trace run must be an object")
    steps = trace.get("steps", [])
    if not isinstance(steps, list):
        raise TypeError("trace steps must be a list")
    timeline = tuple(
        TimelineItem(
            sequence=int(step["sequence"]),
            title=_step_title(str(step["step_type"]), str(step["input_summary"])),
            step_type=str(step["step_type"]),
            status=str(step["status"]),
            input_summary=redact(str(step["input_summary"])),
            output_summary=redact(str(step["output_summary"])),
            duration_ms=int(step["duration_ms"]),
        )
        for step in steps
    )
    diff_text = next(
        (item.output_summary for item in reversed(timeline) if item.step_type == "diff"),
        "",
    )
    review_reasons = run.get("review_reasons", [])
    if not isinstance(review_reasons, list):
        raise TypeError("review reasons must be a list")
    return RunView(
        status_label=STATUS_LABELS.get(str(run.get("status")), str(run.get("status"))),
        functional_success=run.get("functional_success"),
        stop_reason=redact(str(run.get("stop_reason") or "")),
        review_status=str(run.get("review_status") or "未审查"),
        review_reasons=tuple(redact(str(reason)) for reason in review_reasons),
        metrics={
            "duration_ms": sum(int(step["duration_ms"]) for step in steps),
            "tool_calls": sum(step["step_type"] == "tool" for step in steps),
            "input_tokens": sum(int(step["input_tokens"]) for step in steps),
            "output_tokens": sum(int(step["output_tokens"]) for step in steps),
            "cost_usd": float(sum(Decimal(str(step["cost_usd"])) for step in steps)),
        },
        timeline=timeline,
        diff_text=diff_text,
    )


def _step_title(step_type: str, input_summary: str) -> str:
    """Give raw trace events concise, stable Chinese labels."""
    if step_type == "tool":
        tool_name = input_summary.partition(":")[0]
        return {
            "search": "检索代码",
            "read_file": "读取文件",
            "apply_patch": "应用补丁",
            "run_tests": "运行测试",
        }.get(tool_name, "Agent 工具")
    return {
        "reproduction": "故障复现",
        "verification": "独立验证",
        "diff": "代码改动",
        "review": "Reviewer 审查",
        "model": "Agent 结论",
    }.get(step_type, step_type)


@st.cache_resource(show_spinner=False)
def _run_executor() -> ThreadPoolExecutor:
    """Keep one background worker alive for the Streamlit process."""
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix="issueflow-run")


def render_app(
    store: object,
    service: object,
    *,
    submit: SubmitRun | None = None,
) -> None:
    """Render the case selector, asynchronous run state, and persisted evidence."""
    catalog = service.catalog
    latest_run = st.session_state.get("issueflow_latest_run")
    case_views = {
        f"{case.id} · {make_case_view(case, None).origin_label}": make_case_view(
            case,
            latest_run if latest_run is not None and latest_run.case_id == case.id else None,
        )
        for case in catalog.values()
    }
    if "issueflow_run_session" not in st.session_state:
        st.session_state.issueflow_run_session = RunSession(
            service,
            submit or _run_executor().submit,
        )
    run_session = st.session_state.issueflow_run_session

    st.title("IssueFlow 单 Agent 修复工作台")
    st.caption("选择一个固定 Benchmark，观察 Agent 如何复现、定位、修改并独立验证。")
    selected_label = st.selectbox("选择案例", list(case_views))
    selected = case_views[selected_label]

    st.subheader(selected.id)
    st.markdown(f"**样本来源：** {selected.origin_label}")
    st.markdown(f"**Issue：** {selected.issue}")
    st.markdown(f"**最近状态：** {selected.latest_status_label}")
    if st.button("开始真实修复", type="primary", disabled=run_session.is_running):
        st.session_state.pop("issueflow_run_id", None)
        st.session_state.pop("issueflow_run_error", None)
        run_session.start(selected.id, DEFAULT_BUDGET)

    @st.fragment(run_every=2 if run_session.is_running else None)
    def render_run_panel() -> None:
        try:
            result = run_session.poll()
        except Exception as error:  # noqa: BLE001 - page must report background failures safely.
            st.session_state.issueflow_run_error = type(error).__name__
            st.error(f"运行未完成：{type(error).__name__}")
            return
        if result is not None:
            st.session_state.issueflow_run_id = result.id
            st.session_state.issueflow_latest_run = result
            st.rerun(scope="app")
        if run_session.is_running:
            st.status("Benchmark 正在运行", expanded=True, state="running")
            st.caption("页面每 2 秒检查一次结果；结束后会自动停止刷新。")
            return
        run_id = st.session_state.get("issueflow_run_id")
        if run_id is not None:
            _render_finished_run(store, run_id)

    render_run_panel()


def _render_finished_run(store: object, run_id: str) -> None:
    """Render one completed run strictly from persisted, redacted evidence."""
    view = make_run_view(store.export_json(run_id))
    if view.functional_success:
        st.success("功能验证通过")
    else:
        st.error(f"运行结束：{view.status_label}")

    duration_seconds = float(view.metrics["duration_ms"]) / 1_000
    columns = st.columns(5)
    columns[0].metric("总耗时", f"{duration_seconds:.2f} 秒")
    columns[1].metric("工具调用", str(view.metrics["tool_calls"]))
    columns[2].metric("输入 Token", str(view.metrics["input_tokens"]))
    columns[3].metric("输出 Token", str(view.metrics["output_tokens"]))
    columns[4].metric("估算成本", f"${float(view.metrics['cost_usd']):.6f}")

    st.subheader("代码改动")
    if view.diff_text:
        st.code(view.diff_text, language="diff")
    else:
        st.info("本次运行没有产生代码改动。")

    st.subheader("Reviewer 结论")
    st.markdown(f"**状态：** {view.review_status}")
    for reason in view.review_reasons:
        st.markdown(f"- {reason}")

    st.subheader("运行时间线")
    for item in view.timeline:
        with st.expander(f"{item.sequence}. {item.title} · {item.status}"):
            st.caption(f"输入：{item.input_summary}")
            if item.output_summary:
                st.text(item.output_summary)
            st.caption(f"耗时：{item.duration_ms} ms")

    st.download_button(
        "下载 JSON 轨迹",
        data=store.export_json_text(run_id),
        file_name=f"{run_id}.json",
        mime="application/json",
    )


def build_runtime(
    project_root: Path,
    data_root: Path,
    settings: Settings,
) -> tuple[TraceStore, RunService]:
    """Wire the production workbench without exposing credentials to the UI."""
    data_root.mkdir(parents=True, exist_ok=True)
    catalog = load_catalog(project_root / "benchmarks/micrograd.yaml")
    store = TraceStore(data_root / "issueflow.sqlite3")
    sandbox = DockerSandbox()
    api_key = settings.api_key.get_secret_value()

    def agent_factory(case: BenchmarkCase, workspace: Path) -> SingleAgent:
        commands = tuple(dict.fromkeys((case.reproduce_command, case.verify_command)))
        model = DeepSeekModelClient(
            api_key=api_key,
            model=settings.model,
            base_url=settings.base_url,
            test_commands=commands,
        )
        return SingleAgent(model, ToolExecutor(workspace, case, sandbox))

    service = RunService(
        catalog=catalog,
        store=store,
        workspace_preparer=GitWorkspacePreparer(
            data_root / "workspaces",
            project_root / "benchmarks",
        ),
        sandbox=sandbox,
        agent_factory=agent_factory,
        reviewer=Reviewer(
            DeepSeekReviewClient(
                api_key=api_key,
                model=settings.model,
                base_url=settings.base_url,
            )
        ),
    )
    return store, service


def main() -> None:
    """Start the configured Streamlit application."""
    st.set_page_config(page_title="IssueFlow", page_icon="🧭", layout="wide")
    project_root = Path(__file__).resolve().parents[2]
    configured_data_root = Path(os.getenv("ISSUEFLOW_DATA_DIR", ".issueflow"))
    data_root = (
        configured_data_root
        if configured_data_root.is_absolute()
        else project_root / configured_data_root
    )
    try:
        settings = Settings.from_env()
    except KeyError:
        st.title("IssueFlow 单 Agent 修复工作台")
        st.warning("请先在终端设置 DEEPSEEK_API_KEY，然后重新启动工作台。")
        return
    store, service = build_runtime(project_root, data_root, settings)
    render_app(store, service)


if __name__ == "__main__":
    main()
