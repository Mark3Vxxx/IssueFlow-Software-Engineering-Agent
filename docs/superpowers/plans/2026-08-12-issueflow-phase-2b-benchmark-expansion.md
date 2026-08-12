# IssueFlow Phase 2B Benchmark Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build 20 new strict historical repair cases across 3–4 qualified deep-learning repositories plus at least 10 separately reported exploratory cases, with hidden tests, no answer leakage, and three clean reference replays per strict case.

**Architecture:** Generalize the phase-one single-catalog schema into compatibility/strict/exploratory catalogs and repository-specific Docker environments. Keep hidden tests in a validator-only tree mounted only after Agent execution; a qualification service records provenance, stability, leakage, and reference replay evidence before a case can enter the strict catalog.

**Tech Stack:** Python 3.11+, Pydantic 2, PyYAML, Git, Docker, pytest, Ruff, SQLite-compatible JSON evidence.

## Global Constraints

- Create exactly 20 new strict cases; none of the five phase-one micrograd compatibility cases counts toward 20.
- Create at least 10 exploratory cases and never include them in strict success-rate denominators.
- Final strict cases must span 3–4 repositories; each selected repository contributes at least 3 and at most 8 strict cases.
- Strict cases are historical upstream repairs, not constructed regressions.
- Every strict revision is a full lowercase 40-character SHA and every source/ref patch has an authoritative upstream URL and license record.
- Every strict case fails before repair, passes public and hidden verification after the reference patch, and does so in three fresh workspaces.
- Hidden tests and reference patches remain outside the Agent-visible workspace; only the outer validator mounts hidden tests.
- Default Agent-visible Git history ends at the faulty revision; post-fault commits are not fetched into its worktree.
- Repository environments are CPU-only, default-deny network, version-pinned, reproducible on Apple Silicon, and use no private data or credentials.
- Candidate repositories that fail environment, license, stability, leakage, or case-count gates are rejected or moved to exploratory with a recorded reason.
- Do not lower a strict gate to reach the target count; use the documented fallback repository order.

---

### Task 1: Generalize catalog metadata and dataset splits

**Files:**
- Modify: `src/issueflow/models.py`
- Modify: `src/issueflow/benchmark.py`
- Create: `benchmarks/catalogs/compatibility.yaml`
- Create: `benchmarks/catalogs/strict.yaml`
- Create: `benchmarks/catalogs/exploratory.yaml`
- Modify: `tests/test_models.py`
- Modify: `tests/test_benchmark.py`
- Create: `tests/phase2/test_benchmark_splits.py`
- Modify: `src/issueflow/ui.py`
- Modify: `src/issueflow/architectures/direct.py`
- Modify: `src/issueflow/architectures/roles.py`

**Interfaces:**
- Consumes: Existing `BenchmarkCase` and `load_catalog`.
- Produces: `DatasetSplit`, `AgentCaseView`, strict-only hidden metadata, `load_catalogs(paths)`, and a migrated compatibility catalog.

- [ ] **Step 1: Write failing split and strict-validation tests**

```python
def test_strict_case_requires_hidden_validation():
    values = valid_case(dataset_split="strict", hidden_test_path=None)
    with pytest.raises(ValidationError, match="strict cases require hidden validation"):
        BenchmarkCase(**values)


def test_compatibility_case_does_not_require_hidden_validation():
    case = BenchmarkCase(**valid_case(dataset_split="compatibility"))
    assert case.hidden_test_path is None


def test_load_catalogs_rejects_duplicate_ids_across_splits(tmp_path):
    with pytest.raises(ValueError, match="catalog case IDs must be globally unique"):
        load_catalogs([strict_path, exploratory_path])
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/phase2/test_benchmark_splits.py tests/test_models.py tests/test_benchmark.py -q`

Expected: FAIL because split and hidden fields do not exist and the loader enforces the phase-one 1+4 mix.

- [ ] **Step 3: Add exact metadata fields**

```python
class DatasetSplit(StrEnum):
    COMPATIBILITY = "compatibility"
    STRICT = "strict"
    EXPLORATORY = "exploratory"


class BenchmarkCase(BaseModel):
    id: str
    dataset_split: DatasetSplit
    repository_id: str
    environment_id: str
    kind: Literal["historical", "constructed"]
    # keep existing fields, including verify_command as the public verification command
    hidden_test_path: str | None = None
    hidden_verify_command: str | None = None
    fixed_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    difficulty: Literal["small", "medium", "large"]
    issue_category: Literal[
        "data_config", "model_training", "test_boundary", "numerical", "performance", "refactor"
    ]
```

Validation rules: strict requires `kind="historical"`, both hidden fields, a full `fixed_revision`, a non-empty `source_url`, and no `fault_patch`; compatibility accepts existing historical/constructed cases; exploratory records missing strict gates in `construction_notes`.

- [ ] **Step 4: Replace the hard-coded mix loader**

```python
def load_catalog(path: Path) -> dict[str, BenchmarkCase]: ...


def load_catalogs(paths: Sequence[Path]) -> dict[str, BenchmarkCase]: ...
```

Each file declares top-level `dataset_split`; each item must match it. Preserve YAML order and reject empty catalogs, duplicate IDs, or cross-file duplicates. Remove the 1 historical/4 constructed rule from generic loading; assert that exact mix only in the compatibility-catalog test.

Add `AgentCaseView` with only `id`, `repository_id`, `issue`, `reproduce_command`, and `verify_command`. `BenchmarkCase.agent_view()` returns that model. Direct and every production role build prompts only from `AgentCaseView` plus tool observations. Add a serialization test proving `source_url`, `revision`, `fixed_revision`, `reference_patch`, `hidden_test_path`, `hidden_verify_command`, and `construction_notes` cannot appear in Agent prompts or LangGraph state.

- [ ] **Step 5: Migrate phase-one cases**

Move the five entries from `benchmarks/micrograd.yaml` into `benchmarks/catalogs/compatibility.yaml`; add `dataset_split: compatibility`, `repository_id: micrograd`, `environment_id: micrograd`, difficulty equal to the existing budget profile, and an appropriate issue category. Keep a one-release compatibility shim: `benchmarks/micrograd.yaml` contains a comment and the same five entries, but production/UI/Makefile use the new path; delete the shim only in phase three.

- [ ] **Step 6: Verify GREEN**

Run the tests from Step 2 and `make verify-benchmarks` after updating its catalog path.

Expected: all tests PASS and compatibility remains 5/5.

- [ ] **Step 7: Commit**

```bash
git add src/issueflow/models.py src/issueflow/benchmark.py src/issueflow/ui.py src/issueflow/architectures/direct.py src/issueflow/architectures/roles.py benchmarks/catalogs benchmarks/micrograd.yaml tests/test_models.py tests/test_benchmark.py tests/phase2/test_benchmark_splits.py Makefile
git commit -m "feat: split benchmark catalogs by evidence quality"
```

---

### Task 2: Add repository-specific environment registry

**Files:**
- Create: `src/issueflow/environment.py`
- Create: `benchmarks/environments.yaml`
- Modify: `src/issueflow/sandbox.py`
- Modify: `src/issueflow/run_service.py`
- Modify: `src/issueflow/architectures/factory.py`
- Create: `docker/Dockerfile.mingpt`
- Create: `docker/Dockerfile.nanogpt`
- Create: `docker/Dockerfile.build-nanogpt`
- Create: `docker/Dockerfile.tinygrad`
- Modify: `tests/test_sandbox.py`
- Create: `tests/phase2/test_environments.py`

**Interfaces:**
- Consumes: `BenchmarkCase.environment_id`.
- Produces: `EnvironmentSpec`, `load_environments(path)`, and `SandboxFactory.for_case(case)`.

- [ ] **Step 1: Write registry and image-selection tests**

```python
def test_environment_registry_resolves_pinned_image(tmp_path):
    registry = load_environments(path)
    assert registry["mingpt"].image == "issueflow-mingpt:phase2"
    assert registry["mingpt"].dockerfile == "docker/Dockerfile.mingpt"


def test_sandbox_factory_rejects_unknown_environment(case):
    with pytest.raises(KeyError, match="unknown environment"):
        factory.for_case(case.model_copy(update={"environment_id": "missing"}))
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/phase2/test_environments.py tests/test_sandbox.py -q`

Expected: FAIL because Docker uses one global image.

- [ ] **Step 3: Implement registry and factory**

```python
class EnvironmentSpec(BaseModel):
    id: str
    image: str
    dockerfile: str
    python_version: str
    lock_digest: str


class SandboxFactory:
    def for_case(self, case: BenchmarkCase) -> DockerSandbox: ...
```

Change `DockerSandbox` to accept `image_name` in its constructor. Change the architecture factory signature to `create(kind, case, workspace, sandbox) -> ArchitectureRunner`. `RunService` asks `SandboxFactory` once per run and passes that same sandbox instance to the architecture/tools, public verification, and hidden verifier.

- [ ] **Step 4: Define four primary CPU environments**

`benchmarks/environments.yaml` contains `micrograd`, `mingpt`, `nanogpt`, `build-nanogpt`, and `tinygrad`. Each Dockerfile starts from `python:3.12-slim`, installs pinned CPU wheels and repository test requirements discovered during qualification, uses `/workspace`, and contains no repository source, API key, network credential, or mutable `latest` dependency. Update the `lock_digest` with the SHA-256 of the normalized installed-package lock emitted by the build task.

- [ ] **Step 5: Build and smoke-test images**

Run each image with `python --version` and `python -c "import torch; print(torch.__version__)"`; the command must work with `--network none` after build. If a primary repository cannot run on Python 3.12, document the exact blocker and use a per-environment Python 3.11 image rather than relaxing the project minimum.

- [ ] **Step 6: Verify GREEN and commit**

Run focused tests plus `make verify-phase-1`, then:

```bash
git add src/issueflow/environment.py src/issueflow/sandbox.py src/issueflow/run_service.py src/issueflow/architectures/factory.py benchmarks/environments.yaml docker tests/test_sandbox.py tests/phase2/test_environments.py
git commit -m "feat: select pinned benchmark environments"
```

---

### Task 3: Isolate hidden verification from Agent workspaces

**Files:**
- Create: `src/issueflow/hidden_validation.py`
- Modify: `src/issueflow/sandbox.py`
- Modify: `src/issueflow/run_service.py`
- Create: `benchmarks/hidden/.gitkeep`
- Create: `tests/phase2/test_hidden_validation.py`
- Modify: `tests/test_run_service.py`

**Interfaces:**
- Consumes: Strict-case `hidden_test_path` and `hidden_verify_command`.
- Produces: `HiddenVerifier.verify(case, workspace, sandbox, timeout_seconds) -> SandboxResult`.

- [ ] **Step 1: Write no-leakage and execution tests**

```python
def test_hidden_test_is_absent_from_agent_workspace(prepared_strict_case):
    assert not any("hidden" in path.parts for path in prepared_strict_case.workspace.rglob("*"))


def test_hidden_verifier_mounts_one_read_only_validator(...):
    command = build_hidden_docker_command(...)
    assert any("/issueflow-hidden/test_hidden.py:ro" in item for item in command)
    assert not any(str(hidden_path.parent) + ":/workspace" in item for item in command)
```

Also assert `ToolExecutor.run_tests` rejects `hidden_verify_command` because it is not one of the Agent-visible commands.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/phase2/test_hidden_validation.py tests/test_run_service.py -q`

Expected: FAIL because hidden verification is absent.

- [ ] **Step 3: Implement validator-only mount**

Resolve `hidden_test_path` relative to `benchmarks/hidden`, reject traversal/symlinks outside that root, mount exactly the resolved file at `/issueflow-hidden/test_hidden.py:ro`, and execute `hidden_verify_command` only after Agent execution and public independent verification. The hidden command is catalog-owned and never included in prompts, `ToolExecutor.test_commands`, graph state, or Agent trace input summaries.

Change `GitWorkspacePreparer` to create an empty repository, add the approved remote, fetch only `case.revision` with `--depth 1`, and check out `FETCH_HEAD`; do not perform a full clone. Tests must prove `git rev-list --all` contains the one fetched baseline commit (plus the local constructed fault-baseline commit for compatibility cases) and does not contain the documented fixed SHA or later remote history.

- [ ] **Step 4: Extend deterministic success gates**

Functional success now requires reproduction failure, non-empty diff, public verification success, hidden verification success for strict cases, and no exhausted budget. Compatibility cases have no hidden gate. Persist a `hidden_verification` trace step whose input is the stable label `validator-only hidden test`, not the host path or test source.

- [ ] **Step 5: Verify GREEN and commit**

Run focused tests plus the deterministic phase-two E2E suite, then:

```bash
git add src/issueflow/hidden_validation.py src/issueflow/sandbox.py src/issueflow/run_service.py benchmarks/hidden/.gitkeep tests/phase2/test_hidden_validation.py tests/test_run_service.py
git commit -m "feat: isolate hidden benchmark validation"
```

---

### Task 4: Build the qualification and three-replay validator

**Files:**
- Create: `src/issueflow/benchmark_validation.py`
- Create: `scripts/qualify_benchmarks.py`
- Modify: `scripts/verify_benchmarks.py`
- Create: `tests/phase2/test_benchmark_qualification.py`
- Create: `benchmarks/qualification/.gitkeep`

**Interfaces:**
- Consumes: Catalog, environment registry, reference patches, hidden tests.
- Produces: `QualificationResult`, `qualify_case(case, replays)`, CLI JSON evidence, and strict/exploratory exit codes.

- [ ] **Step 1: Write qualification tests**

Use local Git fixtures to prove strict acceptance only when all three clean replays report `FAIL_AS_EXPECTED`, reference patch applies, public verification passes, hidden verification passes, workspace has no answer files, and source/license fields are non-empty. Test normalized rejection reasons: `license_missing`, `reproduction_unstable`, `reference_patch_failed`, `public_verification_failed`, `hidden_verification_failed`, `answer_leakage`, and `environment_failed`.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/phase2/test_benchmark_qualification.py -q`

Expected: FAIL because no qualification service exists.

- [ ] **Step 3: Implement immutable result schema**

```python
class ReplayResult(BaseModel):
    replay: PositiveInt
    reproduction: Literal["FAIL_AS_EXPECTED", "UNEXPECTED_PASS", "ERROR"]
    public_verification: Literal["PASS", "FAIL", "NOT_RUN"]
    hidden_verification: Literal["PASS", "FAIL", "NOT_REQUIRED", "NOT_RUN"]


class QualificationResult(BaseModel):
    case_id: str
    accepted_split: DatasetSplit | None
    reasons: list[str]
    replays: list[ReplayResult]
    environment_id: str
    revision: str
    reference_patch_sha256: str
```

Write one redacted JSON file per case under `benchmarks/qualification/<case-id>.json`; never include hidden source or host paths.

- [ ] **Step 4: Implement CLI semantics**

```bash
.venv/bin/python scripts/qualify_benchmarks.py \
  --catalog benchmarks/catalogs/strict.yaml \
  --environments benchmarks/environments.yaml \
  --replays 3
```

Exit 0 only if every strict case qualifies; exit 1 for case rejection; exit 2 for invalid catalog/configuration. `verify_benchmarks.py` remains the one-replay compatibility check and delegates shared logic instead of duplicating Git/Docker code.

- [ ] **Step 5: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest tests/phase2/test_benchmark_qualification.py -q
git add src/issueflow/benchmark_validation.py scripts/qualify_benchmarks.py scripts/verify_benchmarks.py tests/phase2/test_benchmark_qualification.py benchmarks/qualification/.gitkeep
git commit -m "feat: qualify strict benchmarks with three replays"
```

---

### Task 5: Run the repository candidate funnel and freeze 3–4 repositories

**Files:**
- Create: `benchmarks/candidates.yaml`
- Create: `docs/phase-2-repository-qualification.md`
- Create: `scripts/inspect_repository_candidates.py`
- Create: `tests/phase2/test_candidate_catalog.py`
- Modify: `benchmarks/environments.yaml`

**Interfaces:**
- Consumes: Public Git repositories and Task 4 qualification criteria.
- Produces: An approved 3–4-repository set, rejected-candidate reasons, and target case quotas totaling 20.

- [ ] **Step 1: Encode the fixed candidate order**

Primary order:

1. `karpathy/minGPT` — `https://github.com/karpathy/minGPT`, MIT, target 5.
2. `karpathy/nanoGPT` — `https://github.com/karpathy/nanoGPT`, MIT, target 5.
3. `karpathy/build-nanogpt` — `https://github.com/karpathy/build-nanogpt`, MIT, target 4.
4. `tinygrad/tinygrad` — `https://github.com/tinygrad/tinygrad`, MIT, target 6, using only a historical revision whose checked-out core Python tree is at most 10,000 nonblank lines.

Fallback order if a primary yields fewer than three strict cases: `karpathy/nanochat` (MIT), then `karpathy/makemore` (MIT). A fallback must independently pass the same license, CPU environment, ≤10,000-core-line, stability, and minimum-three-case gates. No selected repository may contribute more than eight strict cases. `minitorch/minitorch` is not in the fixed funnel because its repository license evidence was not confirmed during plan writing; it may enter only through a separately reviewed plan change.

- [ ] **Step 2: Write and run metadata tests**

Assert every candidate has exact owner/name, HTTPS URL, expected license, priority, target quota, and official license/source URL. Total primary target quota is 20.

- [ ] **Step 3: Implement read-only inspection**

The script clones a mirror into `.issueflow/candidate-cache`, reports license file SHA, Python nonblank lines at candidate revisions, number of commits touching `.py`, number of linked issues/PR URLs recorded by the researcher, test entrypoints, and whether a CPU/offline smoke command exists. It does not mutate upstream or write accepted catalogs.

- [ ] **Step 4: Apply deterministic selection gates**

Select a repository only if: license is exactly MIT, Apache-2.0, or BSD-3-Clause and matches the upstream license file; one pinned CPU Docker environment builds; at least three candidate historical repairs have a failing reproduction; the Agent-visible checkout can exclude post-fault history; and each reproduction completes in 120 seconds. If a fallback is selected, add and smoke-test its pinned Dockerfile and environment-registry entry before approval. Freeze 3–4 selected repositories and exact quotas totaling 20 in `docs/phase-2-repository-qualification.md` before constructing strict case packages.

- [ ] **Step 5: User review gate**

Show the license URL, line count, environment smoke output, candidate repair count, selected quota, and rejection reasons. Do not proceed to paid runs or accept strict cases until the user approves the selected repositories.

- [ ] **Step 6: Commit**

```bash
git add benchmarks/candidates.yaml benchmarks/environments.yaml docs/phase-2-repository-qualification.md scripts/inspect_repository_candidates.py tests/phase2/test_candidate_catalog.py
git commit -m "docs: freeze phase two benchmark repositories"
```

---

### Task 6: Accept the first five strict cases as a vertical slice

**Files:**
- Modify: `benchmarks/catalogs/strict.yaml`
- Create: five accepted case directories under `benchmarks/cases/`, each containing `reference.patch`
- Create: five matching directories under `benchmarks/hidden/`, each containing `test_hidden.py`
- Create: five matching qualification JSON files under `benchmarks/qualification/`
- Create: five matching case notes under `docs/benchmark-notes/`
- Modify: `tests/phase2/test_benchmark_qualification.py`

**Interfaces:**
- Consumes: Approved repositories and qualification runner.
- Produces: Five fully qualified strict historical cases used for four-architecture calibration.

- [ ] **Step 1: Package each case with a fixed naming rule**

IDs are `<repository-id>-hNN`, starting at `h01` within each repository. Each case note records source issue/PR/commit, faulty SHA, fixed SHA, license URL, affected files, public reproduction, hidden-test rationale, reference patch SHA-256, difficulty, category, budget profile, and why post-fault history is unavailable to the Agent.

- [ ] **Step 2: Write public reproduction before extracting the reference patch**

On the faulty SHA, demonstrate a stable nonzero exit in a clean container. The public test exposes the reported behavior but not the complete fix. Save command and redacted output in the case note.

- [ ] **Step 3: Write a hidden test with a distinct assertion**

The hidden test must exercise a different input, boundary, shape, dtype, or state transition than the public reproduction. Explain in the note which shortcut fix it rejects. It imports code from `/workspace` and uses no network or external data.

- [ ] **Step 4: Extract and minimize the authoritative reference patch**

Generate from the upstream fixed commit, preserve only the actual fix and required tests, and record SHA-256. Do not hand-author a different “equivalent” patch for a historical case.

- [ ] **Step 5: Run the gate three times**

Run qualification with `--replays 3`; all five must be accepted. Then run a leakage scan proving case workspaces and model-visible catalog fields contain neither reference patch content nor hidden test content.

- [ ] **Step 6: Human learning gate**

The user explains all five bugs and the hidden-test rationale for at least two without viewing the reference patch.

- [ ] **Step 7: Commit**

Stage only the five accepted packages, notes, qualification JSON, strict catalog entries, and tests:

```bash
git add benchmarks/catalogs/strict.yaml benchmarks/cases benchmarks/hidden benchmarks/qualification docs/benchmark-notes tests/phase2/test_benchmark_qualification.py
git commit -m "data: add first strict benchmark batch"
```

---

### Task 7: Expand the strict set from five to twenty

**Files:**
- Modify: `benchmarks/catalogs/strict.yaml`
- Create: 15 additional `benchmarks/cases/<case-id>/reference.patch`
- Create: 15 additional `benchmarks/hidden/<case-id>/test_hidden.py`
- Create: 15 additional `benchmarks/qualification/<case-id>.json`
- Create: 15 additional `docs/benchmark-notes/<case-id>.md`
- Create: `tests/phase2/test_strict_catalog_inventory.py`

**Interfaces:**
- Consumes: The same package/gate from Task 6.
- Produces: Exactly 20 qualified strict cases over 3–4 repositories.

- [ ] **Step 1: Write the inventory test before adding cases**

Assert: 20 cases; all `dataset_split=strict` and `kind=historical`; 3–4 unique repository IDs; each repository contributes 3–8; all IDs match `[a-z0-9-]+-h[0-9]{2}`; each source URL is unique; each has unique faulty SHA + reference patch pair; category distribution includes at least three categories; difficulty includes at least five `medium` or `large` cases.

- [ ] **Step 2: Verify RED at five cases**

Run: `.venv/bin/python -m pytest tests/phase2/test_strict_catalog_inventory.py -q`

Expected: FAIL reporting 5 instead of 20.

- [ ] **Step 3: Add batch two (cases 6–10)**

For each case, record the authoritative source and faulty/fixed SHAs; demonstrate the public reproduction on the faulty SHA; add a validator-only hidden assertion using a distinct input/boundary/shape/dtype/state transition; extract the upstream reference patch; scan the Agent view/workspace for hidden or patch content; and require three clean `FAIL_AS_EXPECTED → public PASS → hidden PASS` replays. Review all five qualification JSON files, then commit `data: add second strict benchmark batch`.

- [ ] **Step 4: Add batch three (cases 11–15)**

For each case, record the authoritative source and faulty/fixed SHAs; demonstrate the public reproduction on the faulty SHA; add a validator-only hidden assertion using a distinct input/boundary/shape/dtype/state transition; extract the upstream reference patch; scan the Agent view/workspace for hidden or patch content; and require three clean `FAIL_AS_EXPECTED → public PASS → hidden PASS` replays. Review all five qualification JSON files, then commit `data: add third strict benchmark batch`.

- [ ] **Step 5: Add batch four (cases 16–20)**

For each case, record the authoritative source and faulty/fixed SHAs; demonstrate the public reproduction on the faulty SHA; add a validator-only hidden assertion using a distinct input/boundary/shape/dtype/state transition; extract the upstream reference patch; scan the Agent view/workspace for hidden or patch content; and require three clean `FAIL_AS_EXPECTED → public PASS → hidden PASS` replays. Review all five qualification JSON files, then commit `data: complete strict benchmark set`.

- [ ] **Step 6: Verify GREEN and full replay**

Run inventory test and:

```bash
.venv/bin/python scripts/qualify_benchmarks.py --catalog benchmarks/catalogs/strict.yaml --environments benchmarks/environments.yaml --replays 3
```

Expected: 20/20 accepted, 60/60 clean reference replays.

- [ ] **Step 7: Human learning gate**

The user explains at least six representative bugs and at least three hidden tests. Record concise explanations in `docs/phase-2-benchmark-learning.md`.

---

### Task 8: Build the exploratory set and close 2B

**Files:**
- Modify: `benchmarks/catalogs/exploratory.yaml`
- Create: `docs/phase-2-benchmark-evaluation.md`
- Create: `docs/phase-2-benchmark-learning.md`
- Create: `tests/phase2/test_exploratory_catalog_inventory.py`
- Modify: `docs/phase-2-progress.md`
- Modify: `README.md`
- Modify: `README.zh-CN.md`

**Interfaces:**
- Consumes: Rejected or partially qualified candidates with useful evidence.
- Produces: At least 10 exploratory entries and the final 2B evaluation.

- [ ] **Step 1: Write exploratory inventory tests**

Require at least 10 unique entries, `dataset_split=exploratory`, a non-empty `construction_notes` field naming every failed strict gate, provenance/license fields, and no overlap with strict source URLs or faulty revisions. Do not require hidden validation or three stable replays.

- [ ] **Step 2: Verify RED and add entries**

Run the inventory test, then add candidates rejected for documented reasons such as missing hidden oracle, unstable dependency, long CPU runtime, incomplete Issue, or partial reproduction. Do not invent failures or constructed regressions merely to reach 10.

- [ ] **Step 3: Generate the 2B evaluation**

Report selected repositories, quotas, 20 strict case matrix, 60 replay outcomes, category/difficulty distribution, 10+ exploratory reasons, rejected repositories, environment digests, and known limitations. Show strict and exploratory counts separately.

- [ ] **Step 4: Run full 2B verification**

```bash
make verify-phase-1
make test-phase-2
.venv/bin/python scripts/qualify_benchmarks.py --catalog benchmarks/catalogs/strict.yaml --environments benchmarks/environments.yaml --replays 3
git diff --check
```

Expected: phase one PASS, phase two tests PASS, strict 20/20 and 60/60 replays PASS, exploratory count at least 10.

- [ ] **Step 5: Credential and leakage scans**

Assert the actual API key value is absent without printing it; assert every hidden test/reference patch is outside all prepared Agent workspaces; assert post-fault Git objects are unavailable from those workspaces.

- [ ] **Step 6: Update progress and commit**

Set 2B to `8/8`, verification `20/20 strict × 3 PASS`, paid spend to the actual value (normally CNY 0), and status `Complete`.

```bash
git add benchmarks/catalogs/exploratory.yaml docs/phase-2-benchmark-evaluation.md docs/phase-2-benchmark-learning.md tests/phase2/test_exploratory_catalog_inventory.py docs/phase-2-progress.md README.md README.zh-CN.md
git commit -m "docs: complete phase two benchmark set"
```
