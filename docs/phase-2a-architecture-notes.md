# Phase 2A architecture notes

Phase 2A puts Direct, Single, Fixed, and Dynamic behind one runtime contract. This note is the learning handoff for that milestone; it describes the implemented behavior, not a claim that one architecture is better.

## Why the architecture contract exists

`ArchitectureRunner.run(case, workspace, budget, context)` gives all four experiment arms the same inputs and an `ArchitectureResult` gives `RunService` the same outputs: architecture identity, terminal status and reason, immutable trace steps, aggregate usage, role usage, route count, and final message. The contract isolates internal orchestration from the outer evidence pipeline. `RunService` can therefore apply one reproduction, independent verification, diff, functional-success, SQLite, and JSON policy to every arm without architecture-specific branches.

- **Direct** makes one structured patch proposal from bounded repository context and applies it once.
- **Single** adapts the phase-one tool-using loop to the shared result contract.
- **Fixed** runs Planner → Retriever → Coder → Reviewer, with at most one Reviewer-requested Coder rework.
- **Dynamic** lets a Supervisor choose among the same four bounded roles, subject to deterministic route guards and a 12-route cap.

`ArchitectureFactory` is the only construction path. It binds one selected kind to the same case, workspace, sandbox, and model boundaries. `RunService.start(case_id, budget)` remains the compatibility call and selects Single by default; an explicit third argument selects another architecture.

## LangGraph State, Node, and Edge mapping

The shared `WorkflowState` is the bounded handoff between roles. It contains the case ID and issue, plan, evidence, current diff, executed public-test result, Reviewer feedback, total `Usage`, per-role usage, role history, rework count, route count, and an optional stop reason. It deliberately does not retain an open-ended chat transcript, credentials, or checkpoint metadata.

Each **Node** is one responsibility boundary. Planner returns a bounded plan and has no tools. Retriever may only search or read. Coder may read, apply a patch, or run a registered test. Reviewer has no tools and returns advisory feedback. Dynamic adds a Supervisor node that may route but cannot use tools, change budgets, or declare functional success.

An **Edge** controls which node may run next. Fixed uses code-defined edges for the four-role sequence and its one optional rework. Dynamic returns to Supervisor after every role; conditional edges turn the Supervisor's schema-validated decision into the next node or terminal edge. LangGraph's in-memory checkpoint uses the IssueFlow run ID as `thread_id`, while SQLite remains the durable run record.

## Fixed versus Dynamic

Fixed is the controlled workflow baseline: order is known before execution, traces are simple, and only Reviewer feedback can cause one Coder rework. Dynamic uses the same role boundaries but tests whether model-selected routing helps. Its flexibility is constrained: Coder requires both a plan and evidence, Reviewer requires a non-empty diff, Stop requires an actually executed passing public test, Reviewer may run at most twice, and the Supervisor may make at most 12 decisions.

The comparison is therefore about orchestration, not access. Both arms receive the same case-level budget, tool allowlist, workspace, Docker checks, deterministic success rule, and persistence path.

## Why Reviewer does not decide success

Reviewer output is useful qualitative evidence, but it is model output and can be wrong, malformed, unavailable, or inconsistent. Functional success is owned by `RunService` and requires the faulty state to reproduce, the selected architecture to finish without a hard stop, independent registered verification to pass, and a non-empty Git diff. The outer Reviewer reports an advisory status after those gates. A negative advisory review does not overturn passing deterministic gates, and an approval cannot rescue failed verification.

The Reviewer inside Fixed or Dynamic has the same boundary: it can approve or request bounded rework, but its opinion is not the persisted functional-success authority.

## Budget aggregation and fairness

Every arm receives the same complete `Budget` object. `Usage` aggregates model calls, tool calls, patch attempts, input and output tokens, estimated cost, and duration. Fixed and Dynamic add each role delta to the architecture total and to that role's subtotal; Dynamic also charges every Supervisor call. Tool and patch caps permit exactly the advertised number of operations and reject the next matching operation. Time, token, and cost checks stop the workflow at their shared case-level limit. No role receives a private budget that can expand the case budget.

This produces comparable accounting, although equal total budgets do not imply equal numbers of useful model turns: multi-role prompts and Supervisor decisions consume part of the same allowance.

## Route and boundary failure behavior

Dynamic normalizes an invalid schema or malformed Supervisor response to a failed trace step and a stable stop reason. It rejects Coder before plan plus evidence, Reviewer before a non-empty diff, a third Reviewer call, and Stop before an executed passing public test as `invalid_supervisor_route`. A model-selected `fail` becomes `supervisor_failed`; 12 decisions become `supervisor_route_budget_exhausted`. Role exceptions and invalid updates become `invalid_role_output`, budget causes retain their specific stop reason, and graph failures are persisted rather than escaping the run.

Fixed similarly normalizes invalid role output, ends on shared-budget reasons, and fails a second requested rework as `review_loop_exhausted`. In every case, `RunService` persists the terminal run and does not proceed to independent verification after an architecture-level hard failure.

## Current limitations

- Phase 2A proves integration on the five phase-one compatibility cases and one deterministic local Docker fixture; it does not provide the new strict benchmark or comparative experiment results.
- The LangGraph checkpointer is in memory, so a graph cannot resume after a process restart. SQLite persists final IssueFlow evidence, not live LangGraph checkpoint recovery.
- Public registered checks are visible to the model. Hidden validation and leakage controls belong to the later benchmark milestone.
- Direct sees a bounded repository map; the other arms share current allowlisted tools. Large repositories and non-Python environments are not established here.
- The deterministic tests use scripted models and spend CNY 0. They prove wiring and invariants, not model quality, cost superiority, or statistical significance.
- Role aggregates are derived from immutable trace-step roles; Phase 2A intentionally adds no separate role-metrics table or experiment dashboard.

## Two-minute interview answer

IssueFlow Phase 2A turns a phase-one single-agent repair loop into a fair four-architecture test bed. I introduced one `ArchitectureRunner` contract, so Direct, Single, Fixed multi-agent, and Dynamic Supervisor all receive the same benchmark case, isolated Git workspace, total budget, and run context, and all return the same trace-and-usage result. `RunService` stays outside the agents and owns reproduction, independent Docker verification, Git diff, deterministic success, SQLite, and JSON. That separation prevents an architecture or Reviewer from grading its own work.

For the multi-agent arms I used LangGraph with a bounded state rather than a chat transcript. Planner has no tools, Retriever can only search and read, Coder can apply patches and run registered tests, and Reviewer is advisory. Fixed hard-codes their order with one possible Coder rework. Dynamic adds a schema-constrained Supervisor, but deterministic guards reject unsafe orderings, premature success, excessive Reviewer calls, and more than 12 routes.

Fairness comes from passing the same case-level budget to every arm and aggregating all model, tool, patch, token, time, and cost usage, including Supervisor and Reviewer calls. The end-to-end acceptance test scripts model decisions but keeps Git cloning, Docker reproduction, actual patch application, independent verification, diff, `RunService`, SQLite, and JSON real for all four parameterized arms. It also exposed and fixed an off-by-one where one permitted patch incorrectly blocked the following test. Phase 2A establishes comparable plumbing; it does not yet claim which architecture performs best.
