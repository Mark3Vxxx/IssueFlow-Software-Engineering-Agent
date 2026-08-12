# IssueFlow Benchmark 预算档位设计

日期：2026-08-12

## 背景

真实运行 `run-a4e1d1c656cd4eb3a02742871cc0f7c0` 在处理 `historical-01` 时，前两次补丁成功应用，Agent 随后请求第三次补丁，但当前 UI 对所有案例统一使用 `max_patch_attempts=2`，因此运行以 `patch_budget_exhausted` 停止。该运行当时仍有工具、时间、Token 和成本余量。

统一预算不能反映案例复杂度，“历史”或“自建”也不是可靠的难度指标。预算只能限制资源，不能保证模型一定修复成功。阶段一需要在保持硬安全边界的前提下，让每个固定 Benchmark 显式选择经过校准的预算档位，并让用户看懂预算与停止原因。

## 目标

- 每个 Benchmark 显式声明一个 `small`、`medium` 或 `large` 预算档位。
- 系统自动把该档位解析为不可变的硬预算，用户无需手动配置。
- 页面在运行前显示预算档位和具体限制。
- 页面在预算耗尽后显示具体中文原因，而不是只显示“预算已用尽”。
- `historical-01` 使用 `medium`，允许超过原先两次补丁限制；现有四个自建样本继续使用 `small`。
- 新增 Benchmark 时，缺少或使用未知预算档位必须在目录加载阶段失败。
- 保持确定性成功判定：预算更高不等于成功，仍须复现失败、非空 diff、独立验证通过且预算未耗尽。

## 非目标

- 不承诺任意 Benchmark 或任意一次模型运行必然成功。
- 不让用户在 UI 中输入任意预算。
- 不在运行中自动扩容、续费、重试或切换预算档位。
- 不根据 `historical` / `constructed` 自动推断复杂度。
- 不修改 SQLite 表结构或历史轨迹格式。
- 不扩大 Agent 工具白名单，不处理 `rg` 安装或搜索回退问题。

## 领域模型与模块边界

### BenchmarkCase

`BenchmarkCase` 新增必填字段：

```python
budget_profile: Literal["small", "medium", "large"]
```

字段必须显式填写，不提供隐式默认值。这样每个新样本在进入目录时都必须作出资源边界选择，不会悄悄继承一个可能不合适的预算。

现有目录分配：

| 案例 | 档位 |
| --- | --- |
| `historical-01` | `medium` |
| `constructed-01` | `small` |
| `constructed-02` | `small` |
| `constructed-03` | `small` |
| `constructed-04` | `small` |

### budget 模块

新增 `src/issueflow/budget.py`，集中保存档位与纯解析函数，避免 UI 承担领域决策：

```python
BUDGET_PROFILES: Mapping[str, Budget]

def budget_for_case(case: BenchmarkCase) -> Budget:
    ...
```

预算值：

| 档位 | 工具调用 | 补丁 | 时间 | 输入 Token | 输出 Token | 成本上限 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `small` | 12 | 2 | 300 秒 | 30,000 | 6,000 | $0.05 |
| `medium` | 18 | 4 | 450 秒 | 50,000 | 8,000 | $0.10 |
| `large` | 24 | 6 | 600 秒 | 80,000 | 12,000 | $0.20 |

`Budget` 是 Pydantic 模型。解析函数每次返回独立副本，避免调用方意外修改共享配置。Agent 继续消费现有 `Budget` 接口，内部预算检查顺序和硬停止语义不变。

`large` 在当前目录中暂不使用，但为后续经评估的多文件案例提供有界档位；它不是无限预算。

## 数据流

1. `load_catalog` 校验每个 YAML 案例包含合法 `budget_profile`。
2. Streamlit 用户选择案例。
3. UI 调用 `budget_for_case(case)` 获得该案例的完整预算。
4. 页面展示档位和限制摘要。
5. 点击“开始真实修复”后，UI 把同一个预算对象传给 `RunSession.start`，再交给 `RunService` 和 `SingleAgent`。
6. Agent 达到任何硬限制时，继续写入现有规范化 `stop_reason` 并停止。
7. 结束页面从持久化轨迹读取 `stop_reason`，显示对应的中文解释。

UI 不根据运行过程临时调整预算，也不根据模型请求决定是否加码。

## 页面反馈

案例选择区新增紧凑预算摘要，例如：

> 预算档位：medium · 工具 18 次 · 补丁 4 次 · 450 秒 · 输入 50,000 Token · 最高 $0.10

新增纯函数将已知停止原因翻译为中文：

| stop_reason | 页面说明 |
| --- | --- |
| `patch_budget_exhausted` | 补丁次数预算已用尽 |
| `tool_budget_exhausted` | 工具调用预算已用尽 |
| `time_budget_exhausted` | 运行时间预算已用尽 |
| `input_token_budget_exhausted` | 输入 Token 预算已用尽 |
| `output_token_budget_exhausted` | 输出 Token 预算已用尽 |
| `cost_budget_exhausted` | 成本预算已用尽 |

页面在失败状态下同时显示总体状态和具体停止原因。未知原因不会被误译；它使用现有脱敏函数处理后显示技术文本，保留排查价值且避免凭据泄露。

## 错误处理与安全边界

- 缺少或未知 `budget_profile`：目录加载失败，阻止错误案例出现在 UI。
- 预算耗尽：保持终态 `budget_exhausted`，不自动重试或续增资源。
- 未知 `stop_reason`：显示脱敏后的原始值，不改变持久化记录。
- `medium` 和 `large` 仍受工具、补丁、时间、Token 与成本六类硬限制。
- 成功判定与 Reviewer 行为不变；Reviewer 不能覆盖确定性失败。
- API Key 处理、Docker 隔离和工具白名单不变。

## 后续样本的档位选择

预算档位提高成功机会，但不提供成功保证。新增样本时应依据以下证据选择档位：

- 参考补丁涉及的文件数和修改规模；
- 预期需要的检索、阅读和验证次数；
- 基线试运行的工具、补丁、Token 和成本轨迹；
- 多次真实 Agent 运行的成功率，而不是单次结果。

建议先选满足预期流程的最小档位。若试运行因同一预算边界反复停止，才在记录理由后提升档位。报告应写成“运行 N 次、成功 M 次”，不能写成“该档位保证解决”。

## 测试策略

### 模型与目录

- `BenchmarkCase` 接受三个合法档位。
- 缺少或使用未知档位时校验失败。
- 五个现有 YAML 案例加载后具有预期档位。

### 预算解析

- `small`、`medium`、`large` 精确解析为表中数值。
- `historical-01` 获得 `medium`，四个自建案例获得 `small`。
- 多次调用返回等值但独立的 `Budget` 对象。

### UI

- 案例页面显示正确档位与完整限制摘要。
- 点击开始时，运行服务接收该案例解析出的预算，而非全局常量。
- 六种预算停止原因显示正确中文文本。
- 未知停止原因经过脱敏后安全回退。

### Agent 回归

- 现有预算硬停止测试继续通过。
- 新增确定性测试：一个脚本模型提出第三次合法补丁时，`medium` 不会在旧的两次阈值处停止；达到第四次已执行补丁后，第五次请求仍须以 `patch_budget_exhausted` 停止。
- 不用真实模型作为自动测试依赖。

### 完整验收

```bash
make lint
make test
make verify-benchmarks
```

实现后可额外运行一次真实 `historical-01` 观察性验收，确认页面使用 `medium` 且不会在旧的两次补丁阈值停止。真实运行结果必须如实报告；模型仍可能因其他预算、错误补丁或验证失败而不成功。

## 验收标准

- 目录中的每个案例都显式声明合法预算档位。
- `historical-01` 的运行收到 `medium` 完整预算，当前自建案例收到 `small`。
- 页面在运行前显示限制，在预算耗尽后显示具体原因。
- Agent 在任何档位下都不能越过相应硬限制。
- 自动测试不依赖网络模型且覆盖旧阈值回归。
- 全量测试、Benchmark 参考补丁验证和凭据安全检查通过。
- 文档明确声明预算不能保证修复成功。
