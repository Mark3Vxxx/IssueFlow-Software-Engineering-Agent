# IssueFlow 2A 收尾：Reviewer 统一预算计划

> 状态：已关闭 —— 2A 置为 Complete，可进入 2B。

## 目标

把外层 Reviewer 严格纳入与架构相同的案例级预算，修复 `docs/phase-2-progress.md` 记录的四个缺口：

1. Reviewer HTTP 请求边界没有接收 RunService 计算出的剩余超时时间。
2. Reviewer 的真实墙钟耗时没有可靠写入 `Usage.duration_ms`。
3. 剩余 token / 成本 / 时间预算为零时，RunService 没有在调用前硬性拒绝 Reviewer。
4. 只有调用后的预算超限归一化，缺少调用前的统一准入。

约束：Reviewer 仍是顾问角色，失败或跳过不得改变由公开验证、diff、预算决定的确定性功能结果。

## 设计

- `ReviewModel.review` 协议增加关键字参数 `timeout_seconds: int`；`DeepSeekReviewClient` 用该值作为单次 HTTP 超时，并注入 `clock` 测量真实墙钟写入 `Usage.duration_ms`。
- `Reviewer.evaluate` 增加 `timeout_seconds: int | None = None`：确定性门槛通过且存在评审模型时，`None`（或 `<=0`）表示无预算头寸 → 跳过顾问调用并返回 `status="skipped"`、`reasons=["reviewer_skipped_no_budget"]`、空 `usage`。
- `RunService` 用 `_no_budget_headroom(usage, budget)`（对时间 / 输入 token / 输出 token / 成本做 `>=` 判定）决定传给 `evaluate` 的 `timeout_seconds`：无头寸传 `None`，有头寸传 `_remaining_seconds`。删除原先只检查时间的调用前早退分支。
- 调用后的预算超限仍由 `_budget_overrun_reason`（严格 `>`）归一化，覆盖「本次调用把用量推到上限之上」与「调用超时耗尽剩余时间」两种情况。

## 任务（TDD）

1. 写失败测试：超时透传到请求边界、墙钟计入用量、零头寸不发调用、精确等于上限时行为明确、调用超时 / 调用后超预算归一化、跳过与失败路径脱敏且用量权威持久化。
2. 实现 `reviewer.py` 与 `run_service.py` 改动并转绿。
3. 全量验证：`make verify-phase-1`、`make test-phase-2`、凭据扫描、`git diff --check`。
4. 请求独立复审，通过后更新 `docs/phase-2-progress.md` 将 2A 置为 `Complete`。

## 验收

- 剩余时间被传给 Reviewer 请求边界。
- Reviewer 墙钟时间计入 `run.usage.duration_ms` 与 reviewer 角色用量。
- 时间 / token / 成本任一维度零头寸时，Reviewer 不被调用，运行仍以确定性结果结束。
- 精确等于上限允许成功；严格超限才归一化为 `timed_out` / `budget_exhausted`。
- 失败与跳过路径保持凭据脱敏，`usage` / `role_usage` 权威持久化。
- 阶段一 197 项与阶段二 89 项测试全部通过，兼容矩阵 20/20。
