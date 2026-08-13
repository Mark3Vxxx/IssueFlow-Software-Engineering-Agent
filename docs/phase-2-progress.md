# Phase 2 progress

Update this table only from verified milestone gates.

| Milestone | Tasks done | Verification | Paid spend | Status |
| --- | ---: | --- | ---: | --- |
| 2A Architectures | 8/8 | Tests PASS; reviewer-budget fix reviewed clean | CNY 0 | Complete |
| 2B Benchmark | 4/8 | Framework PASS; 0/20 strict × 3 | CNY 0 | In progress |
| 2C Experiments | 0/10 | 0/160 trials | CNY 0 | Not started |
| 2D Results | 0/6 | Not run | CNY 0 | Not started |

## 当前快照（2026-08-13）

- 工作分支：`main`
- 2A 已 `Complete`；2B 的 4 个框架代码任务（catalog 拆分、环境 registry、隐藏验证隔离、三次重放资格验证器）已完成并提交，剩余为数据构建阶段（仓库筛选 + 严格/探索样本）。
- 当前累计付费 API 支出：`CNY 0`。所有现有架构与验证均使用确定性脚本模型。

## 已验证证据

- `make verify-phase-1`：通过，包括 Docker 构建、Ruff、`197` 项 pytest 和兼容参考补丁重放 `5/5`。
- `make test-phase-2`：`89` 项通过。
- 保留的单夹具四架构基础设施 E2E：`4/4`。
- 四架构 × 五个兼容案例的确定性集成矩阵：`20/20`。
- 完整 E2E 文件：`24/24`。
- 凭据扫描：通过；实际 `DEEPSEEK_API_KEY` 未出现在 3 份 Git 差异、83 个 SQLite 数据库或 83 个重新生成的 JSON 导出中。扫描过程未打印密钥。
- `git diff --check`：通过。

这些证据分为三层：兼容参考补丁重放验证数据集合同（`5/5`）；单夹具 E2E 验证四种架构共用的基础设施链路（`4/4`）；兼容矩阵使用逐案例的确定性决策，通过真实 RunService、Git、Docker、独立验证、SQLite 和 JSON 验证集成广度（`20/20`）。矩阵不会读取或应用参考补丁，也不代表自主模型质量。

## 已关闭的阻塞项（Reviewer 统一预算）

外层 Reviewer 现已严格纳入同一个案例级预算，四个缺口均已修复并验证：

- Reviewer 请求边界接收 RunService 计算的剩余超时时间。
- Reviewer 真实墙钟耗时写入 `Usage.duration_ms`。
- 剩余时间 / token / 成本预算为零时，RunService 调用前即跳过 Reviewer。
- 调用前统一准入 + 调用后超限归一化并存，语义明确。

独立复审结论：无 Critical / Important 发现；仅三处既有模式的小项（`_remaining_seconds` 向上取整、既有 `>=` 时间检查、浮点成本比较），不在本次修复范围内。

## 后续任务

### 2A 收尾修复（已完成，已提交）

Reviewer 统一预算收尾已完成并提交（`84212a8` + `883f806`）：独立计划见 `docs/superpowers/plans/2026-08-13-issueflow-reviewer-unified-budget.md`；六个回归测试全部落地并通过；Phase 1 / Phase 2 / 兼容矩阵 / 凭据扫描 / `git diff --check` 全绿；独立复审无 Critical / Important 发现。

### 2B Benchmark Expansion

框架代码已完成（`7171bf3` catalog 拆分、`116cf4f` 环境 registry、`06a9315` 隐藏验证隔离、`8330c03` 三次重放资格验证器）。付费 2C 实验继续保持禁止状态。剩余为数据构建阶段：

1. 资格筛选候选仓库，并由用户批准 3–4 个仓库、许可证和 strict/exploratory 分类。（**用户审批门**）
2. 构建 20 个新的严格案例和至少 10 个探索案例；五个 Phase 1 micrograd 案例只作为兼容套件，不计入新严格案例。（**真人学习门**）
3. 每个严格案例必须在修复前失败、应用参考修复后通过公开与隐藏验证，并在三个干净工作区中稳定重放。
4. 运行来源、环境、泄漏和隐藏测试隔离检查；参考补丁与隐藏测试不得进入 Agent 可见工作区。
5. 更新进度为实际通过三次重放的严格案例数，不为赶进度降低验收门槛。

### 2C Comparative Experiments

仅在 2A 和 2B 严格案例门完成后开始：

1. 先执行不超过 `CNY 30` 的五案例校准。
2. 冻结模型、提示词、预算、案例清单、Docker digest 和重试规则。
3. 执行第一轮 `80` 次试验，每 20 次进行成本与基础设施错误率检查。
4. 按冻结规则选择 10 个案例，再执行 `80` 次重复试验。
5. 总正式试验约 `160` 次，总付费支出不得超过 `CNY 300`。

### 2D Results and Interview Review

1. 从 SQLite 重建聚合表，并把每个单元格追溯到 run ID。
2. 生成中英文结果、图表、案例研究、访谈复盘和三分钟项目介绍。
3. 记录 Phase 3 交接事项，不在 Phase 2 中加入生产队列、多用户隔离、跨进程恢复或完整实验仪表盘。
