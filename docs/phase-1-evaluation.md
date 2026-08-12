# IssueFlow 阶段一评估记录

评估日期：2026-08-11

评估范围：固定 `karpathy/micrograd` 案例、单 DeepSeek Agent、Docker 沙箱、Reviewer、SQLite/JSON 轨迹和 Streamlit 工作台。

## 如何阅读结果

本报告把两件不同的事情分开记录：

1. **参考补丁回放**：验证每个 Benchmark 是否真的“修复前失败、应用登记的参考补丁后通过”。它证明题目可复现，不代表 Agent 自己完成了修复。
2. **真实 Agent 运行**：让 DeepSeek 通过受限工具自主检索、读取、修改和测试，再由系统独立复验并审查。只有执行过的案例才报告 Agent 指标；未执行项明确标记为 `N/A`。

## 验收环境与命令

- Apple Silicon Mac
- Python 3.12.13（项目最低要求 3.11）
- Docker 镜像：`issueflow-micrograd:dev`
- 容器策略：`--network none`、只读根文件系统、2 CPU、4 GB 内存、256 个进程、临时 `/tmp`

完整验收入口：

```bash
make verify-phase-1
```

它按顺序执行代码规范检查、Docker 镜像构建、全量测试（包括端到端回放）和 5 个 Benchmark 的参考补丁验证。

## 5 个 Benchmark 的参考补丁回放

| 案例 | 类型 | 复现 | 参考修复 | Agent 功能成功 | 耗时 | 工具调用 | 输入/输出 Token | 成本 | Reviewer |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| `historical-01` | 历史 | `FAIL_AS_EXPECTED` | `PASS` | 未执行 / `N/A` | `N/A` | `N/A` | `N/A` | `N/A` | `N/A` |
| `constructed-01` | 自建 | `FAIL_AS_EXPECTED` | `PASS` | 是 | 485 ms* | 4 | 3,570 / 334 | $0.0001894032 | `approved` |
| `constructed-02` | 自建 | `FAIL_AS_EXPECTED` | `PASS` | 未执行 / `N/A` | `N/A` | `N/A` | `N/A` | `N/A` | `N/A` |
| `constructed-03` | 自建 | `FAIL_AS_EXPECTED` | `PASS` | 未执行 / `N/A` | `N/A` | `N/A` | `N/A` | `N/A` | `N/A` |
| `constructed-04` | 自建 | `FAIL_AS_EXPECTED` | `PASS` | 未执行 / `N/A` | `N/A` | `N/A` | `N/A` | `N/A` | `N/A` |

\* 485 ms 的口径是已持久化执行步骤耗时之和，完整解释见“真实 Agent 成功记录”。

来源与许可证：5 个案例都使用 MIT 许可证的 `karpathy/micrograd`。`historical-01` 指向公开上游修复提交；4 个 `constructed-*` 案例是在固定上游版本上人为注入、可审计的最小回归，不能解释为真实历史 Bug。

## 真实 Agent 成功记录

案例：`constructed-01`

运行 ID：`run-67d50ef1a1c64e8081a03d295d9aa00a`

模型：`deepseek-v4-flash`

| 指标 | 结果 |
| --- | ---: |
| 最终状态 | `succeeded` |
| 功能成功 | `true` |
| 停止原因 | `functional_success` |
| 工具调用 | 4 |
| 补丁尝试 | 1 |
| 输入 Token | 3,570 |
| 输出 Token | 334 |
| 估算模型成本 | $0.0001894032 |
| 已持久化步骤耗时之和 | 485 ms |
| Reviewer | `approved` |
| 轨迹步骤 | 8 |

按顺序保存的 8 步证据是：故障复现、代码搜索、文件读取、应用补丁、Agent 内测试、系统独立验证、Git diff、Reviewer 结论。原始运行先写入 SQLite，脱敏 JSON 已固化为 [`artifacts/phase-1/constructed-01-live-run.json`](../artifacts/phase-1/constructed-01-live-run.json)。自动测试会验证步骤连续、结果成功、指标一致，并确认环境中的真实 API Key 没有出现在文件中。

> 耗时口径：485 ms 是轨迹中已持久化的命令与工具执行耗时之和，不包含 DeepSeek HTTP 请求的完整等待时间，因此不能当作端到端墙钟耗时。

## 确定性端到端回放

`tests/test_e2e_smoke.py` 另有一条不调用外部模型的验收路径。它只在模型边界使用固定动作，其余组件全部真实运行：

- 建立本地 Git 上游仓库并固定 commit；
- 注入故障补丁并提交为干净基线；
- 用真实的断网 Docker 先复现失败；
- 经过真实 `SingleAgent` 和 `ToolExecutor` 的 4 个受限动作；
- 用 Docker 独立复验；
- 生成真实 Git diff；
- 经过确定性 Reviewer 门槛；
- 写入真实 SQLite，再导出并回读 JSON。

这条测试用于稳定地防止模块集成退化，不应冒充真实 LLM 效果；上面的 JSON 记录才是本阶段真实 DeepSeek 运行证据。

## 成功标准核对

| 标准 | 状态 | 证据 |
| --- | --- | --- |
| 固定案例可以复现 | 通过 | 5/5 修复前均失败 |
| 参考补丁有效 | 通过 | 5/5 修复后均通过 |
| 单 Agent 能完成至少一个真实修复 | 通过 | `constructed-01` 真实 DeepSeek 运行 |
| 功能成功不可由 Reviewer 覆盖 | 通过 | 复现、非空 diff、独立验证、预算四个确定性门槛 |
| 运行轨迹可回放 | 通过 | SQLite 顺序写入、JSON 导出、8 步证据 |
| 凭据不落盘 | 通过 | 存储脱敏、证据自动扫描、容器不接收 Key |
| 用户可观察运行 | 通过 | Streamlit 状态、指标、diff、Reviewer、时间线与下载 |

## 局限与下一阶段方向

后续样本必须显式选择经过实测的最小预算档位。预算只定义资源上限，不代表修复能力；评估应报告“运行 N 次、成功 M 次”，并分别记录预算耗尽、错误补丁和验证失败，不能用单次成功或更高档位宣称保证解决。

- 真实 Agent 评估目前只有 `constructed-01` 一次成功记录，不能据此推断总体修复率；需要多次重复和更多仓库。
- 只有公开、登记过的验证命令，没有隐藏测试或泛化评估。
- 单 Agent 和单 Reviewer 均依赖同一外部模型供应商，API 波动会影响真实运行。
- 页面一次只运行一个任务；没有队列、多用户隔离、远程执行或恢复机制。
- SQLite 适合本地 MVP，不适合多进程生产部署。
- 当前不创建分支、提交、PR，也不允许任意仓库输入；这些是刻意保留的安全边界。
