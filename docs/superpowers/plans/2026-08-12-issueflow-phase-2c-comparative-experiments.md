# IssueFlow Phase 2C Comparative Experiments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute a frozen, auditable comparison of Direct, Single, Fixed, and Dynamic over 20 strict cases with approximately 160 paid runs while keeping total DeepSeek spend at or below CNY 300.

**Architecture:** A versioned YAML manifest expands into immutable trial keys and config hashes. An experiment service schedules one fresh `RunService` attempt per trial, records spend and infrastructure classification in SQLite, stops at human/budget gates, and rebuilds all aggregates from persisted run IDs rather than hand-edited tables.

**Tech Stack:** Python 3.11+, Pydantic 2, PyYAML, SQLite, existing architecture/runtime, pytest, Ruff, DeepSeek API.

## Global Constraints

- Formal architectures are exactly `direct`, `single`, `fixed`, and `dynamic`.
- Formal cases are exactly the 20 qualified strict cases from 2B; compatibility and exploratory cases are excluded.
- Round one contains 20 cases × 4 architectures × repetition 1 = 80 planned trials.
- Exactly 10 cases selected by the frozen rule receive repetitions 2 and 3 for all four architectures = 80 additional planned trials.
- Formal total is 160 planned trials before explicitly linked infrastructure retries.
- Use one frozen model name, endpoint, temperature, prompt versions, case catalog hash, environment digests, budget profiles, and retry policy for all formal trials.
- Each case uses the same total budget for all architectures; role costs count against that total.
- Estimated CNY uses the manifest's conservative fixed conversion `usd_to_cny: 7.50`; batch gates also record the provider account's actual charged balance delta.
- Total phase-two paid spend cannot exceed CNY 300; category limits are development 45, calibration 30, first round 105, repeats 80, retry/Reviewer 25, reserve 15.
- The runner never silently retries. An eligible infrastructure retry creates a new trial linked by `retry_of_trial_id` and requires an explicit command after review.
- Invalid model JSON, bad patches, early model finish, poor retrieval, failed public/hidden tests, and budget exhaustion are Agent outcomes, not infrastructure failures.
- No paid batch starts without passing deterministic tests, a credential scan, a dry-run matrix check, and the user gate defined in its task.

---

### Task 1: Define frozen experiment and trial models

**Files:**
- Create: `src/issueflow/experiment_models.py`
- Create: `tests/phase2/test_experiment_models.py`

**Interfaces:**
- Consumes: `ArchitectureKind`, strict catalog IDs, budget profiles, and environment registry.
- Produces: `ExperimentConfig`, `TrialSpec`, `TrialStatus`, `FailureCategory`, canonical config hashing.

- [ ] **Step 1: Write schema and hash tests**

```python
def test_formal_config_requires_all_four_architectures():
    with pytest.raises(ValidationError, match="four architectures"):
        ExperimentConfig(**valid_config(architectures=["single"]))


def test_trial_key_is_stable():
    trial = TrialSpec(experiment_id="phase-2-formal-v1", case_id="mingpt-h01", architecture="fixed", repetition=1)
    assert trial.key == "phase-2-formal-v1:mingpt-h01:fixed:r1"


def test_canonical_hash_ignores_yaml_key_order(config_a, config_b):
    assert config_a.sha256 == config_b.sha256
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/phase2/test_experiment_models.py -q`

Expected: FAIL because experiment models are absent.

- [ ] **Step 3: Implement exact models**

```python
class TrialStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETE = "complete"
    NEEDS_ATTENTION = "needs_attention"


class FailureCategory(StrEnum):
    REPRODUCTION_FAILURE = "reproduction_failure"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    MODEL_PROTOCOL_FAILURE = "model_protocol_failure"
    RETRIEVAL_FAILURE = "retrieval_failure"
    PATCH_APPLICATION_FAILURE = "patch_application_failure"
    PUBLIC_TEST_FAILURE = "public_test_failure"
    HIDDEN_TEST_FAILURE = "hidden_test_failure"
    BUDGET_EXHAUSTED = "budget_exhausted"
    REVIEW_LOOP_EXHAUSTED = "review_loop_exhausted"


class TrialSpec(BaseModel):
    experiment_id: str
    case_id: str
    architecture: ArchitectureKind
    repetition: PositiveInt
    retry_of_trial_id: str | None = None


class ExperimentConfig(BaseModel):
    id: str
    status: Literal["draft", "frozen"]
    model: str
    base_url: str
    temperature: float = Field(ge=0, le=2)
    architectures: list[ArchitectureKind]
    case_ids: list[str]
    catalog_sha256: str
    environment_digests: dict[str, str]
    prompt_versions: dict[str, str]
    budget_profiles_sha256: str
    usd_to_cny: PositiveFloat
    spend_limit_cny: PositiveFloat
    spend_limits_cny: dict[Literal[
        "development", "calibration", "first_round", "repeats", "retry_reviewer", "reserve"
    ], PositiveFloat]
    repetitions: list[PositiveInt]
```

Canonical hash is SHA-256 of sorted, compact JSON generated from validated data; secrets and environment API keys are not config fields.

- [ ] **Step 4: Enforce draft/frozen policy**

Draft manifests may be schema-checked and expanded in tests, but a paid runner rejects them. Frozen manifests require non-empty case IDs, real 64-character catalog/budget/environment/prompt hashes, all four architectures, `usd_to_cny=7.50`, and the six category limits `45/30/105/80/25/15`, which must sum to CNY 300.

- [ ] **Step 5: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest tests/phase2/test_experiment_models.py -q
git add src/issueflow/experiment_models.py tests/phase2/test_experiment_models.py
git commit -m "feat: define frozen experiment manifests"
```

---

### Task 2: Persist experiments and immutable trials

**Files:**
- Modify: `src/issueflow/trace_store.py`
- Create: `src/issueflow/experiment_store.py`
- Create: `tests/phase2/test_experiment_store.py`
- Modify: `tests/test_trace_store.py`

**Interfaces:**
- Consumes: `ExperimentConfig`, `TrialSpec`, and existing run IDs.
- Produces: `ExperimentStore.create_experiment`, `plan_trial`, `start_trial`, `finish_trial`, `list_trials`, and export methods.

- [ ] **Step 1: Write migration and state-transition tests**

Assert creation of tables `experiments` and `experiment_trials`; unique trial key; immutable config hash; `planned -> running -> complete|needs_attention`; a complete trial cannot be overwritten; and every completed non-infrastructure trial has exactly one run ID.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/phase2/test_experiment_store.py tests/test_trace_store.py -q`

Expected: FAIL because tables and store are absent.

- [ ] **Step 3: Add exact schema**

```sql
CREATE TABLE IF NOT EXISTS experiments (
  id TEXT PRIMARY KEY,
  config_sha256 TEXT NOT NULL,
  config_json TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiment_trials (
  id TEXT PRIMARY KEY,
  experiment_id TEXT NOT NULL,
  trial_key TEXT NOT NULL UNIQUE,
  case_id TEXT NOT NULL,
  architecture TEXT NOT NULL,
  repetition INTEGER NOT NULL,
  retry_of_trial_id TEXT,
  status TEXT NOT NULL,
  run_id TEXT,
  failure_category TEXT,
  estimated_cost_usd REAL NOT NULL DEFAULT 0,
  estimated_cost_cny REAL NOT NULL DEFAULT 0,
  provider_cost_cny REAL,
  config_sha256 TEXT NOT NULL,
  FOREIGN KEY (experiment_id) REFERENCES experiments(id)
);
```

All stored config JSON and error labels pass through existing redaction. Do not store raw prompts or API responses in these tables.

- [ ] **Step 4: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest tests/phase2/test_experiment_store.py tests/test_trace_store.py -q
git add src/issueflow/trace_store.py src/issueflow/experiment_store.py tests/phase2/test_experiment_store.py tests/test_trace_store.py
git commit -m "feat: persist immutable experiment trials"
```

---

### Task 3: Expand manifests into a fair, deterministic matrix

**Files:**
- Create: `src/issueflow/experiment_schedule.py`
- Create: `scripts/plan_experiment.py`
- Create: `tests/phase2/test_experiment_schedule.py`

**Interfaces:**
- Consumes: Frozen/draft manifest and strict catalog.
- Produces: ordered `list[TrialSpec]`, dry-run JSON, and duplicate/mismatch rejection.

- [ ] **Step 1: Write 80-trial matrix tests**

```python
def test_round_one_has_eighty_unique_trials(formal_config, strict_catalog):
    trials = build_schedule(formal_config, strict_catalog)
    assert len(trials) == 80
    assert len({trial.key for trial in trials}) == 80
    assert Counter(t.architecture for t in trials) == {
        ArchitectureKind.DIRECT: 20,
        ArchitectureKind.SINGLE: 20,
        ArchitectureKind.FIXED: 20,
        ArchitectureKind.DYNAMIC: 20,
    }
```

Also reject exploratory/compatibility IDs, missing strict IDs, duplicate case IDs, catalog hash mismatch, unknown environment digest, or architectures in any order other than the canonical four.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/phase2/test_experiment_schedule.py -q`

Expected: FAIL because scheduler is absent.

- [ ] **Step 3: Implement blocked randomization**

For each case, derive a stable architecture order from `sha256(f"{experiment_id}:{case_id}")`, then interleave one trial per case before the next trial from the same repository. Store the fully expanded order in dry-run JSON so execution order is auditable and not chosen after results are seen.

- [ ] **Step 4: Implement dry-run CLI**

```bash
.venv/bin/python scripts/plan_experiment.py \
  --config experiments/configs/phase-2-formal.yaml \
  --catalog benchmarks/catalogs/strict.yaml \
  --output artifacts/phase-2/formal-plan.json
```

Dry-run performs no model calls and prints counts, maximum possible USD/CNY cost based on case budgets, repository/category distribution, and manifest hash.

- [ ] **Step 5: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest tests/phase2/test_experiment_schedule.py -q
git add src/issueflow/experiment_schedule.py scripts/plan_experiment.py tests/phase2/test_experiment_schedule.py
git commit -m "feat: plan fair experiment matrices"
```

---

### Task 4: Enforce CNY spend ledgers and human batch gates

**Files:**
- Create: `src/issueflow/spend.py`
- Create: `tests/phase2/test_spend.py`
- Modify: `src/issueflow/experiment_store.py`

**Interfaces:**
- Consumes: role/run USD cost, frozen 7.50 conversion, provider charge entries, and category limits.
- Produces: `SpendLedger`, `SpendDecision`, and batch-gate reports.

- [ ] **Step 1: Write exact limit tests**

Assert estimated CNY is `Decimal(str(cost_usd)) * Decimal("7.50")`; a new run is denied if its case maximum could exceed the category or CNY 300 total; completed trial cost is counted once; provider actual cost can be recorded but cannot be lower than zero; and key material is rejected from notes.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/phase2/test_spend.py -q`

Expected: FAIL because the ledger is absent.

- [ ] **Step 3: Implement Decimal-based decisions**

```python
class SpendDecision(BaseModel):
    allowed: bool
    reason: Literal["within_limit", "category_limit", "total_limit", "human_gate"]
    estimated_spend_cny: Decimal
    remaining_cny: Decimal


class SpendLedger:
    def can_start(self, category: str, maximum_cost_usd: Decimal) -> SpendDecision: ...
    def record_trial(self, trial_id: str, estimated_cost_usd: Decimal) -> None: ...
    def record_provider_delta(self, batch_id: str, amount_cny: Decimal) -> None: ...
```

Use `ROUND_UP` to CNY 0.01 for preflight maximums. Require a new human gate after each 20 completed paid trials, even when monetary headroom remains.

- [ ] **Step 4: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest tests/phase2/test_spend.py -q
git add src/issueflow/spend.py src/issueflow/experiment_store.py tests/phase2/test_spend.py
git commit -m "feat: enforce phase two spend gates"
```

---

### Task 5: Implement resumable single-trial and bounded-batch execution

**Files:**
- Create: `src/issueflow/experiment_runner.py`
- Create: `scripts/run_experiment.py`
- Create: `tests/phase2/test_experiment_runner.py`

**Interfaces:**
- Consumes: schedule, `RunService`, `ExperimentStore`, `SpendLedger`, API settings.
- Produces: `run_trial(trial_id)`, `run_batch(limit<=20)`, and normalized terminal records.

- [ ] **Step 1: Write deterministic runner tests**

Test: one trial creates one fresh run; already complete trial is skipped; crash leaves `needs_attention`; batch rejects `limit>20`; spend denial starts no run; no API key is serialized; and `--dry-run` invokes no architecture.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/phase2/test_experiment_runner.py -q`

Expected: FAIL because runner is absent.

- [ ] **Step 3: Implement exact CLI modes**

```bash
# no paid calls
.venv/bin/python scripts/run_experiment.py --config experiments/configs/phase-2-formal.yaml --dry-run

# bounded paid batch
.venv/bin/python scripts/run_experiment.py --config experiments/configs/phase-2-formal.yaml --batch-size 20
```

For one explicit trial, pass `--trial` followed by an exact key copied from the committed `artifacts/phase-2/formal-plan.json`; the runner rejects keys that are not present in that file.

Reject a draft manifest, dirty config hash, missing API key, unqualified case, changed environment digest, or pending human gate. The CLI prints trial IDs, statuses, aggregate estimated spend, and next action; it never prints prompts, full model output, Authorization headers, or key values.

- [ ] **Step 4: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest tests/phase2/test_experiment_runner.py -q
git add src/issueflow/experiment_runner.py scripts/run_experiment.py tests/phase2/test_experiment_runner.py
git commit -m "feat: run bounded experiment batches"
```

---

### Task 6: Classify failures, link retries, and aggregate results

**Files:**
- Create: `src/issueflow/experiment_analysis.py`
- Create: `scripts/export_experiment.py`
- Create: `tests/phase2/test_experiment_analysis.py`

**Interfaces:**
- Consumes: Persisted run stop reasons and trial records.
- Produces: deterministic `classify_failure`, retry eligibility, CSV/JSON aggregates, and run-ID provenance.

- [ ] **Step 1: Write classification tests**

Infrastructure is limited to Docker daemon/image failure, workspace clone transport failure, HTTP connect timeout before a model response, HTTP 429, and HTTP 5xx. Map all known Agent stop reasons to the other eight categories. Unknown reasons produce `needs_attention` and block aggregation; they are not silently assigned.

- [ ] **Step 2: Write retry tests**

Only an infrastructure failure is retry eligible. A retry must name the failed trial ID, use the identical config hash/case/architecture/repetition, receive a new trial/run ID, and remain visible beside the original. Maximum one infrastructure retry per original trial in phase 2C; a second infrastructure failure remains reported.

- [ ] **Step 3: Verify RED**

Run: `.venv/bin/python -m pytest tests/phase2/test_experiment_analysis.py -q`

Expected: FAIL because analysis is absent.

- [ ] **Step 4: Implement aggregate rows**

Each architecture aggregate includes `functional_successes`, `valid_trials`, `infrastructure_failures`, `success_rate`, median/mean/range cost, token, wall-clock, tools, patches, role calls, route count, and success-per-CNY. Each cell's JSON contains contributing run IDs. CSV contains scalar summaries only; JSON is the provenance source.

- [ ] **Step 5: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest tests/phase2/test_experiment_analysis.py -q
git add src/issueflow/experiment_analysis.py scripts/export_experiment.py tests/phase2/test_experiment_analysis.py
git commit -m "feat: classify and aggregate experiment outcomes"
```

---

### Task 7: Run five-case calibration and freeze formal policy

**Files:**
- Create: `experiments/configs/phase-2-calibration.yaml`
- Create: `artifacts/phase-2/calibration-summary.json`
- Create: `docs/phase-2-calibration.md`
- Create: `experiments/configs/phase-2-formal.yaml`
- Modify: `docs/phase-2-progress.md`

**Interfaces:**
- Consumes: First five strict cases and production DeepSeek settings.
- Produces: 20 calibration trials, measured costs/failures, and a frozen formal manifest.

- [ ] **Step 1: Freeze and commit the calibration manifest before paid calls**

Create `phase-2-calibration.yaml` with the five accepted strict IDs, all four architectures, repetition 1, exact model/base URL/temperature, prompt/catalog/environment/budget hashes, conversion 7.50, CNY 30 calibration limit, and `status: frozen`. Run all phase-one/phase-two tests, strict five-case three-replay qualification, dry-run matrix, diff check, and credential scan. Record the commit SHA and Docker digests, then commit the manifest with `git commit -m "docs: freeze phase two calibration"` before any paid call.

- [ ] **Step 2: User paid-run gate**

Show maximum possible calibration cost, current total phase-two spend, 20 planned trial keys, model name, config hash, and test evidence. Start only after explicit user approval.

- [ ] **Step 3: Run calibration in batches of at most five**

After each batch, inspect spend, infrastructure errors, unclassified failures, and trace completeness. Stop immediately if estimated/actual calibration spend reaches CNY 30 or any secret scan fails.

- [ ] **Step 4: Analyze without tuning to winners**

Use calibration to correct protocol bugs, environment failures, or budgets that prevent every architecture from completing a meaningful attempt. Do not choose architecture-specific budgets or remove difficult cases. Any code/config change invalidates previous calibration and requires rerunning affected trials under a new calibration manifest ID.

- [ ] **Step 5: Freeze formal config**

Set all 20 strict IDs, exact model/base URL/temperature, prompt version hashes, catalog hash, environment digests, budget-profile hash, conversion 7.50, retry rules, architecture order, and `status: frozen`. Commit before formal runs.

- [ ] **Step 6: Commit redacted evidence**

```bash
git add experiments/configs/phase-2-calibration.yaml experiments/configs/phase-2-formal.yaml artifacts/phase-2/calibration-summary.json docs/phase-2-calibration.md docs/phase-2-progress.md
git commit -m "docs: freeze phase two formal experiment"
```

---

### Task 8: Execute the 80-trial first formal round

**Files:**
- Create: `artifacts/phase-2/formal-plan.json`
- Create: `artifacts/phase-2/round-1-summary.json`
- Create: `docs/phase-2-round-1-log.md`
- Modify: `docs/phase-2-progress.md`

**Interfaces:**
- Consumes: Frozen formal manifest.
- Produces: 80 terminal planned trials plus explicitly linked eligible retries.

- [ ] **Step 1: Verify the frozen matrix**

Dry-run must show 80 unique trial keys, 20 per architecture, all 20 strict cases, config hash equal to the committed manifest, and worst-case category spend at or below CNY 105.

- [ ] **Step 2: Run four batches of 20**

Before each batch, require a human gate. After each batch, record estimated spend, provider delta, success count, infrastructure failures, unclassified reasons, and credential scan. Do not change manifest or code between batches; if a correctness bug is found, stop and invalidate/restart the formal experiment under `phase-2-formal-v2`.

- [ ] **Step 3: Review infrastructure retries**

Create at most one explicit retry for each eligible infrastructure failure. Keep original records in all reports. Charge retries to the CNY 25 retry/Reviewer bucket.

- [ ] **Step 4: Verify round-one integrity**

Export JSON and assert 80 planned trial keys are terminal, no duplicate, all config hashes identical, every valid trial has a run ID, and every scalar aggregate lists source run IDs.

- [ ] **Step 5: Commit aggregate evidence only**

Do not commit the live SQLite database or unredacted model payloads.

```bash
git add artifacts/phase-2/formal-plan.json artifacts/phase-2/round-1-summary.json docs/phase-2-round-1-log.md docs/phase-2-progress.md
git commit -m "data: record phase two first experiment round"
```

---

### Task 9: Select ten cases and execute 80 repeat trials

**Files:**
- Create: `scripts/select_repeat_cases.py`
- Create: `tests/phase2/test_repeat_selection.py`
- Create: `artifacts/phase-2/repeat-selection.json`
- Create: `artifacts/phase-2/round-2-summary.json`
- Create: `docs/phase-2-round-2-log.md`
- Modify: `docs/phase-2-progress.md`

**Interfaces:**
- Consumes: Round-one results and frozen case metadata.
- Produces: Exactly 10 selected case IDs and repetitions 2/3 for all four architectures.

- [ ] **Step 1: Encode the selection rule before reading architecture rankings**

Score each case: +4 if architecture functional results disagree; +2 if difficulty is medium/large; +2 if it represents an issue category with fewer than four strict cases; +1 if any infrastructure failure occurred. Sort descending by `(score, sha256(formal_config_hash + case_id))` and take exactly 10. Do not use architecture name or cost ranking as a tie-breaker.

- [ ] **Step 2: Write and pass deterministic selection tests**

Assert exactly 10 unique strict IDs, identical output for shuffled input, no architecture-dependent tie-break, and an output file containing scores/reasons/config hash.

- [ ] **Step 3: Freeze the 80-trial repeat schedule**

Generate 10 cases × 4 architectures × repetitions 2 and 3. Assert no collision with round-one keys and worst-case repeat spend at or below CNY 80.

- [ ] **Step 4: User gate and four batches of 20**

Apply the same preflight, human gate, provider-delta, infrastructure retry, and no-code-change rules as Task 8.

- [ ] **Step 5: Verify and commit aggregates**

```bash
git add scripts/select_repeat_cases.py tests/phase2/test_repeat_selection.py artifacts/phase-2/repeat-selection.json artifacts/phase-2/round-2-summary.json docs/phase-2-round-2-log.md docs/phase-2-progress.md
git commit -m "data: record phase two repeat experiments"
```

---

### Task 10: Close experiment integrity and hand off to reporting

**Files:**
- Create: `scripts/verify_experiment.py`
- Create: `tests/phase2/test_verify_experiment.py`
- Create: `artifacts/phase-2/experiment-summary.json`
- Create: `artifacts/phase-2/trials.csv`
- Create: `docs/phase-2-experiment-integrity.md`
- Modify: `docs/phase-2-progress.md`

**Interfaces:**
- Consumes: Both formal rounds, retry links, spend ledger, and source runs.
- Produces: Reproducible final aggregate and a machine-checkable 2C exit gate.

- [ ] **Step 1: Write verifier tests against corrupt fixtures**

Reject missing/duplicate trial keys, mixed config hashes, wrong repetition counts, non-strict case IDs, unclassified terminal outcomes, invalid retry links, aggregate cells without run IDs, CNY spend over 300, or secret values.

- [ ] **Step 2: Verify RED, implement CLI, then GREEN**

```bash
.venv/bin/python scripts/verify_experiment.py --config experiments/configs/phase-2-formal.yaml
```

Expected final output: `round1=80/80 repeats=80/80 failures_classified=PASS provenance=PASS spend<=CNY300 credential_scan=PASS`.

- [ ] **Step 3: Rebuild final exports from SQLite**

Delete generated summary/CSV in a temporary copy, rerun export, and byte-compare normalized JSON/CSV to committed artifacts. Manual spreadsheet edits are prohibited.

- [ ] **Step 4: Run full verification**

```bash
make verify-phase-1
make test-phase-2
.venv/bin/python scripts/qualify_benchmarks.py --catalog benchmarks/catalogs/strict.yaml --environments benchmarks/environments.yaml --replays 3
.venv/bin/python scripts/verify_experiment.py --config experiments/configs/phase-2-formal.yaml
git diff --check
```

- [ ] **Step 5: Update progress and commit**

Set 2C to `10/10`, `160/160 trials` plus retry count, actual CNY spend, and `Complete`.

```bash
git add scripts/verify_experiment.py tests/phase2/test_verify_experiment.py artifacts/phase-2/experiment-summary.json artifacts/phase-2/trials.csv docs/phase-2-experiment-integrity.md docs/phase-2-progress.md
git commit -m "test: verify phase two experiment integrity"
```
