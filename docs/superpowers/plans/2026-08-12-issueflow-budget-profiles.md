# IssueFlow Benchmark Budget Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single UI-wide repair budget with explicit per-Benchmark budget profiles and show users the selected limits and exact budget-exhaustion reason.

**Architecture:** `BenchmarkCase` owns a required `budget_profile` name, while a focused `issueflow.budget` module maps the three names to bounded `Budget` values and returns independent copies. Streamlit resolves the selected case through that module, displays the limits, passes the same budget to `RunService`, and translates persisted normalized stop reasons without changing Agent, SQLite, Reviewer, or success semantics.

**Tech Stack:** Python 3.12, Pydantic 2, PyYAML, Streamlit, pytest, Ruff, Docker.

## Global Constraints

- Valid profile names are exactly `small`, `medium`, and `large`.
- `small`: 12 tools, 2 patches, 300 seconds, 30,000 input tokens, 6,000 output tokens, $0.05.
- `medium`: 18 tools, 4 patches, 450 seconds, 50,000 input tokens, 8,000 output tokens, $0.10.
- `large`: 24 tools, 6 patches, 600 seconds, 80,000 input tokens, 12,000 output tokens, $0.20.
- `historical-01` uses `medium`; `constructed-01` through `constructed-04` use `small`.
- Every catalog case must explicitly declare a profile; there is no implicit default.
- Budgets remain hard limits. Do not add automatic retries, runtime expansion, or user-entered limits.
- Do not change the SQLite schema, trace JSON schema, success gates, Reviewer behavior, Docker boundary, API-key handling, or tool allowlist.
- UI copy must state the exact exhausted resource and must not imply that a larger budget guarantees a repair.
- Preserve the user-owned untracked file `2026-08-11-software-engineering-agent-design.md` in the main checkout.

---

### Task 1: Make budget profiles explicit catalog data

**Files:**
- Modify: `src/issueflow/models.py:49-64`
- Modify: `benchmarks/micrograd.yaml:2-62`
- Modify: `tests/test_models.py:26-75`
- Modify: `tests/test_benchmark.py:11-79`
- Modify: `tests/test_agent.py:43-58`
- Modify: `tests/test_e2e_smoke.py:92-108`
- Modify: `tests/test_run_service.py:13-31`
- Modify: `tests/test_ui.py:14-70`

**Interfaces:**
- Consumes: Existing `BenchmarkCase` Pydantic validation and `load_catalog(path: Path) -> dict[str, BenchmarkCase]`.
- Produces: Required `BenchmarkCase.budget_profile: Literal["small", "medium", "large"]` for the budget resolver and UI.

- [ ] **Step 1: Write model tests for required and constrained profile names**

Add a complete valid-case factory to `tests/test_models.py`, then assert missing and unknown profiles fail:

```python
def valid_historical_case(**updates) -> dict[str, object]:
    values = {
        "id": "historical-01",
        "kind": "historical",
        "budget_profile": "medium",
        "repository_url": "https://github.com/karpathy/micrograd",
        "revision": "a" * 40,
        "license": "MIT",
        "issue": "Shared graphs leave gradients at zero.",
        "source_url": "https://github.com/karpathy/micrograd/commit/fix",
        "reproduce_command": "python -m pytest",
        "verify_command": "python -m pytest",
        "reference_patch": "patches/historical-01-fix.patch",
        "construction_notes": "Historical public repair.",
    }
    values.update(updates)
    return values


def test_benchmark_case_requires_an_explicit_budget_profile():
    values = valid_historical_case()
    values.pop("budget_profile")

    with pytest.raises(ValidationError, match="budget_profile"):
        BenchmarkCase(**values)


def test_benchmark_case_rejects_an_unknown_budget_profile():
    with pytest.raises(ValidationError, match="budget_profile"):
        BenchmarkCase(**valid_historical_case(budget_profile="unlimited"))
```

Also pass `budget_profile="medium"` to the existing bad-revision fixture and `budget_profile="small"` to the existing constructed fixture so those tests continue to isolate their intended validation rules.

- [ ] **Step 2: Run the model tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_models.py -q`

Expected: FAIL because `BenchmarkCase` does not define or require `budget_profile`.

- [ ] **Step 3: Add the required model field**

In `BenchmarkCase`, place the field next to `kind`:

```python
class BenchmarkCase(BaseModel):
    id: str
    kind: Literal["historical", "constructed"]
    budget_profile: Literal["small", "medium", "large"]
    repository_url: str
```

Do not give the field a default.

- [ ] **Step 4: Run the model tests to verify the field behavior passes**

Run: `.venv/bin/python -m pytest tests/test_models.py -q`

Expected: PASS.

- [ ] **Step 5: Write catalog assertions for the five assigned profiles**

Change `tests/test_benchmark.py::make_case` to require a profile and include it in the YAML dictionary:

```python
def make_case(case_id: str, kind: str, budget_profile: str) -> dict[str, str]:
    case = {
        "id": case_id,
        "kind": kind,
        "budget_profile": budget_profile,
        "repository_url": "https://github.com/karpathy/micrograd",
        "revision": "a" * 40,
        "license": "MIT",
        "issue": "Verify a gradient calculation",
        "source_url": "https://github.com/karpathy/micrograd",
        "reproduce_command": "python -m pytest",
        "verify_command": "python -m pytest",
        "reference_patch": f"patches/{case_id}.patch",
        "construction_notes": "A controlled regression case.",
    }
    if kind == "constructed":
        case["fault_patch"] = f"patches/{case_id}-fault.patch"
    return case
```

Build the test catalog with `medium` for `historical-01` and `small` for the four constructed cases. Add this assertion after loading:

```python
assert {case_id: case.budget_profile for case_id, case in catalog.items()} == {
    "historical-01": "medium",
    "constructed-01": "small",
    "constructed-02": "small",
    "constructed-03": "small",
    "constructed-04": "small",
}
```

- [ ] **Step 6: Run the catalog tests to verify the checked-in YAML fails**

Run: `.venv/bin/python -m pytest tests/test_benchmark.py -q`

Expected: PASS because the temporary catalog explicitly supplies a valid profile for every case.

- [ ] **Step 7: Assign profiles in the real catalog and all direct test fixtures**

Add the following immediately after each case's `kind` in `benchmarks/micrograd.yaml`:

```yaml
  - id: historical-01
    kind: historical
    budget_profile: medium
```

Use `budget_profile: small` for all four `constructed-*` entries.

Add `budget_profile="small"` to the direct constructed `BenchmarkCase` factories in `tests/test_agent.py`, `tests/test_e2e_smoke.py`, `tests/test_run_service.py`, and the constructed cases in `tests/test_ui.py`. Add `budget_profile="medium"` to the direct historical cases in `tests/test_ui.py`.

- [ ] **Step 8: Run affected and full tests**

Run: `.venv/bin/python -m pytest tests/test_models.py tests/test_benchmark.py tests/test_agent.py tests/test_run_service.py tests/test_ui.py tests/test_e2e_smoke.py -q`

Expected: PASS with no missing-profile errors and the Docker end-to-end case passing.

- [ ] **Step 9: Commit explicit catalog profiles**

```bash
git add src/issueflow/models.py benchmarks/micrograd.yaml tests/test_models.py tests/test_benchmark.py tests/test_agent.py tests/test_e2e_smoke.py tests/test_run_service.py tests/test_ui.py
git commit -m "feat: assign budget profiles to benchmarks"
```

---

### Task 2: Resolve bounded profiles and prove the old patch threshold is gone

**Files:**
- Create: `src/issueflow/budget.py`
- Create: `tests/test_budget.py`
- Modify: `tests/test_agent.py:150-177`

**Interfaces:**
- Consumes: `BenchmarkCase.budget_profile` from Task 1 and the existing `Budget` model.
- Produces: `BUDGET_PROFILES: Mapping[str, Budget]` and `budget_for_case(case: BenchmarkCase) -> Budget`.

- [ ] **Step 1: Write exact resolver tests**

Create `tests/test_budget.py`:

```python
from pathlib import Path

from issueflow.benchmark import load_catalog
from issueflow.budget import budget_for_case


EXPECTED = {
    "small": {
        "max_tool_calls": 12,
        "max_patch_attempts": 2,
        "max_seconds": 300,
        "max_input_tokens": 30_000,
        "max_output_tokens": 6_000,
        "max_cost_usd": 0.05,
    },
    "medium": {
        "max_tool_calls": 18,
        "max_patch_attempts": 4,
        "max_seconds": 450,
        "max_input_tokens": 50_000,
        "max_output_tokens": 8_000,
        "max_cost_usd": 0.10,
    },
    "large": {
        "max_tool_calls": 24,
        "max_patch_attempts": 6,
        "max_seconds": 600,
        "max_input_tokens": 80_000,
        "max_output_tokens": 12_000,
        "max_cost_usd": 0.20,
    },
}


def test_budget_profiles_have_the_documented_limits():
    catalog = load_catalog(Path("benchmarks/micrograd.yaml"))
    historical = catalog["historical-01"]
    constructed = catalog["constructed-01"]

    assert budget_for_case(historical).model_dump() == EXPECTED["medium"]
    assert budget_for_case(constructed).model_dump() == EXPECTED["small"]


def test_budget_resolution_returns_independent_objects():
    case = load_catalog(Path("benchmarks/micrograd.yaml"))["historical-01"]

    first = budget_for_case(case)
    second = budget_for_case(case)
    first.max_patch_attempts = 99

    assert first is not second
    assert second.model_dump() == EXPECTED["medium"]
```

Add a parametrized test that builds a case copy for each profile and compares its full `model_dump()` to `EXPECTED[profile]`, so the currently-unused `large` profile is also covered.

```python
import pytest


@pytest.mark.parametrize("profile", ["small", "medium", "large"])
def test_every_named_profile_resolves_to_its_documented_limits(profile):
    case = load_catalog(Path("benchmarks/micrograd.yaml"))["constructed-01"].model_copy(
        update={"budget_profile": profile}
    )

    assert budget_for_case(case).model_dump() == EXPECTED[profile]
```

- [ ] **Step 2: Run resolver tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_budget.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'issueflow.budget'`.

- [ ] **Step 3: Implement the focused resolver module**

Create `src/issueflow/budget.py`:

```python
"""Bounded resource profiles for registered benchmark cases."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from issueflow.models import BenchmarkCase, Budget


BUDGET_PROFILES: Final[Mapping[str, Budget]] = MappingProxyType(
    {
        "small": Budget(
            max_tool_calls=12,
            max_patch_attempts=2,
            max_seconds=300,
            max_input_tokens=30_000,
            max_output_tokens=6_000,
            max_cost_usd=0.05,
        ),
        "medium": Budget(
            max_tool_calls=18,
            max_patch_attempts=4,
            max_seconds=450,
            max_input_tokens=50_000,
            max_output_tokens=8_000,
            max_cost_usd=0.10,
        ),
        "large": Budget(
            max_tool_calls=24,
            max_patch_attempts=6,
            max_seconds=600,
            max_input_tokens=80_000,
            max_output_tokens=12_000,
            max_cost_usd=0.20,
        ),
    }
)


def budget_for_case(case: BenchmarkCase) -> Budget:
    """Return an independent hard-limit object for one registered case."""
    return BUDGET_PROFILES[case.budget_profile].model_copy(deep=True)
```

- [ ] **Step 4: Run resolver tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_budget.py -q`

Expected: PASS for all three profiles and copy isolation.

- [ ] **Step 5: Write a regression test for four allowed patches and a blocked fifth**

In `tests/test_agent.py`, add a helper that uses structured replacements so every allowed patch is valid:

```python
def structured_patch(old: str, new: str) -> ModelAction:
    return ModelAction(
        tool="apply_patch",
        arguments={"path": "engine.py", "old_text": old, "new_text": new},
    )


def test_medium_profile_allows_four_patches_and_blocks_the_fifth(tmp_path):
    from issueflow.budget import budget_for_case

    target = tmp_path / "engine.py"
    target.write_text("state = 0\n", encoding="utf-8")
    case = make_case().model_copy(update={"budget_profile": "medium"})
    actions = [
        structured_patch(f"state = {value}\n", f"state = {value + 1}\n")
        for value in range(5)
    ]
    agent = SingleAgent(FakeModel(actions), ToolExecutor(tmp_path, case, None))

    result = agent.run(case, tmp_path, budget_for_case(case))

    assert result.status is RunStatus.BUDGET_EXHAUSTED
    assert result.stop_reason == "patch_budget_exhausted"
    assert result.patch_attempts == 4
    assert target.read_text(encoding="utf-8") == "state = 4\n"
    assert [step.status for step in result.steps] == [
        "completed",
        "completed",
        "completed",
        "completed",
        "budget_exhausted",
    ]
```

- [ ] **Step 6: Prove the regression test would fail under the old limit**

Temporarily pass `make_budget(max_tool_calls=18, max_patch_attempts=2)` to the new test.

Run: `.venv/bin/python -m pytest tests/test_agent.py::test_medium_profile_allows_four_patches_and_blocks_the_fifth -q`

Expected: FAIL because only two patches are applied and the file remains `state = 2`.

Restore `budget_for_case(case)` immediately after observing the failure.

- [ ] **Step 7: Run resolver and Agent tests with the real medium profile**

Run: `.venv/bin/python -m pytest tests/test_budget.py tests/test_agent.py -q`

Expected: PASS; four patches apply and the fifth request is blocked.

- [ ] **Step 8: Commit the resolver and hard-limit regression**

```bash
git add src/issueflow/budget.py tests/test_budget.py tests/test_agent.py
git commit -m "feat: resolve bounded benchmark budgets"
```

---

### Task 3: Display selected limits and exact stop reasons in Streamlit

**Files:**
- Modify: `src/issueflow/ui.py:13-38,78-171,199-299`
- Modify: `tests/test_ui.py:9-12,14-170,280-393`

**Interfaces:**
- Consumes: `budget_for_case(case: BenchmarkCase) -> Budget` from Task 2 and persisted `run.stop_reason` strings.
- Produces: `format_budget_summary(case: BenchmarkCase, budget: Budget) -> str`, `describe_stop_reason(reason: str) -> str`, and `RunView.stop_reason_label: str`.

- [ ] **Step 1: Write pure view-model tests for budget and stop-reason copy**

Update imports in `tests/test_ui.py` to include `budget_for_case`, `describe_stop_reason`, and `format_budget_summary`. Add:

```python
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
    case = load_catalog(Path("benchmarks/micrograd.yaml"))["historical-01"]
    budget = budget_for_case(case)

    assert format_budget_summary(case, budget) == (
        "预算档位：medium · 工具 18 次 · 补丁 4 次 · 450 秒 · "
        "输入 50,000 Token · 输出 8,000 Token · 最高 $0.10"
    )
```

Extend the existing failed-run trace fixture with `stop_reason="patch_budget_exhausted"` and assert `make_run_view(trace).stop_reason_label == "补丁次数预算已用尽"`.

- [ ] **Step 2: Run pure UI tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ui.py -q`

Expected: FAIL because the helper functions and `RunView.stop_reason_label` do not exist.

- [ ] **Step 3: Implement translations and budget summary**

In `src/issueflow/ui.py`, import the resolver and replace `DEFAULT_BUDGET` with normalized copy mappings:

```python
from issueflow.budget import budget_for_case


STOP_REASON_LABELS = {
    "patch_budget_exhausted": "补丁次数预算已用尽",
    "tool_budget_exhausted": "工具调用预算已用尽",
    "time_budget_exhausted": "运行时间预算已用尽",
    "input_token_budget_exhausted": "输入 Token 预算已用尽",
    "output_token_budget_exhausted": "输出 Token 预算已用尽",
    "cost_budget_exhausted": "成本预算已用尽",
}


def describe_stop_reason(reason: str) -> str:
    """Translate known terminal reasons and safely preserve unknown evidence."""
    return STOP_REASON_LABELS.get(reason, redact(reason))


def format_budget_summary(case: BenchmarkCase, budget: Budget) -> str:
    """Render every hard limit selected for one catalog case."""
    return (
        f"预算档位：{case.budget_profile} · 工具 {budget.max_tool_calls} 次 · "
        f"补丁 {budget.max_patch_attempts} 次 · {budget.max_seconds} 秒 · "
        f"输入 {budget.max_input_tokens:,} Token · "
        f"输出 {budget.max_output_tokens:,} Token · 最高 ${budget.max_cost_usd:.2f}"
    )
```

Add `stop_reason_label: str` to `RunView` and assign it with `describe_stop_reason` inside `make_run_view`. Keep the existing redacted `stop_reason` field for debugging compatibility.

- [ ] **Step 4: Resolve and display the selected case budget**

In `render_app`, retain access to the selected `BenchmarkCase` rather than deriving the budget from the view alone:

```python
selected = case_views[selected_label]
selected_case = catalog[selected.id]
selected_budget = budget_for_case(selected_case)

st.caption(format_budget_summary(selected_case, selected_budget))
```

Replace:

```python
run_session.start(selected.id, DEFAULT_BUDGET)
```

with:

```python
run_session.start(selected.id, selected_budget)
```

In `_render_finished_run`, show the detailed reason only for unsuccessful results:

```python
if view.functional_success:
    st.success("功能验证通过")
else:
    st.error(f"运行结束：{view.status_label}")
    st.warning(f"停止原因：{view.stop_reason_label}")
```

- [ ] **Step 5: Add AppTest coverage for actual budget handoff**

In the `StaticService.start` method inside `test_workbench_runs_selected_case_and_renders_persisted_evidence`, assert the default selected historical case receives medium limits:

```python
def start(self, case_id, budget):
    assert case_id == "historical-01"
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
```

Before clicking the button, assert one rendered caption contains the exact medium summary. After clicking, retain all existing success, metrics, diff, and download assertions.

Add a second AppTest using the existing `render_app` pattern. Its service returns a terminal budget failure and its store returns the matching persisted trace:

```python
def test_workbench_explains_the_exact_exhausted_budget():
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
```

- [ ] **Step 6: Run UI tests**

Run: `.venv/bin/python -m pytest tests/test_ui.py -q`

Expected: PASS with correct medium handoff, summary copy, translations, redaction, and finished-run warning.

- [ ] **Step 7: Run the focused feature suite**

Run: `.venv/bin/python -m pytest tests/test_models.py tests/test_benchmark.py tests/test_budget.py tests/test_agent.py tests/test_ui.py -q`

Expected: PASS.

- [ ] **Step 8: Commit the Streamlit behavior**

```bash
git add src/issueflow/ui.py tests/test_ui.py
git commit -m "feat: show benchmark budget boundaries"
```

---

### Task 4: Document calibration limits and perform release verification

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/phase-1-evaluation.md`

**Interfaces:**
- Consumes: The three exact profiles and UI behavior delivered by Tasks 1-3.
- Produces: User-facing setup and evaluation language that distinguishes bounded resources from repair success.

- [ ] **Step 1: Update English documentation with profiles and non-guarantee wording**

Add a “Budget profiles” subsection near the safety boundary in `README.md` with this table:

```markdown
| Profile | Tools | Patches | Seconds | Input tokens | Output tokens | Cost cap |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `small` | 12 | 2 | 300 | 30,000 | 6,000 | $0.05 |
| `medium` | 18 | 4 | 450 | 50,000 | 8,000 | $0.10 |
| `large` | 24 | 6 | 600 | 80,000 | 12,000 | $0.20 |
```

State explicitly: every catalog case declares a profile; `historical-01` is `medium`; the current constructed cases are `small`; higher budgets increase available work but never guarantee a successful repair.

- [ ] **Step 2: Mirror the guidance in Chinese documentation**

Add the same values to `README.zh-CN.md` and include this sentence verbatim:

```markdown
预算档位只规定 Agent 最多可以使用多少资源，不保证任何一次运行或未来样本一定修复成功。
```

- [ ] **Step 3: Correct the evaluation report's future-sample guidance**

In `docs/phase-1-evaluation.md`, add this paragraph without changing the previously recorded `constructed-01` metrics or claiming a new `historical-01` success:

```markdown
后续样本必须显式选择经过实测的最小预算档位。预算只定义资源上限，不代表修复能力；评估应报告“运行 N 次、成功 M 次”，并分别记录预算耗尽、错误补丁和验证失败，不能用单次成功或更高档位宣称保证解决。
```

- [ ] **Step 4: Run formatting, unit, integration, Docker, and catalog verification**

Run: `make verify-phase-1`

Expected:

- Ruff check and format pass.
- The complete pytest suite passes, including real-Docker end-to-end replay.
- Docker image rebuild passes.
- All five Benchmark reference checks report `reproduction=FAIL_AS_EXPECTED verification=PASS`.

- [ ] **Step 5: Scan changed artifacts for credentials and diff errors**

Run:

```bash
git diff --check
.venv/bin/python -c "import os, subprocess; text=subprocess.run(['git','diff','main..HEAD'],capture_output=True,text=True,check=True).stdout; key=os.environ.get('DEEPSEEK_API_KEY'); assert not key or key not in text; print('credential-scan=PASS')"
```

Expected: no whitespace errors and `credential-scan=PASS` without printing the key. Controlled literal credential markers used by redaction tests are allowed; when set, the actual environment value must be absent from `main..HEAD`.

- [ ] **Step 6: Start the workbench and check its health endpoint**

Run in one terminal:

```bash
.venv/bin/python -m streamlit run src/issueflow/ui.py --server.headless true --server.port 8518 --browser.gatherUsageStats false
```

Run in a second terminal:

```bash
curl --fail --silent --show-error http://127.0.0.1:8518/_stcore/health
```

Expected: `ok`. Stop the temporary Streamlit process after the check.

- [ ] **Step 7: Commit documentation and release evidence**

```bash
git add README.md README.zh-CN.md docs/phase-1-evaluation.md
git commit -m "docs: explain benchmark budget calibration"
```

- [ ] **Step 8: Inspect the final branch state**

Run:

```bash
git status --short
git log --oneline --decorate -5
```

Expected: clean worktree with the design commit plus four implementation commits. Do not merge, push, or delete the worktree until the user chooses a finishing option.
