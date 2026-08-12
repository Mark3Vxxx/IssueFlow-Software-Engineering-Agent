# IssueFlow Phase 2A Agent Architectures Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run Direct, phase-one Single, fixed four-role, and dynamic Supervisor architectures through one deterministic `RunService` contract with comparable budgets and role-level traces.

**Architecture:** Add an `architectures` package around a shared result contract. Direct and Single are adapters; Fixed and Dynamic are LangGraph `StateGraph` workflows whose injected role callables make routing deterministic in tests. `RunService` remains responsible for reproduction, independent verification, diff, functional success, and persistence.

**Tech Stack:** Python 3.11+, Pydantic 2, `langgraph>=1.2,<2`, HTTPX, SQLite, Streamlit, pytest, Ruff, Docker.

## Global Constraints

- Phase 2A uses only the five phase-one micrograd cases; they remain compatibility cases and do not count toward the new strict set.
- Architecture names are exactly `direct`, `single`, `fixed`, and `dynamic`.
- Role names are exactly `direct`, `single_agent`, `planner`, `retriever`, `coder`, `reviewer`, and `supervisor`.
- All four architectures receive the same case-level `Budget`; every model call, tool call, patch, token, second, and estimated cost counts toward it.
- Fixed may perform at most one Reviewer-requested Coder rework in 2A.
- Dynamic may perform at most 12 Supervisor routes and may invoke Reviewer at most twice.
- A Supervisor cannot change budgets, execute tools, mark functional success, or bypass `RunService` verification.
- LangGraph uses `run_id` as `thread_id` and an in-memory checkpointer in phase 2A; cross-process recovery is out of scope.
- `TraceStore`, not LangGraph checkpoints, is authoritative for result, metrics, and exports.
- Preserve the phase-one public API where practical: `RunService.start(case_id, budget)` defaults to `single`.
- Preserve the current Docker, credential, tool allowlist, redaction, and deterministic success behavior.

---

### Task 1: Add LangGraph without changing phase-one behavior

**Files:**
- Modify: `pyproject.toml`
- Modify: `Makefile`
- Create: `tests/phase2/test_langgraph_runtime.py`
- Create: `tests/phase2/__init__.py`

**Interfaces:**
- Consumes: Existing Python 3.11+ package and `make verify-phase-1`.
- Produces: Importable LangGraph 1.2.x runtime and `make test-phase-2`.

- [ ] **Step 1: Write the failing dependency smoke test**

Create `tests/phase2/test_langgraph_runtime.py`:

```python
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph


def test_langgraph_state_graph_runs_with_a_thread_id():
    graph = StateGraph(dict)
    graph.add_node("finish", lambda state: {**state, "visited": True})
    graph.add_edge(START, "finish")
    graph.add_edge("finish", END)
    compiled = graph.compile(checkpointer=InMemorySaver())

    result = compiled.invoke(
        {"visited": False},
        {"configurable": {"thread_id": "run-test"}},
    )

    assert result["visited"] is True
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/phase2/test_langgraph_runtime.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'langgraph'`.

- [ ] **Step 3: Add the bounded dependency and phase-two test target**

Add to project dependencies:

```toml
"langgraph>=1.2,<2",
```

Add to `Makefile`:

```make
.PHONY: test-phase-2

test-phase-2:
	$(PYTHON) -m pytest tests/phase2 -q
```

Install with `.venv/bin/python -m pip install -e '.[dev]'`.

- [ ] **Step 4: Verify GREEN and phase-one compatibility**

Run:

```bash
.venv/bin/python -m pytest tests/phase2/test_langgraph_runtime.py -q
make test
```

Expected: LangGraph smoke PASS and all existing tests PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml Makefile tests/phase2
git commit -m "build: add bounded langgraph runtime"
```

---

### Task 2: Define the common architecture contract

**Files:**
- Create: `src/issueflow/architectures/__init__.py`
- Create: `src/issueflow/architectures/base.py`
- Create: `src/issueflow/architectures/single.py`
- Modify: `src/issueflow/models.py`
- Create: `tests/phase2/test_architecture_contract.py`

**Interfaces:**
- Consumes: `BenchmarkCase`, `Budget`, `TraceStep`, `AgentResult`, and `SingleAgent.run`.
- Produces: `ArchitectureKind`, `RoleName`, `Usage`, `RunContext`, `ArchitectureResult`, `ArchitectureRunner`, and `SingleArchitecture`.

- [ ] **Step 1: Write contract and Single-adapter tests**

Use these exact assertions:

```python
def test_architecture_kinds_are_the_four_experiment_arms():
    assert [item.value for item in ArchitectureKind] == [
        "direct", "single", "fixed", "dynamic"
    ]


def test_single_adapter_preserves_terminal_result(case, workspace, budget):
    result = SingleArchitecture(ScriptedSingle()).run(
        case, workspace, budget, RunContext(run_id="run-1")
    )

    assert result.architecture is ArchitectureKind.SINGLE
    assert result.status is RunStatus.SUCCEEDED
    assert result.role_usage[RoleName.SINGLE_AGENT].tool_calls == 2
    assert result.steps[0].role == "single_agent"
```

The scripted single runner returns an existing `AgentResult` with two tool calls, one patch, and one `TraceStep`.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/phase2/test_architecture_contract.py -q`

Expected: FAIL because `issueflow.architectures` does not exist.

- [ ] **Step 3: Implement the models and protocol**

Add `Usage` to `models.py` so Agent, Reviewer, architectures, persistence, and experiment code share one dependency-neutral metric type. `base.py` exposes the remaining signatures:

```python
class ArchitectureKind(StrEnum):
    DIRECT = "direct"
    SINGLE = "single"
    FIXED = "fixed"
    DYNAMIC = "dynamic"


class RoleName(StrEnum):
    DIRECT = "direct"
    SINGLE_AGENT = "single_agent"
    PLANNER = "planner"
    RETRIEVER = "retriever"
    CODER = "coder"
    REVIEWER = "reviewer"
    SUPERVISOR = "supervisor"


class RunContext(BaseModel):
    run_id: str


class ArchitectureResult(BaseModel):
    architecture: ArchitectureKind
    status: RunStatus
    stop_reason: str
    steps: list[TraceStep] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    role_usage: dict[RoleName, Usage] = Field(default_factory=dict)
    route_count: NonNegativeInt = 0
    final_message: str = ""


class ArchitectureRunner(Protocol):
    def run(
        self,
        case: BenchmarkCase,
        workspace: Path,
        budget: Budget,
        context: RunContext,
    ) -> ArchitectureResult: ...
```

The shared model is:

```python
class Usage(BaseModel):
    model_calls: NonNegativeInt = 0
    tool_calls: NonNegativeInt = 0
    patch_attempts: NonNegativeInt = 0
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    cost_usd: NonNegativeFloat = 0.0
    duration_ms: NonNegativeInt = 0
```

`SingleArchitecture` delegates once to `SingleAgent.run`, copies steps/status/reason, converts its counters to `Usage`, and assigns all usage to `RoleName.SINGLE_AGENT`.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/bin/python -m pytest tests/phase2/test_architecture_contract.py tests/test_agent.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/issueflow/models.py src/issueflow/architectures tests/phase2/test_architecture_contract.py
git commit -m "feat: define comparable architecture contract"
```

---

### Task 3: Add a schema-validated structured model boundary

**Files:**
- Create: `src/issueflow/structured_model.py`
- Create: `tests/phase2/test_structured_model.py`
- Modify: `src/issueflow/agent.py`
- Modify: `src/issueflow/config.py`
- Modify: `tests/test_deepseek.py`
- Modify: `tests/test_config.py`

**Interfaces:**
- Consumes: DeepSeek's OpenAI-compatible `/chat/completions` endpoint and existing price table.
- Produces: `StructuredCompletion[T]` and `StructuredModel.complete(system, payload, schema) -> StructuredCompletion[T]`.

- [ ] **Step 1: Write tests for schema validation, usage, and redacted errors**

```python
class PlanAnswer(BaseModel):
    summary: str
    steps: list[str]


def test_structured_model_validates_json_and_returns_usage(fake_http):
    client = DeepSeekStructuredModel("secret", "deepseek-v4-flash", "https://example", fake_http)

    result = client.complete("plan", {"issue": "bug"}, PlanAnswer)

    assert result.value == PlanAnswer(summary="fix gradient", steps=["inspect", "test"])
    assert result.usage.input_tokens == 120
    assert result.usage.output_tokens == 30


def test_structured_model_rejects_invalid_schema(fake_http_invalid):
    with pytest.raises(ModelProtocolError, match="invalid_structured_response") as caught:
        DeepSeekStructuredModel(...).complete("plan", {"issue": "bug"}, PlanAnswer)
    assert caught.value.usage.input_tokens == 120
```

The fake response content is `{"summary":"fix gradient","steps":["inspect","test"]}` and includes deterministic usage fields.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/phase2/test_structured_model.py -q`

Expected: FAIL because the structured boundary is absent.

- [ ] **Step 3: Implement the generic boundary**

```python
T = TypeVar("T", bound=BaseModel)


class StructuredCompletion(BaseModel, Generic[T]):
    value: T
    usage: Usage


class StructuredModel(Protocol):
    def complete(
        self,
        system_prompt: str,
        payload: dict[str, object],
        schema: type[T],
    ) -> StructuredCompletion[T]: ...
```

`ModelProtocolError` stores normalized text plus a `Usage` object so invalid JSON/schema responses still count consumed tokens and cost. `DeepSeekStructuredModel` posts one non-streaming request with `response_format={"type":"json_object"}`, validates `message.content` through `schema.model_validate_json`, calculates cost with the same price helper used by `DeepSeekModelClient`, and raises normalized `ModelProtocolError` without including raw content or Authorization headers. Both DeepSeek clients accept `temperature: float` and include it in every request; `Settings` reads `ISSUEFLOW_TEMPERATURE`, defaults to `0.0`, and validates the inclusive range 0–2.

Move the price table and estimator from `agent.py` into `structured_model.py` only if both clients import the same pure `estimate_cost(model, usage) -> float`; do not maintain two price tables.

- [ ] **Step 4: Verify GREEN and existing DeepSeek behavior**

Run: `.venv/bin/python -m pytest tests/phase2/test_structured_model.py tests/test_deepseek.py tests/test_config.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/issueflow/structured_model.py src/issueflow/agent.py src/issueflow/config.py tests/phase2/test_structured_model.py tests/test_deepseek.py tests/test_config.py
git commit -m "feat: add structured model boundary"
```

---

### Task 4: Implement the non-iterative Direct baseline

**Files:**
- Create: `src/issueflow/architectures/direct.py`
- Create: `tests/phase2/test_direct_architecture.py`

**Interfaces:**
- Consumes: `StructuredModel`, `ToolExecutor`, `ArchitectureResult`, and one case-level `Budget`.
- Produces: `DirectPatch(path: str, old_text: str, new_text: str, explanation: str)` and `DirectArchitecture.run(...)`.

- [ ] **Step 1: Write Direct success and failure tests**

```python
def test_direct_uses_one_model_call_and_one_patch(case, workspace, budget):
    result = DirectArchitecture(model=StubStructuredModel(), tools=tools).run(
        case, workspace, budget, RunContext(run_id="run-direct")
    )

    assert result.status is RunStatus.SUCCEEDED
    assert result.usage.model_calls == 1
    assert result.usage.patch_attempts == 1
    assert [step.role for step in result.steps] == ["direct", "direct"]


def test_direct_does_not_retry_an_invalid_patch(...):
    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "patch_application_failure"
    assert result.usage.model_calls == 1
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/phase2/test_direct_architecture.py -q`

Expected: FAIL because Direct is absent.

- [ ] **Step 3: Implement one-shot Direct**

Direct sends the Issue plus a bounded repository map produced by a deterministic host-side scan: at most 120 relative paths and at most 400 characters per file preview for files matching `.py`, `.toml`, `.yaml`, and `.md`; total payload is capped at 20,000 characters. It requests exactly one `DirectPatch`, checks token/cost/time limits, applies the exact structured replacement through `ToolExecutor`, and returns without running Agent-side tests. `RunService` performs the independent test later.

Direct must return normalized reasons: `input_token_budget_exhausted`, `output_token_budget_exhausted`, `cost_budget_exhausted`, `time_budget_exhausted`, `model_protocol_failure`, or `patch_application_failure`.

- [ ] **Step 4: Verify GREEN**

Run: `.venv/bin/python -m pytest tests/phase2/test_direct_architecture.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/issueflow/architectures/direct.py tests/phase2/test_direct_architecture.py
git commit -m "feat: add direct repair baseline"
```

---

### Task 5: Implement reusable role nodes and the Fixed graph

**Files:**
- Create: `src/issueflow/architectures/state.py`
- Create: `src/issueflow/architectures/roles.py`
- Create: `src/issueflow/architectures/fixed.py`
- Create: `tests/phase2/test_fixed_architecture.py`

**Interfaces:**
- Consumes: `StructuredModel`, `ToolExecutor`, architecture contract, and LangGraph.
- Produces: `PlanOutput`, `EvidenceItem`, `EvidenceBundle`, `CoderOutput`, `ReviewOutput`, `WorkflowState`, injectable `RoleSet`, and `FixedMultiAgentArchitecture`.

- [ ] **Step 1: Write graph-order and one-rework tests**

```python
def test_fixed_visits_roles_in_order_once(fixed_with_approved_review, ...):
    result = fixed_with_approved_review.run(...)

    assert [step.role for step in result.steps if step.step_type == "role"] == [
        "planner", "retriever", "coder", "reviewer"
    ]
    assert result.route_count == 4


def test_fixed_allows_exactly_one_coder_rework(fixed_with_two_change_requests, ...):
    result = fixed_with_two_change_requests.run(...)

    assert result.stop_reason == "review_loop_exhausted"
    assert [step.role for step in result.steps if step.step_type == "role"] == [
        "planner", "retriever", "coder", "reviewer", "coder", "reviewer"
    ]
```

Also test that Planner cannot call tools, Retriever accepts only `search/read_file`, Coder accepts only `read_file/apply_patch/run_tests`, and Reviewer cannot apply patches.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/phase2/test_fixed_architecture.py -q`

Expected: FAIL because state, roles, and graph are absent.

- [ ] **Step 3: Implement bounded schemas and state**

Use a `TypedDict` state with these keys and no unbounded message list:

```python
class WorkflowState(TypedDict):
    case_id: str
    issue: str
    plan: PlanOutput | None
    evidence: list[EvidenceItem]
    current_diff: str
    public_test_result: str
    review_feedback: ReviewOutput | None
    usage: Usage
    role_usage: dict[RoleName, Usage]
    role_history: list[RoleName]
    rework_count: int
    route_count: int
    stop_reason: str | None
```

Enforce: at most 6 plan steps, 20 evidence items, 2,000 characters per evidence summary, 20,000 characters of diff in state, and 50 role-history entries.

- [ ] **Step 4: Implement role boundaries**

`RoleSet` is a dataclass of four injected callables with exact methods `plan(state)`, `retrieve(state)`, `code(state)`, and `review(state)`. Production callables use structured responses and `ToolExecutor`; tests inject deterministic callables. Each node returns only a partial state update and one `TraceStep(step_type="role")` with its exact role.

- [ ] **Step 5: Build and run the Fixed graph**

Graph topology:

```text
START -> planner -> retriever -> coder -> reviewer
reviewer -- approved --> END
reviewer -- needs_changes and rework_count == 0 --> coder
reviewer -- otherwise --> END with review_loop_exhausted/failed
```

Compile with `InMemorySaver`, invoke with `{"configurable": {"thread_id": context.run_id}, "recursion_limit": 12}`, and convert `GraphRecursionError` to `review_loop_exhausted`.

- [ ] **Step 6: Verify GREEN and budget failures**

Run: `.venv/bin/python -m pytest tests/phase2/test_fixed_architecture.py -q`

Expected: PASS, including tool/token/cost/time exhaustion before the next role starts.

- [ ] **Step 7: Commit**

```bash
git add src/issueflow/architectures/state.py src/issueflow/architectures/roles.py src/issueflow/architectures/fixed.py tests/phase2/test_fixed_architecture.py
git commit -m "feat: add fixed four-role workflow"
```

---

### Task 6: Implement the Dynamic Supervisor graph

**Files:**
- Create: `src/issueflow/architectures/dynamic.py`
- Create: `tests/phase2/test_dynamic_architecture.py`

**Interfaces:**
- Consumes: `WorkflowState`, `RoleSet`, `StructuredModel`, and the same budget guard used by Fixed.
- Produces: `SupervisorDecision(next_role, reason)` and `DynamicSupervisorArchitecture`.

- [ ] **Step 1: Write legal routing, invalid routing, and route-cap tests**

```python
def test_dynamic_records_each_supervisor_decision(dynamic_scripted, ...):
    result = dynamic_scripted.run(...)

    assert [step.role for step in result.steps if step.step_type == "route"] == [
        "supervisor", "supervisor", "supervisor", "supervisor", "supervisor"
    ]
    assert result.route_count == 5


def test_dynamic_rejects_coder_before_plan_and_evidence(...):
    assert result.status is RunStatus.FAILED
    assert result.stop_reason == "invalid_supervisor_route"


def test_dynamic_stops_at_twelve_routes(...):
    assert result.stop_reason == "supervisor_route_budget_exhausted"
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/phase2/test_dynamic_architecture.py -q`

Expected: FAIL because Dynamic is absent.

- [ ] **Step 3: Implement decision schema and deterministic route guard**

```python
class SupervisorDecision(BaseModel):
    next_role: Literal["planner", "retriever", "coder", "reviewer", "stop", "fail"]
    reason: str = Field(min_length=1, max_length=500)
```

The guard rejects Coder before a plan and evidence exist, Reviewer before a non-empty diff exists, a third Reviewer invocation, any 13th route, and `stop` before public verification succeeds. Every Supervisor model call contributes to `RoleName.SUPERVISOR` usage.

- [ ] **Step 4: Build Dynamic routing**

Use one `supervisor` node and conditional routing to the four existing role nodes or END. Each role has one fixed edge back to Supervisor. Do not mix a normal and conditional edge from Supervisor. Compile with `recursion_limit=30`; normalize `GraphRecursionError` to `supervisor_route_budget_exhausted`.

- [ ] **Step 5: Verify GREEN**

Run: `.venv/bin/python -m pytest tests/phase2/test_dynamic_architecture.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/issueflow/architectures/dynamic.py tests/phase2/test_dynamic_architecture.py
git commit -m "feat: add dynamic supervisor workflow"
```

---

### Task 7: Integrate architecture selection, persistence, and the workbench

**Files:**
- Create: `src/issueflow/architectures/factory.py`
- Modify: `src/issueflow/run_service.py`
- Modify: `src/issueflow/trace_store.py`
- Modify: `src/issueflow/ui.py`
- Modify: `src/issueflow/reviewer.py`
- Modify: `tests/test_run_service.py`
- Modify: `tests/test_trace_store.py`
- Modify: `tests/test_ui.py`
- Modify: `tests/test_reviewer.py`
- Create: `tests/phase2/test_architecture_factory.py`

**Interfaces:**
- Consumes: All architecture implementations.
- Produces: `ArchitectureFactory.create(kind, case, workspace) -> ArchitectureRunner`; `RunService.start(case_id, budget, architecture=ArchitectureKind.SINGLE)`; persisted `run.architecture`.

- [ ] **Step 1: Write migration, default, and selector tests**

Assert that an existing database gains an `architecture TEXT NOT NULL DEFAULT 'single'` column; a two-argument `RunService.start` persists `single`; an explicit Fixed request persists `fixed`; JSON includes `run.architecture`; Streamlit passes the selected architecture without changing the selected case budget; and the outer advisory Reviewer model's tokens/cost are present on the persisted `review` step.

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/phase2/test_architecture_factory.py tests/test_run_service.py tests/test_trace_store.py tests/test_ui.py -q
```

Expected: FAIL on missing architecture field/factory/selector.

- [ ] **Step 3: Replace the Agent-only factory boundary**

`RunService.__init__` accepts `architecture_factory: Callable[[ArchitectureKind, BenchmarkCase, Path], ArchitectureRunner]`. `start` defaults to `ArchitectureKind.SINGLE`, creates `RunContext(run_id=run_id)`, invokes the selected runner, and appends returned role/route/tool steps exactly once. Keep a temporary keyword-only `agent_factory` compatibility adapter only if existing tests require it; remove it before Task 8 after all call sites migrate.

- [ ] **Step 4: Persist architecture safely**

Add the migration and include architecture in create/get/export queries. Do not add a second table for role metrics in 2A; derive role aggregates from immutable `TraceStep.role` fields.

Change the outer Reviewer model boundary to return its parsed result plus shared `Usage`. Persist that usage on the `review` trace step, including invalid-response/request-failed outcomes. Reviewer failure remains advisory and cannot change deterministic `functional_success`; its model cost still counts toward the run total.

- [ ] **Step 5: Add workbench selector and role metrics**

The selector labels are `Direct`, `Single Agent`, `Fixed Multi-Agent`, and `Dynamic Supervisor`. Default is `Single Agent`. Change the title from “单 Agent 修复工作台” to “IssueFlow Agent 架构工作台”. Finished runs show architecture, role call counts, route count, and the existing diff/test/reviewer evidence. Do not add an experiment dashboard.

- [ ] **Step 6: Verify GREEN**

Run the focused tests from Step 2.

Expected: PASS and no credential appears in UI snapshots or JSON.

- [ ] **Step 7: Commit**

```bash
git add src/issueflow/architectures/factory.py src/issueflow/run_service.py src/issueflow/trace_store.py src/issueflow/ui.py src/issueflow/reviewer.py tests/phase2/test_architecture_factory.py tests/test_run_service.py tests/test_trace_store.py tests/test_ui.py tests/test_reviewer.py
git commit -m "feat: select and persist agent architectures"
```

---

### Task 8: Prove four-architecture integration and document 2A

**Files:**
- Create: `tests/phase2/test_architectures_e2e.py`
- Modify: `tests/test_e2e_smoke.py`
- Create: `docs/phase-2a-architecture-notes.md`
- Create: `docs/phase-2-progress.md`
- Modify: `README.md`
- Modify: `README.zh-CN.md`

**Interfaces:**
- Consumes: Completed four-architecture runtime.
- Produces: Deterministic Docker/Git/LangGraph/SQLite/JSON acceptance and a learning handoff.

- [ ] **Step 1: Add an offline parameterized end-to-end test**

Parameterize over all four `ArchitectureKind` values. Inject scripted structured models and role functions, but keep Git workspace creation, Docker reproduction, patch application, independent verification, diff, `RunService`, SQLite, and JSON real. Assert each result is successful, records the correct architecture, contains role-appropriate steps, and uses the same `Budget` object values.

- [ ] **Step 2: Verify RED on any missing integration**

Run: `.venv/bin/python -m pytest tests/phase2/test_architectures_e2e.py -q`

Expected: the first run may FAIL on incomplete factory/trace/UI wiring; fix only the observed integration gaps.

- [ ] **Step 3: Remove the temporary Agent-only factory path**

Update all production and test constructors to the architecture factory. Preserve only `RunService.start(case_id, budget)` defaulting to Single; do not keep two constructor systems.

- [ ] **Step 4: Write the 2A learning note**

`docs/phase-2a-architecture-notes.md` must answer: contract purpose; State/Node/Edge mapping; Fixed vs Dynamic; why Reviewer does not decide success; budget aggregation; route failure behavior; current limitations; and a two-minute interview answer.

- [ ] **Step 5: Run full verification**

Run:

```bash
make verify-phase-1
make test-phase-2
git diff --check
```

Expected: all phase-one checks and all 2A tests PASS; five compatibility Benchmarks remain 5/5.

- [ ] **Step 6: Credential scan**

Run a Python check that reads the actual `DEEPSEEK_API_KEY` from the environment and asserts it is absent from `git diff main..HEAD`, generated JSON fixtures, and SQLite test outputs without printing the value.

- [ ] **Step 7: Update progress and commit**

Set 2A to `8/8`, verification `PASS`, and status `Complete`; record actual paid spend, which should remain CNY 0 for deterministic implementation tests.

```bash
git add tests/phase2/test_architectures_e2e.py tests/test_e2e_smoke.py docs/phase-2a-architecture-notes.md docs/phase-2-progress.md README.md README.zh-CN.md
git commit -m "test: verify phase two agent architectures"
```
