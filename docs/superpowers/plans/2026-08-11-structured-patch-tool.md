# Structured Patch Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the model-facing raw diff contract with a deterministic, workspace-confined exact-text replacement while preserving legacy patch inputs internally.

**Architecture:** DeepSeek continues to call the `apply_patch` tool, but its published JSON schema requires `path`, `old_text`, and `new_text`. `ToolExecutor` validates the relative path, requires exactly one `old_text` match, and writes the replacement without invoking a shell; the existing `patch` argument remains accepted only for backward compatibility.

**Tech Stack:** Python 3.12, Pydantic 2, HTTPX, pytest, Ruff, Git, Docker, DeepSeek Chat Completions.

## Global Constraints

- All model-provided paths must remain inside the resolved task workspace.
- Structured replacement must reject zero matches and multiple matches.
- No model-provided string may be executed as a shell command.
- Existing Git unified diff and `*** Begin Patch` tests must remain green.
- Every behavior change follows red-green TDD.

---

### Task 1: Publish and execute structured patch arguments

**Files:**
- Modify: `src/issueflow/agent.py`
- Modify: `tests/test_agent.py`
- Modify: `tests/test_deepseek.py`

**Interfaces:**
- Consumes: `ModelAction(tool="apply_patch", arguments={...})`, `ToolExecutor.workspace`.
- Produces: `ToolExecutor.execute(action) -> "patch applied"` for `{path, old_text, new_text}`; DeepSeek tool schema exposes only these three fields.

- [ ] **Step 1: Write failing executor tests**

Add tests that use these literal arguments:

```python
ModelAction(
    tool="apply_patch",
    arguments={
        "path": "micrograd/engine.py",
        "old_text": "        return self\n",
        "new_text": "        return self * -1\n",
    },
)
```

Assert a unique match is replaced. Add separate tests asserting `ValueError` messages for zero matches, two matches, and `path="../secret.txt"`.

- [ ] **Step 2: Write the failing model-schema test**

In the HTTPX mock handler, locate the `apply_patch` function definition and assert:

```python
parameters = apply_patch_tool["function"]["parameters"]
assert parameters["required"] == ["path", "old_text", "new_text"]
assert set(parameters["properties"]) == {"path", "old_text", "new_text"}
```

- [ ] **Step 3: Run tests to verify red**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_agent.py::test_structured_patch_replaces_exactly_one_match \
  tests/test_agent.py::test_structured_patch_rejects_zero_matches \
  tests/test_agent.py::test_structured_patch_rejects_multiple_matches \
  tests/test_agent.py::test_structured_patch_rejects_workspace_traversal \
  tests/test_deepseek.py::test_deepseek_client_publishes_structured_patch_schema -q
```

Expected: failures because `patch` is still the only accepted/published argument.

- [ ] **Step 4: Implement the minimal structured replacement**

Update `TOOL_ARGUMENTS["apply_patch"]` to accept `path`, `old_text`, `new_text`, and legacy `patch`. Change the model-facing tool schema to require only the structured fields. In `ToolExecutor.execute`, route structured arguments to a helper equivalent to:

```python
def _apply_structured_patch(self, path_value: object, old_value: object, new_value: object) -> None:
    path = self._resolve_workspace_path(path_value)
    if not isinstance(old_value, str) or not old_value:
        raise ValueError("structured patch requires path, old_text, and new_text")
    if not isinstance(new_value, str):
        raise ValueError("structured patch requires path, old_text, and new_text")
    original = path.read_text(encoding="utf-8")
    matches = original.count(old_value)
    if matches != 1:
        raise ValueError(f"old_text must match exactly once: found {matches}")
    path.write_text(original.replace(old_value, new_value, 1), encoding="utf-8")
```

Keep the existing legacy `patch` branch unchanged.

- [ ] **Step 5: Run focused and Agent regression tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py tests/test_deepseek.py -q
.venv/bin/python -m ruff check src/issueflow/agent.py tests/test_agent.py tests/test_deepseek.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit the protocol fix**

```bash
git add src/issueflow/agent.py tests/test_agent.py tests/test_deepseek.py
git commit -m "fix: stabilize model patch protocol"
```

### Task 2: Re-run Task 6 acceptance and finish the orchestration commit

**Files:**
- Modify: `src/issueflow/models.py`
- Modify: `src/issueflow/trace_store.py`
- Create: `src/issueflow/reviewer.py`
- Create: `src/issueflow/run_service.py`
- Modify: `tests/test_trace_store.py`
- Create: `tests/test_reviewer.py`
- Create: `tests/test_run_service.py`

**Interfaces:**
- Consumes: `RunService.start(case_id, budget)`, real `constructed-01`, `DEEPSEEK_API_KEY`, Docker.
- Produces: terminal `RunRecord`, ordered SQLite trace, independent verification result, diff, and advisory review.

- [ ] **Step 1: Run Task 6 focused tests**

```bash
.venv/bin/python -m pytest tests/test_reviewer.py tests/test_run_service.py tests/test_trace_store.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run the complete automated verification**

```bash
make lint
make test
```

Expected: Ruff passes and the complete pytest suite, including Docker runtime validation, passes.

- [ ] **Step 3: Run real `constructed-01` acceptance**

Assemble `RunService` with `GitWorkspacePreparer`, `DockerSandbox`, `SingleAgent`, `DeepSeekModelClient`, `Reviewer`, `DeepSeekReviewClient`, and `TraceStore`. Use:

```python
Budget(
    max_tool_calls=12,
    max_patch_attempts=2,
    max_seconds=300,
    max_input_tokens=30_000,
    max_output_tokens=6_000,
    max_cost_usd=0.05,
)
```

Expected persisted result:

```text
status=succeeded
functional_success=true
stop_reason=functional_success
steps include reproduction, apply_patch, verification, diff, review
```

- [ ] **Step 4: Inspect the persisted trace and secret boundary**

Export the real run with `TraceStore.export_json(run_id)`. Confirm sequences are contiguous, the diff is non-empty, Reviewer status is present, efficiency metrics are non-negative, and neither the JSON nor `git diff` contains the API Key.

- [ ] **Step 5: Commit Task 6**

```bash
git add src/issueflow/models.py src/issueflow/trace_store.py \
  src/issueflow/reviewer.py src/issueflow/run_service.py \
  tests/test_trace_store.py tests/test_reviewer.py tests/test_run_service.py
git commit -m "feat: orchestrate runs and review repair evidence"
```
