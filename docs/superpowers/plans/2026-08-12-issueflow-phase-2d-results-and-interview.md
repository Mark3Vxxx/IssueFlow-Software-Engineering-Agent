# IssueFlow Phase 2D Results and Interview Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn phase-two evidence into reproducible bilingual results, honest case studies, an interview review package, and a bounded phase-three handoff without changing experiment outcomes.

**Architecture:** Generate every table and chart from verified aggregate JSON and trial provenance. Documentation links claims to run IDs/config hashes, separates deterministic functionality from advisory review, and records limitations and negative results before producing concise Demo and interview narratives.

**Tech Stack:** Python 3.11+, standard library, matplotlib 3.x for deterministic SVG charts, Markdown, pytest, Ruff, existing JSON/SQLite evidence.

## Global Constraints

- Do not rerun or alter formal phase-two trials during reporting.
- Do not edit aggregate numbers by hand; regenerate from verified JSON/SQLite sources.
- Every reported percentage includes numerator and denominator.
- Strict, exploratory, compatibility, infrastructure-failed, and retried runs remain distinguishable.
- Functional correctness comes from reproduction, non-empty diff, public test, hidden test, and budget gates; Reviewer is advisory.
- Report negative, tied, or higher-cost multi-Agent results as observed.
- Do not claim general superiority from 20 strict cases or from one model/provider.
- Charts must include accessible text labels and source-data JSON/CSV.
- Published artifacts are redacted and must not include API keys, raw Authorization headers, hidden test source, reference-patch answers inside prompts, or unredacted model payloads.
- Stage-three GitHub PR automation, full dashboard, video, and production recovery remain out of scope.

---

### Task 1: Preserve the newly verified historical phase-one run

**Files:**
- Create: `artifacts/phase-1/historical-01-live-run.json`
- Modify: `docs/phase-1-evaluation.md`
- Modify: `tests/test_e2e_smoke.py`
- Modify: `README.md`
- Modify: `README.zh-CN.md`

**Interfaces:**
- Consumes: Local run `run-59dba1b5ebad4fc3844840d72344bd72` from `.issueflow/issueflow.sqlite3`.
- Produces: A redacted historical-run artifact and corrected phase-one evidence summary.

- [ ] **Step 1: Write the failing recorded-evidence test**

Add a test loading `artifacts/phase-1/historical-01-live-run.json` and asserting:

```python
assert evidence["run"]["id"] == "run-59dba1b5ebad4fc3844840d72344bd72"
assert evidence["run"]["case_id"] == "historical-01"
assert evidence["run"]["status"] == "succeeded"
assert evidence["run"]["functional_success"] is True
assert evidence["run"]["review_status"] == "failed"
assert evidence["run"]["review_reasons"] == ["invalid_reviewer_response"]
assert len(evidence["steps"]) == 11
assert sum(step["input_tokens"] for step in evidence["steps"]) == 10_175
assert sum(step["output_tokens"] for step in evidence["steps"]) == 1_544
assert sum(step["cost_usd"] for step in evidence["steps"]) == 0.0008206856000000001
```

Also assert sequence is 1–11 and the actual environment key value is absent without printing it.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_e2e_smoke.py -q`

Expected: FAIL because the artifact is not present.

- [ ] **Step 3: Export through `TraceStore`**

Use `TraceStore.export_json_text` against the local database; do not query and hand-format rows. Scan the result for the actual API key and known secret patterns, then write only the redacted JSON artifact. If the run is unavailable, stop and ask the user to re-export from the original local database; do not fabricate it.

- [ ] **Step 4: Correct phase-one documentation**

Report historical-01 as a real functional success with 11 trace steps, 10,175/1,544 tokens, and estimated cost $0.0008206856. Explain that advisory Reviewer parsing failed but deterministic functionality remained successful. Keep the previous constructed-01 evidence; clearly state that two successful runs do not estimate general success rate.

- [ ] **Step 5: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest tests/test_e2e_smoke.py -q
git add artifacts/phase-1/historical-01-live-run.json docs/phase-1-evaluation.md tests/test_e2e_smoke.py README.md README.zh-CN.md
git commit -m "docs: record historical agent repair evidence"
```

---

### Task 2: Build deterministic phase-two tables and SVG charts

**Files:**
- Modify: `pyproject.toml`
- Create: `src/issueflow/reporting.py`
- Create: `scripts/build_phase2_report.py`
- Create: `tests/phase2/test_reporting.py`
- Create: `artifacts/phase-2/tables/architecture-summary.csv`
- Create: `artifacts/phase-2/charts/success-rate.svg`
- Create: `artifacts/phase-2/charts/cost-vs-success.svg`
- Create: `artifacts/phase-2/charts/failure-distribution.svg`

**Interfaces:**
- Consumes: `artifacts/phase-2/experiment-summary.json` and `artifacts/phase-2/trials.csv`.
- Produces: deterministic table rows, three SVG charts, and source/provenance captions.

- [ ] **Step 1: Write reporting tests with a small fixture**

Assert architecture order is Direct/Single/Fixed/Dynamic; success labels use `n/N (p%)`; median cost uses six USD decimals; infrastructure failures are excluded from the functional denominator but printed separately; charts contain title, axis labels, architecture names, and config hash; shuffled source rows generate byte-identical normalized CSV and semantically identical SVG.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/phase2/test_reporting.py -q`

Expected: FAIL because report generation is absent.

- [ ] **Step 3: Add reporting dependency**

Add `matplotlib>=3.9,<4` to the `dev` extra only. Set the `Agg` backend, fixed figure sizes, fixed colors, and embedded text; do not depend on a GUI.

- [ ] **Step 4: Implement report generation**

```python
def build_architecture_rows(summary: dict[str, object]) -> list[ArchitectureRow]: ...
def write_architecture_csv(rows: list[ArchitectureRow], path: Path) -> None: ...
def write_success_rate_svg(rows: list[ArchitectureRow], path: Path, config_hash: str) -> None: ...
def write_cost_success_svg(rows: list[ArchitectureRow], path: Path, config_hash: str) -> None: ...
def write_failure_svg(summary: dict[str, object], path: Path, config_hash: str) -> None: ...
```

The CLI refuses unverified input, reads no live API credentials, and writes only beneath `artifacts/phase-2/tables` and `artifacts/phase-2/charts`.

- [ ] **Step 5: Verify GREEN, regenerate twice, and commit**

Run generator twice and compare SHA-256 of table/chart files. Then:

```bash
git add pyproject.toml src/issueflow/reporting.py scripts/build_phase2_report.py tests/phase2/test_reporting.py artifacts/phase-2/tables artifacts/phase-2/charts
git commit -m "feat: generate phase two result visuals"
```

---

### Task 3: Write bilingual phase-two evaluation reports

**Files:**
- Create: `docs/phase-2-evaluation.md`
- Create: `docs/phase-2-evaluation.zh-CN.md`
- Create: `tests/phase2/test_phase2_report.py`

**Interfaces:**
- Consumes: Verified experiment summary, case qualification report, CSV, SVGs, run-ID provenance.
- Produces: English and Chinese reports with the same facts and section structure.

- [ ] **Step 1: Write report-content tests before reports**

Require both documents to contain: config hash; model/version; 20 strict and 10+ exploratory counts; 80 first-round and 80 repeat counts; actual CNY spend; four architecture rows with `n/N`; public vs hidden distinction; infrastructure/retry count; at least one success and one failure case study; limitations; and links to three charts and source JSON. Extract numeric tokens from both language reports and assert the key counts/costs match.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/phase2/test_phase2_report.py -q`

Expected: FAIL because reports are absent.

- [ ] **Step 3: Write reports from generated evidence**

Use this section order in both languages: scope; experimental controls; dataset; costs; primary results; role/process metrics; failures; case studies; threats to validity; reproducibility; conclusion. Include numerator/denominator in every success claim and state whether Dynamic/Fixed improved, tied, or regressed relative to Single exactly as observed.

- [ ] **Step 4: Verify links, numbers, and provenance**

For each table row and case study, verify referenced run IDs exist in experiment-summary provenance. Ensure exploratory examples are labeled and excluded from primary rates.

- [ ] **Step 5: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest tests/phase2/test_phase2_report.py -q
git add docs/phase-2-evaluation.md docs/phase-2-evaluation.zh-CN.md tests/phase2/test_phase2_report.py
git commit -m "docs: report phase two experiments"
```

---

### Task 4: Update project onboarding and Demo narrative

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/demo-script.md`
- Create: `docs/phase-2-demo-script.md`
- Modify: `tests/test_ui.py`

**Interfaces:**
- Consumes: Finished 2A–2C product and evidence.
- Produces: Accurate setup, verification, architecture explanation, and a three-minute phase-two Demo.

- [ ] **Step 1: Add documentation checks**

Assert READMEs mention the four architectures, 20 strict/10+ exploratory split, hidden validation, 300-CNY limit, phase-two verification commands, and links to both reports. Assert they do not say “guaranteed”, “always better”, or describe exploratory results as formal success.

- [ ] **Step 2: Update README architecture overview**

Keep phase-one quick start. Add a phase-two section explaining architecture selector, strict qualification, experiment reproducibility, artifact locations, cost gates, and the boundary between automated functional evidence and advisory review.

- [ ] **Step 3: Write the three-minute Demo**

Timing: 0:00–0:30 problem/phase one; 0:30–1:10 four architectures and LangGraph; 1:10–1:50 one strict case with hidden evidence; 1:50–2:30 result/cost chart; 2:30–3:00 limitation and phase-three direction. The script uses actual winning/losing observations, not an assumed Dynamic victory.

- [ ] **Step 4: Verify and commit**

```bash
.venv/bin/python -m pytest tests/test_ui.py tests/phase2/test_phase2_report.py -q
git add README.md README.zh-CN.md docs/demo-script.md docs/phase-2-demo-script.md tests/test_ui.py
git commit -m "docs: explain phase two workflow and demo"
```

---

### Task 5: Produce the interview review package

**Files:**
- Create: `docs/interview/phase-2-review.zh-CN.md`
- Create: `docs/interview/phase-2-question-bank.zh-CN.md`
- Create: `docs/interview/phase-2-two-minute-answers.zh-CN.md`
- Create: `tests/phase2/test_interview_material.py`

**Interfaces:**
- Consumes: Design decisions and measured results.
- Produces: Architecture review, question bank, and rehearsable answers grounded in evidence.

- [ ] **Step 1: Write completeness tests**

Require question coverage for: architecture contract; LangGraph State/Node/Edge; Fixed vs Dynamic; Supervisor guard; budget fairness; hidden tests; answer leakage; benchmark provenance; repeated runs; failure classification; SQLite vs checkpoints; Reviewer degradation; cost control; negative results; threats to validity; and next-stage tradeoffs.

- [ ] **Step 2: Write the architecture review**

For every 2A–2D module, state problem, inputs, outputs, dependencies, design choice, verification, failure mode, and likely interview follow-up. Include the final measured architecture numbers and exact run/config references.

- [ ] **Step 3: Write at least 30 questions with answer rubrics**

Split into basic, implementation, experimental-method, security, and challenge questions. Each answer rubric lists three required evidence points and one overclaim to avoid.

- [ ] **Step 4: Write concise rehearsable answers**

Provide two-minute answers for: project overview; why multi-Agent; why LangGraph; experiment fairness; benchmark credibility; main result; failure lesson; and stage-three plan. Use actual results and limitations.

- [ ] **Step 5: User rehearsal gate**

Ask the user five randomly selected questions, record only topics that need review (not private interview details), and revise unclear explanations.

- [ ] **Step 6: Verify and commit**

```bash
.venv/bin/python -m pytest tests/phase2/test_interview_material.py -q
git add docs/interview tests/phase2/test_interview_material.py
git commit -m "docs: add phase two interview review"
```

---

### Task 6: Create the phase-three handoff and finish phase two

**Files:**
- Create: `docs/phase-3-handoff.md`
- Modify: `docs/phase-2-progress.md`
- Modify: `docs/superpowers/specs/2026-08-12-issueflow-phase-2-roadmap-design.md`
- Create: `artifacts/phase-2/manifest.json`

**Interfaces:**
- Consumes: All phase-two code, tests, data, reports, and artifacts.
- Produces: Final inventory, open risks, phase-three inputs, and whole-phase acceptance.

- [ ] **Step 1: Generate the artifact manifest**

List each tracked phase-two artifact path, SHA-256, type, config hash, source experiment/run IDs, and redaction status. Reject missing files or files outside `artifacts/phase-2`.

- [ ] **Step 2: Write the bounded handoff**

Include: completed capabilities; measured result summary; known technical debt; candidate phase-three features (case workbench refinement, experiment dashboard, human-approved GitHub branch/PR, graph recovery, video, 4–6 page report); prerequisite decisions; security risks; and features explicitly deferred. Do not create a phase-three implementation plan yet.

- [ ] **Step 3: Run final verification from a clean checkout**

```bash
make verify-phase-1
make test-phase-2
.venv/bin/python scripts/qualify_benchmarks.py --catalog benchmarks/catalogs/strict.yaml --environments benchmarks/environments.yaml --replays 3
.venv/bin/python scripts/verify_experiment.py --config experiments/configs/phase-2-formal.yaml
.venv/bin/python scripts/build_phase2_report.py
git diff --check
```

Expected: phase one PASS; strict 20/20 × 3; experiment 80+80 verified; spend ≤ CNY 300; reports reproducible; credentials absent.

- [ ] **Step 4: Independent review gate**

Request a code/evidence review covering spec compliance, secret leakage, strict/exploratory separation, statistical claims, and artifact provenance. Resolve Critical/Important findings and rerun final verification before declaring complete.

- [ ] **Step 5: Update progress and commit**

Set 2D to `6/6`, verification `PASS`, actual total paid spend, and `Complete`. Add a short completion note to the phase-two design spec without rewriting its original requirements.

```bash
git add docs/phase-3-handoff.md docs/phase-2-progress.md docs/superpowers/specs/2026-08-12-issueflow-phase-2-roadmap-design.md artifacts/phase-2/manifest.json
git commit -m "docs: complete phase two handoff"
```
