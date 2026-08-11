# IssueFlow 结构化补丁工具设计

## 背景

真实 `constructed-01` 运行证明，DeepSeek 对同一修复上下文会生成多种补丁文本：标准 Git diff、hunk 行数错误的 Git diff，以及 `*** Begin Patch` 封装。继续扩展模糊文本解析会增加误改文件的风险，也让 Benchmark 成功率依赖偶然的输出格式。

## 决策

模型侧继续使用工具名 `apply_patch`，但参数改为三个结构化字段：

```text
path:     工作区内的相对文件路径
old_text: 必须被替换的原始文本
new_text: 替换后的文本
```

旧的 `patch` 文本参数仅作为内部兼容入口保留，用于已有测试和历史调用；新的模型工具定义不再公开该参数。

## 安全与执行规则

1. `path` 必须是工作区内的相对路径；拒绝绝对路径、`..` 和符号链接越界。
2. `old_text` 必须非空，并且在目标文件中恰好出现一次。
3. 匹配零次或多次时拒绝修改，返回规范化的无效参数结果。
4. 替换在内存中计算完成后一次写回，不执行 shell 命令。
5. 每次成功调用仍计为一次补丁尝试，并受现有补丁次数、时间、token 和成本预算约束。

## 数据流

```text
DeepSeek tool call
  → ModelAction(apply_patch, path/old_text/new_text)
  → 参数白名单
  → 工作区路径验证
  → 唯一文本匹配
  → 原子式单文件写回
  → Agent 继续运行登记测试
```

## 错误处理

- 路径越界：`path must stay inside workspace`
- 缺少或类型错误：`structured patch requires path, old_text, and new_text`
- 未找到原文：`old_text must match exactly once: found 0`
- 多处匹配：`old_text must match exactly once: found N`

错误沿用现有 Agent 规则，记录失败轨迹并停止，不自动执行模型给出的其他动作。

## 测试与验收

自动测试覆盖：

- 唯一匹配时正确替换；
- 零匹配和多匹配均拒绝；
- `../` 越界拒绝；
- DeepSeek 请求中的 `apply_patch` schema 只公开结构化字段；
- 旧 Git diff 和 `*** Begin Patch` 兼容测试保持通过。

最终验收重新运行真实 `constructed-01`：必须完成故障复现、结构化修改、独立 Docker 验证、非空 diff、Reviewer 和 SQLite 轨迹持久化。
