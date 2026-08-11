# IssueFlow 阶段一 MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个以固定版本 micrograd 为对象、可在 Docker 中受控运行、可通过 Streamlit 回放的单 Agent Issue 修复 MVP。

**Architecture:** Python 应用将 Benchmark、预算和运行状态建模为显式数据结构。宿主侧负责 DeepSeek 调用、轨迹持久化和 Streamlit 展示；每次任务在默认断网的 Docker 容器中获得独立仓库副本，Agent 只能通过受控工具读取、编辑和测试。SQLite 为运行索引的唯一事实来源，JSON 是可移植导出格式。

**Tech Stack:** Python 3.11、Streamlit、SQLite（标准库）、Pydantic、HTTPX、pytest、Docker、DeepSeek OpenAI-compatible API。

## Global Constraints

- Python 版本为 3.11 或更高。
- 首期仅支持 `https://github.com/karpathy/micrograd`，每个样本以 40 位 Git SHA 固定故障版本。
- Docker 默认使用 `--network none`，仅挂载该次任务的临时工作目录。
- API Key 仅从 `DEEPSEEK_API_KEY` 读取，不能写入 SQLite、JSON、日志、截图或 Git。
- MVP 只实现 `single_agent`；轨迹保留可扩展的 `role` 与 `step_type` 字段。
- 功能成功条件：复现失败、生成非空补丁、公开验证通过、未超预算；LLM Reviewer 仅作补充展示。
- 样本固定为 3 个 `historical` 和 2 个 `constructed`，必须在 UI 和报告中区分。

---

## File Structure

```text
issueflow/
├── pyproject.toml                    # 包、工具与测试配置
├── Makefile                          # 开发、验证和 Demo 命令
├── .env.example                      # 变量名与安全说明
├── docker/Dockerfile.micrograd       # ARM64 验证镜像
├── benchmarks/micrograd.yaml         # 5 个固定样本
├── scripts/verify_benchmarks.py      # 样本复现验证
├── src/issueflow/
│   ├── config.py                     # 环境配置与预算
│   ├── models.py                     # Pydantic 类型与状态枚举
│   ├── benchmark.py                  # 目录读取与工作区准备
│   ├── trace_store.py                # SQLite 与 JSON 导出
│   ├── sandbox.py                    # Docker 隔离执行
│   ├── agent.py                      # 模型客户端、受限工具、Agent 循环
│   ├── reviewer.py                   # 确定性与 LLM 审查
│   ├── run_service.py                # 一次运行的编排
│   └── ui.py                         # Streamlit 工作台
└── tests/
    ├── conftest.py                   # 假模型与临时数据库 fixtures
    ├── test_config.py
    ├── test_benchmark.py
    ├── test_trace_store.py
    ├── test_sandbox.py
    ├── test_agent.py
    ├── test_reviewer.py
    ├── test_run_service.py
    ├── test_ui.py
    └── test_e2e_smoke.py
```

### Task 1: 初始化工程、配置与核心模型

**Files:**
- Create: `pyproject.toml`, `Makefile`, `.env.example`
- Create: `src/issueflow/__init__.py`, `src/issueflow/config.py`, `src/issueflow/models.py`
- Create: `tests/conftest.py`, `tests/test_config.py`, `tests/test_models.py`

**Interfaces:**
- Consumes: `DEEPSEEK_API_KEY`、可选 `ISSUEFLOW_MODEL`、`ISSUEFLOW_DATA_DIR`。
- Produces: `Settings`、`Budget`、`RunStatus`、`BenchmarkCase`、`TraceStep`、`RunRecord`。

- [ ] **Step 1: 写失败测试**

```python
def test_settings_redacts_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-value")
    settings = Settings.from_env()
    assert settings.model == "deepseek-chat"
    assert "secret-value" not in settings.safe_dict().values()

def test_budget_rejects_zero_limit():
    with pytest.raises(ValidationError):
        Budget(max_tool_calls=0, max_patch_attempts=1, max_seconds=60,
               max_input_tokens=100, max_output_tokens=100, max_cost_usd=1.0)
```

- [ ] **Step 2: 确认测试失败**

Run: `pytest tests/test_config.py tests/test_models.py -q`

Expected: FAIL，缺少 `Settings` 与 `Budget`。

- [ ] **Step 3: 实现最小配置与模型**

```python
class Budget(BaseModel):
    max_tool_calls: PositiveInt
    max_patch_attempts: PositiveInt
    max_seconds: PositiveInt
    max_input_tokens: PositiveInt
    max_output_tokens: PositiveInt
    max_cost_usd: PositiveFloat

class Settings(BaseModel):
    api_key: SecretStr
    model: str = "deepseek-chat"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(api_key=SecretStr(os.environ["DEEPSEEK_API_KEY"]),
                   model=os.getenv("ISSUEFLOW_MODEL", "deepseek-chat"))

    def safe_dict(self) -> dict[str, str]:
        return {"model": self.model}
```

在 `pyproject.toml` 配置 `src` 布局、pytest、ruff、streamlit、pydantic 和 httpx；Makefile 提供 `test`、`lint`、`demo`、`docker-build`；`.env.example` 只列变量名。

- [ ] **Step 4: 验证**

Run: `pytest tests/test_config.py tests/test_models.py -q && ruff check src tests && ruff format --check src tests`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git init
git add pyproject.toml Makefile .env.example src/issueflow tests
git commit -m "chore: initialize IssueFlow MVP"
```

### Task 2: 建立可复现 micrograd Benchmark

**Files:**
- Create: `benchmarks/micrograd.yaml`, `scripts/verify_benchmarks.py`, `src/issueflow/benchmark.py`, `tests/test_benchmark.py`
- Modify: `src/issueflow/models.py`

**Interfaces:**
- Consumes: YAML 清单。
- Produces: `load_catalog(path: Path) -> dict[str, BenchmarkCase]`、`prepare_worktree(case: BenchmarkCase, destination: Path) -> Path`。

- [ ] **Step 1: 写失败测试**

```python
def test_catalog_requires_three_historical_and_two_constructed(tmp_path):
    path = tmp_path / "cases.yaml"
    path.write_text("cases: []", encoding="utf-8")
    with pytest.raises(ValueError, match="3 historical and 2 constructed"):
        load_catalog(path)

def test_case_requires_full_git_sha():
    with pytest.raises(ValidationError, match="revision"):
        BenchmarkCase(id="bad", kind="historical", revision="main",
                      issue="x", reproduce_command="pytest", verify_command="pytest")
```

- [ ] **Step 2: 确认测试失败**

Run: `pytest tests/test_benchmark.py -q`

Expected: FAIL，目录读取器尚未实现。

- [ ] **Step 3: 实现目录、版本固定和样本验证**

使用 40 位 SHA 正则验证 `revision`，解析 YAML。每个样本必须包含：`id`、`kind`、`repository_url`、`revision`、`license`、`issue`、`source_url`、`reproduce_command`、`verify_command`、`reference_patch`、`construction_notes`。

`verify_benchmarks.py` 对每个样本克隆仓库、检出故障 SHA、运行复现命令并要求非零退出；应用完整 `reference_patch` 后运行验证命令并要求零退出。清单中的样本 ID 固定为 `historical-01`、`historical-02`、`historical-03`、`constructed-01`、`constructed-02`。三个历史样本的来源必须是 Issue 或修复提交链接；两个自建样本必须明确注入的断言与构造理由。

- [ ] **Step 4: 验证目录和样本**

Run: `pytest tests/test_benchmark.py -q && python scripts/verify_benchmarks.py --catalog benchmarks/micrograd.yaml`

Expected: 5 个样本均为 `reproduction=failed_as_expected verification=passed`。

- [ ] **Step 5: 提交**

```bash
git add benchmarks scripts src/issueflow/benchmark.py tests/test_benchmark.py src/issueflow/models.py
git commit -m "feat: add reproducible micrograd benchmark catalog"
```

### Task 3: 实现 SQLite 轨迹与 JSON 导出

**Files:**
- Create: `src/issueflow/trace_store.py`, `tests/test_trace_store.py`
- Modify: `src/issueflow/models.py`

**Interfaces:**
- Consumes: `RunRecord`、`TraceStep`。
- Produces: `create_run(record)`、`append_step(run_id, step)`、`finish_run(run_id, status, stop_reason)`、`export_json(run_id) -> dict`。

- [ ] **Step 1: 写失败测试**

```python
def test_step_sequence_is_immutable(store, run):
    store.append_step(run.id, TraceStep(sequence=1, role="single_agent",
                                        step_type="tool", input_summary="search",
                                        output_summary="engine.py:1", status="ok"))
    with pytest.raises(sqlite3.IntegrityError):
        store.append_step(run.id, TraceStep(sequence=1, role="single_agent",
                                            step_type="tool", input_summary="read",
                                            output_summary="x", status="ok"))

def test_export_redacts_secret(store, run):
    store.append_step(run.id, TraceStep(sequence=1, role="single_agent",
                                        step_type="model", input_summary="key=secret-value",
                                        output_summary="done", status="ok"))
    assert "secret-value" not in json.dumps(store.export_json(run.id))
```

- [ ] **Step 2: 确认测试失败**

Run: `pytest tests/test_trace_store.py -q`

Expected: FAIL，缺少 `TraceStore`。

- [ ] **Step 3: 实现不可变 schema 和统一脱敏**

建立 `runs`、`trace_steps`、`artifacts`；将 `(run_id, sequence)` 设为主键并只使用 `INSERT` 写步骤。实现 `redact(text: str) -> str`，去除 `sk-` token、`Authorization: Bearer` 值和 `DEEPSEEK_API_KEY=` 值；所有数据库写入、JSON 导出和 UI 显示前调用它。JSON 顶层固定为 `{run, steps, artifacts}`。

- [ ] **Step 4: 验证**

Run: `pytest tests/test_trace_store.py -q && ruff check src/issueflow/trace_store.py tests/test_trace_store.py`

Expected: PASS；重复 sequence 被拒绝，导出不含密钥。

- [ ] **Step 5: 提交**

```bash
git add src/issueflow/models.py src/issueflow/trace_store.py tests/test_trace_store.py
git commit -m "feat: persist immutable run traces"
```

### Task 4: 构建默认断网 Docker 沙箱

**Files:**
- Create: `docker/Dockerfile.micrograd`, `src/issueflow/sandbox.py`, `tests/test_sandbox.py`
- Modify: `Makefile`

**Interfaces:**
- Consumes: 工作副本、命令和 `Budget`。
- Produces: `Sandbox.run(workspace, command, timeout_seconds) -> CommandResult`，结果含 `exit_code`、`stdout`、`stderr`、`duration_ms`、`timed_out`。

- [ ] **Step 1: 写失败测试**

```python
def test_docker_command_is_network_isolated(tmp_path):
    command = build_docker_command(tmp_path, "python -m pytest", timeout_seconds=60)
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command

def test_timeout_is_not_success(fake_subprocess, sandbox, tmp_path):
    fake_subprocess.timeout = True
    result = sandbox.run(tmp_path, "python -m pytest", timeout_seconds=1)
    assert result.timed_out is True
    assert result.exit_code == 124
```

- [ ] **Step 2: 确认测试失败**

Run: `pytest tests/test_sandbox.py -q`

Expected: FAIL，缺少 `Sandbox`。

- [ ] **Step 3: 实现镜像和严格执行参数**

Dockerfile 使用 Python 3.11 slim，预装 pytest、CPU PyTorch 和 micrograd 验证依赖，镜像不含 API Key。构造命令必须包含 `--network none --read-only --pids-limit 256 --cpus 2 --memory 4g --tmpfs /tmp:rw,noexec,nosuid,size=512m`；工作副本仅挂载到 `/workspace`，不挂载主目录。用 `subprocess.run(command, timeout=timeout_seconds, capture_output=True, text=True)` 执行，超时映射为 124。

- [ ] **Step 4: 验证**

Run: `pytest tests/test_sandbox.py -q && make docker-build && docker run --rm --network none issueflow-micrograd:dev python -m pytest`

Expected: PASS；镜像内测试通过，命令断网。

- [ ] **Step 5: 提交**

```bash
git add docker src/issueflow/sandbox.py tests/test_sandbox.py Makefile
git commit -m "feat: add network-isolated Docker sandbox"
```

### Task 5: 实现受限工具和单 Agent 循环

**Files:**
- Create: `src/issueflow/agent.py`, `tests/test_agent.py`
- Modify: `src/issueflow/models.py`

**Interfaces:**
- Consumes: `BenchmarkCase`、`Budget`、`Sandbox`、工作区和 DeepSeek 响应。
- Produces: `SingleAgent.run(case, workspace, budget) -> AgentResult`，含计划、补丁次数、停止原因、状态与 `TraceStep` 列表。

- [ ] **Step 1: 写失败测试**

```python
def test_agent_rejects_shell_tool(fake_model, agent, case, workspace):
    fake_model.responses = [{"tool": "shell", "arguments": {"command": "curl example.com"}}]
    result = agent.run(case, workspace, small_budget())
    assert result.status == "failed"
    assert result.stop_reason == "disallowed_tool:shell"

def test_agent_stops_at_tool_budget(fake_model, agent, case, workspace):
    fake_model.responses = [{"tool": "search", "arguments": {"query": "Value"}}] * 3
    result = agent.run(case, workspace, small_budget(max_tool_calls=2))
    assert result.stop_reason == "tool_budget_exhausted"
    assert len(result.steps) == 2
```

- [ ] **Step 2: 确认测试失败**

Run: `pytest tests/test_agent.py -q`

Expected: FAIL，缺少 Agent 和工具协议。

- [ ] **Step 3: 实现工具白名单和停止状态机**

仅公开 `search(query)`、`read_file(path, start_line, end_line)`、`apply_patch(patch)`、`run_tests(command)`。搜索只在工作区运行 `rg`；读取拒绝绝对路径与 `..`；补丁拒绝工作区外路径且递增修复次数；测试命令只能等于该样本的复现或验证命令，并由 `Sandbox` 执行。

HTTPX 调用 DeepSeek chat-completions 兼容端点。每一次模型请求摘要、工具调用和结果都生成 `TraceStep`。出现非允许工具、无效参数、超时、工具/补丁/token/时间/成本任一预算耗尽时，写入规范化 `stop_reason` 并停止。

- [ ] **Step 4: 验证**

Run: `pytest tests/test_agent.py -q && ruff check src/issueflow/agent.py tests/test_agent.py`

Expected: PASS；假模型可执行 `search → read_file → apply_patch → run_tests`，禁止工具不会被执行。

- [ ] **Step 5: 提交**

```bash
git add src/issueflow/agent.py src/issueflow/models.py tests/test_agent.py
git commit -m "feat: add budgeted single-agent repair loop"
```

### Task 6: 编排运行、成功判定与 Reviewer

**Files:**
- Create: `src/issueflow/reviewer.py`, `src/issueflow/run_service.py`
- Create: `tests/test_reviewer.py`, `tests/test_run_service.py`

**Interfaces:**
- Consumes: `AgentResult`、`CommandResult`、diff、`TraceStore`。
- Produces: `RunService.start(case_id, budget) -> RunRecord`、`ReviewResult(status, reasons)`。

- [ ] **Step 1: 写失败测试**

```python
def test_success_requires_reproduction_patch_and_verification(reviewer):
    result = reviewer.evaluate_deterministic(
        reproduction_exit_code=1, verification_exit_code=0,
        diff_text="diff --git a/micrograd/engine.py b/micrograd/engine.py",
        budget_exhausted=False)
    assert result.functional_success is True

def test_review_rejection_does_not_change_success(service, successful_agent):
    service.llm_reviewer = RejectingReviewer()
    run = service.execute(successful_agent)
    assert run.status == RunStatus.SUCCEEDED
    assert run.review_status == "needs_changes"
```

- [ ] **Step 2: 确认测试失败**

Run: `pytest tests/test_reviewer.py tests/test_run_service.py -q`

Expected: FAIL，缺少编排与审查逻辑。

- [ ] **Step 3: 实现状态转换和两层审查**

只允许 `queued → running → succeeded|failed|timed_out|budget_exhausted`。创建运行即持久化，异常也必须调用 `finish_run`。确定性审查要求复现退出非零、非空 diff、验证退出为零、未超预算。只有通过后才调用模型输出 JSON `{status, reasons}`；无效 JSON 记为 `review_status="failed"`，不覆盖功能成功状态。

- [ ] **Step 4: 验证**

Run: `pytest tests/test_reviewer.py tests/test_run_service.py -q`

Expected: PASS；所有终止状态均保存，Reviewer 不改变功能成功。

- [ ] **Step 5: 提交**

```bash
git add src/issueflow/reviewer.py src/issueflow/run_service.py tests/test_reviewer.py tests/test_run_service.py
git commit -m "feat: orchestrate runs and review repair evidence"
```

### Task 7: 构建 Streamlit 案例工作台

**Files:**
- Create: `src/issueflow/ui.py`, `tests/test_ui.py`
- Modify: `Makefile`

**Interfaces:**
- Consumes: `load_catalog`、`TraceStore.get_run`、`TraceStore.export_json`、`RunService.start`。
- Produces: `render_app(store, service) -> None`。

- [ ] **Step 1: 写失败测试**

```python
def test_case_view_distinguishes_origin():
    case = BenchmarkCase(
        id="c1", kind="constructed", repository_url="https://github.com/karpathy/micrograd",
        revision="a" * 40, license="MIT", issue="check negation gradient",
        source_url="https://github.com/karpathy/micrograd", reproduce_command="python -m pytest",
        verify_command="python -m pytest", reference_patch="patches/c1.patch",
        construction_notes="Adds one public regression assertion for unary negation.")
    view = make_case_view(case, None)
    assert view.origin_label == "自建边界样本"

def test_run_view_exposes_metrics(finished_run):
    view = make_run_view(finished_run)
    assert {"duration_ms", "tool_calls", "input_tokens", "output_tokens", "cost_usd"} <= view.metrics.keys()
```

- [ ] **Step 2: 确认测试失败**

Run: `pytest tests/test_ui.py -q`

Expected: FAIL，缺少视图适配函数。

- [ ] **Step 3: 实现首页与案例工作台**

首页下拉选择 5 个样本，明确显示“历史修复样本”或“自建边界样本”，按钮调用 `RunService.start`。按时间展示 Issue、Agent 计划、检索证据（文件和行号）、工具日志、diff、测试输出、Reviewer 结论和指标；提供 JSON 下载。运行中每两秒自动刷新，结束即停止。UI 只显示 TraceStore 的脱敏字段。

- [ ] **Step 4: 验证**

Run: `pytest tests/test_ui.py -q && streamlit run src/issueflow/ui.py --server.headless true`

Expected: PASS；可选择样本和查看/启动运行，页面无 API Key。

- [ ] **Step 5: 提交**

```bash
git add src/issueflow/ui.py tests/test_ui.py Makefile
git commit -m "feat: add Streamlit case workbench"
```

### Task 8: 端到端验收、复现说明与展示材料

**Files:**
- Create: `README.md`, `README.zh-CN.md`, `docs/phase-1-evaluation.md`, `docs/demo-script.md`, `tests/test_e2e_smoke.py`
- Modify: `Makefile`

**Interfaces:**
- Consumes: Docker 镜像、5 个验证样本、RunService、SQLite 与 JSON。
- Produces: 可复现启动说明、可回放运行记录和中英文演示材料。

- [ ] **Step 1: 写失败测试**

```python
def test_completed_run_has_replayable_artifacts(e2e_service):
    run = e2e_service.start("historical-01", e2e_budget())
    exported = e2e_service.trace_store.export_json(run.id)
    assert run.status in {RunStatus.SUCCEEDED, RunStatus.FAILED}
    assert exported["run"]["id"] == run.id
    assert exported["steps"]
    assert "DEEPSEEK_API_KEY" not in json.dumps(exported)
```

- [ ] **Step 2: 确认测试失败**

Run: `pytest tests/test_e2e_smoke.py -q`

Expected: FAIL，直至 Docker、样本、Agent 和存储完成集成。

- [ ] **Step 3: 编写 README、实验记录和演示脚本**

中英文 README 都必须写明 Apple Silicon 前置条件、环境变量设置、`make docker-build`、`make verify-benchmarks`、`make demo`、安全边界、样本来源/许可证及样本类型差异。实验记录分别报告历史/自建样本的复现、功能成功、耗时、工具调用、token、成本和 Reviewer 状态。演示脚本以三分钟顺序说明：选择案例、启动、查看证据、diff、测试、指标与局限。

- [ ] **Step 4: 全量验证**

Run: `make lint && make test && make docker-build && make verify-benchmarks && pytest tests/test_e2e_smoke.py -q`

Expected: 全部 PASS；5 个样本均能复现故障并通过参考补丁验证，至少一条真实 Agent 运行已保存在 SQLite/JSON 中。

- [ ] **Step 5: 提交**

```bash
git add README.md README.zh-CN.md docs tests/test_e2e_smoke.py Makefile
git commit -m "docs: document reproducible IssueFlow phase one MVP"
```

## Plan Self-Review

- **Spec coverage:** Task 1 定义配置与预算；Task 2 覆盖样本、来源、许可证和固定版本；Task 3 覆盖 SQLite/JSON 轨迹；Task 4 覆盖默认断网 Docker；Task 5 覆盖 DeepSeek 单 Agent 受限工具；Task 6 覆盖成功判定与审查；Task 7 交付 Streamlit Demo；Task 8 覆盖复现、双语材料与演示。
- **Scope:** 未引入多 Agent、Supervisor、任意仓库、自动 PR、隐藏测试、异步服务或分布式执行器。
- **Consistency:** 运行入口统一使用 `BenchmarkCase`、`Budget`、`TraceStore`、`Sandbox` 与 `RunService`；成功判定只在 Task 6 定义，后续任务只消费该结果。
