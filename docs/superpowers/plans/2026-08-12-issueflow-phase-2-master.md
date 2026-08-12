# IssueFlow Phase Two Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver phase two as four independently testable milestones: comparable LangGraph architectures, a new 20-case strict benchmark plus 10-case exploratory set, a budgeted four-architecture experiment, and reproducible results/interview material.

**Architecture:** Preserve `RunService` as the deterministic outer controller and put Direct, Single, Fixed, and Dynamic behind one architecture contract. Build the benchmark and experiment layers around immutable run evidence in `TraceStore`; LangGraph controls role routing but never becomes the source of truth for correctness, cost, or aggregate results.

**Tech Stack:** Python 3.11+, Pydantic 2, LangGraph 1.2.x, HTTPX, PyYAML, SQLite, Docker, Streamlit, pytest, Ruff, DeepSeek API.

## Global Constraints

- Phase-two duration is 4–5 weeks at 20+ hours per week.
- Total paid DeepSeek API spend must not exceed CNY 300.
- Use `langgraph>=1.2,<2`; change this range only in an explicit dependency-upgrade task.
- Create 20 new strict cases across 3–4 repositories and at least 10 exploratory cases.
- The five phase-one micrograd cases remain a compatibility suite and do not count toward the 20 strict cases.
- Compare exactly `direct`, `single`, `fixed`, and `dynamic` under the same case-level budget, model configuration, visible information, sandbox, and deterministic success gates.
- Run all 20 strict cases once for all four architectures, then apply the frozen post-round selection rule and add two runs for all four architectures on exactly 10 critical/unstable cases: approximately 160 formal paid runs.
- A strict case must fail before repair, pass public and hidden verification after its reference patch, and replay consistently in three clean workspaces.
- Reference patches and hidden tests must remain outside the Agent-visible workspace.
- Reviewer and LangGraph checkpoint failures are advisory/infrastructure failures; they cannot override deterministic functional evidence.
- API keys must never enter Docker, Git, logs, SQLite, JSON, screenshots, prompts stored as artifacts, or generated reports.
- Preserve `/Users/mark3v/Desktop/agent/2026-08-11-software-engineering-agent-design.md` unless the user explicitly asks to version it.

---

## Plan Set and Execution Order

| Order | Plan | Exit gate |
| ---: | --- | --- |
| 1 | [`2A: Agent Architectures`](2026-08-12-issueflow-phase-2a-agent-architectures.md) | Four architectures run through one `RunService`; phase-one compatibility verification passes. |
| 2 | [`2B: Benchmark Expansion`](2026-08-12-issueflow-phase-2b-benchmark-expansion.md) | 20 new strict and 10+ exploratory cases are cataloged; strict cases pass three clean replays. |
| 3 | [`2C: Comparative Experiments`](2026-08-12-issueflow-phase-2c-comparative-experiments.md) | About 160 formal runs are complete within CNY 300 and aggregates rebuild from SQLite. |
| 4 | [`2D: Results and Interview Review`](2026-08-12-issueflow-phase-2d-results-and-interview.md) | Bilingual evidence, charts, case studies, interview review, and phase-three handoff are complete. |

Do not start paid 2C runs while 2A or the strict-case gate in 2B is incomplete. Candidate discovery in 2B may overlap the final implementation tasks in 2A, but accepted case files must not be merged before their environment and leakage checks pass.

The 20-case strict set is the schedule's critical path. If fewer than 20 cases pass all gates by the end of week 3, extend qualification into week 4 and delay paid experiments; do not reduce hidden-test, provenance, or three-replay requirements to preserve the calendar.

## File Ownership Map

| Area | Primary responsibility |
| --- | --- |
| `src/issueflow/architectures/` | Architecture contracts, Direct/Single adapters, LangGraph state, roles, Fixed, Dynamic, factory. |
| `src/issueflow/benchmark*.py` | Dataset split validation, hidden verification, environment metadata, qualification. |
| `src/issueflow/experiment*.py` | Frozen experiment configuration, scheduling, spend ledger, retry policy, aggregation. |
| `src/issueflow/run_service.py` | Deterministic reproduction, architecture execution, public/hidden verification, diff, terminal result. |
| `src/issueflow/trace_store.py` | Authoritative run, role, experiment, trial, metric, and export records. |
| `benchmarks/catalogs/` | Compatibility, strict, and exploratory catalog metadata. |
| `benchmarks/cases/` | Auditable fault/reference patch packages; no hidden tests. |
| `benchmarks/hidden/` | Validator-only hidden tests, never copied into Agent workspaces. |
| `docker/` | Pinned repository-specific CPU-only environments. |
| `experiments/configs/` | Frozen calibration and formal experiment manifests. |
| `artifacts/phase-2/` | Redacted aggregate results, selected traces, charts, and configuration snapshots. |
| `docs/` | Evaluation reports, operating instructions, learning notes, and interview material. |

## Five-Week Working Calendar

### Week 1 — 2A contracts and baselines

- [ ] Complete architecture contracts, Single adapter, Direct baseline, and structured model boundary.
- [ ] Add LangGraph dependency and deterministic graph smoke tests.
- [ ] Keep all phase-one tests green.
- [ ] Learning checkpoint: explain why an architecture contract is needed and why Direct is a valid baseline.

### Week 2 — 2A graphs plus 2B candidate funnel

- [ ] Complete Fixed and Dynamic graphs, role-level usage, routing trace, and UI selector.
- [ ] Run all four architectures with deterministic scripted models on the five compatibility cases.
- [ ] Execute the repository qualification funnel and freeze 3–4 selected repositories.
- [ ] Learning checkpoint: draw Fixed and Dynamic state transitions without reading code.

### Week 3 — 2B data construction

- [ ] Accept the first five strict cases and run the no-leakage gate.
- [ ] Expand to 20 new strict cases and at least 10 exploratory cases.
- [ ] Replay every strict case three times in clean workspaces.
- [ ] Learning checkpoint: explain six bugs and three hidden tests in the user's own words.

### Week 4 — 2C calibration and first formal round

- [ ] Run five-case calibration within CNY 30 and inspect architecture cost distribution.
- [ ] Freeze prompts, model, budgets, case list, Docker digests, and retry rules.
- [ ] Run the 80-trial first formal round in four batches of 20 with a human cost gate after each batch.
- [ ] Learning checkpoint: explain fairness controls and infrastructure-vs-agent failure classification.

### Week 5 — 2C repeats and 2D reporting

- [ ] Select 10 repeat cases with the frozen rule and run 80 additional trials.
- [ ] Rebuild all aggregate tables from SQLite and verify every cell against run IDs.
- [ ] Produce bilingual results, charts, case studies, interview review, and phase-three handoff.
- [ ] Learning checkpoint: deliver a three-minute project explanation and answer the phase-two question bank.

## Progress Visualization

Maintain `docs/phase-2-progress.md` during execution with this exact table; update it only from verified task gates:

```markdown
| Milestone | Tasks done | Verification | Paid spend | Status |
| --- | ---: | --- | ---: | --- |
| 2A Architectures | 0/8 | Not run | CNY 0 | Not started |
| 2B Benchmark | 0/8 | 0/20 strict × 3 | CNY 0 | Not started |
| 2C Experiments | 0/10 | 0/160 trials | CNY 0 | Not started |
| 2D Results | 0/6 | Not run | CNY 0 | Not started |
```

Status values are exactly `Not started`, `In progress`, `Blocked`, and `Complete`. “Complete” requires the plan's exit verification output, not an implementation claim.

## User Actions

- [ ] Review and approve the 3–4 selected repositories, their licenses, and the strict/exploratory classification.
- [ ] Explain at least six accepted strict bugs without reading the reference patch.
- [ ] Explain how hidden tests prevent shortcut fixes for at least three cases.
- [ ] Approve paid calibration only after deterministic tests and credential scans pass.
- [ ] Review spend and infrastructure error rate after each 20 paid trials.
- [ ] Manually inspect at least 16 formal patches (10% of approximately 160 runs).
- [ ] Approve the frozen formal experiment manifest before the first 80-run round.
- [ ] Rehearse the final three-minute explanation and the phase-two interview questions.

## Whole-Phase Verification

Run after every milestone and once more on final `main`:

```bash
make verify-phase-1
.venv/bin/python -m pytest tests/phase2 -q
.venv/bin/python scripts/verify_benchmarks.py --catalog benchmarks/catalogs/compatibility.yaml
.venv/bin/python scripts/qualify_benchmarks.py --catalog benchmarks/catalogs/strict.yaml --environments benchmarks/environments.yaml --replays 3
.venv/bin/python scripts/verify_experiment.py --config experiments/configs/phase-2-formal.yaml
git diff --check
```

Expected final evidence:

- All phase-one regression tests and five compatibility Benchmarks pass.
- Every strict case reports three successful reference replays and no answer leakage.
- Experiment verification reports 80 first-round trials plus 80 repeat trials, no duplicate trial keys, no unclassified failure, and total paid spend at or below CNY 300.
- Credential scan reports that the current `DEEPSEEK_API_KEY` value is absent from tracked changes, SQLite exports, JSON, and generated reports without printing the key.

## Completion Boundary

Phase two ends after 2D. Do not add GitHub push/PR automation, a production queue, multi-user isolation, cross-process graph recovery, a full experiment dashboard, or video production. Record those items in the phase-three handoff instead.
