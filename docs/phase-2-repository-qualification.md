# Phase 2 仓库资格筛选

> 状态：已冻结（用户 2026-08-13 批准），并如实记录严格样本短少。

## 选定仓库（4 个）

| 优先级 | 仓库 | 许可证 | Python 非空行 |
| ---: | --- | --- | ---: |
| 1 | `karpathy/minGPT` | MIT | 1,057 |
| 2 | `karpathy/nanoGPT` | MIT | 1,085 |
| 3 | `karpathy/nanochat` | MIT | 6,731 |
| 4 | `karpathy/makemore` | MIT | 603 |

## 被拒候选

| 仓库 | 拒绝原因 |
| --- | --- |
| `karpathy/build-nanogpt` | 无 LICENSE 文件，许可证门失败 |
| `tinygrad/tinygrad` | HEAD 核心树 347,643 行，超 10,000 行上限 |
| `minitorch/minitorch` | 无 LICENSE 文件（GitHub API `license: null`），许可证门失败 |
| `google-deepmind/optax` | Apache-2.0 但核心 39,212 行超上限，且依赖 JAX |
| `Lightning-AI/torchmetrics` | Apache-2.0 但规模偏大 |
| `pytorch/ignite` | BSD-3-Clause 但 72MB 规模过大 |

## 严格样本实际结果（目标 20，实际 6）

| 仓库 | 计划配额 | 实际构建 |
| --- | ---: | ---: |
| `karpathy/minGPT` | 5 | 1 |
| `karpathy/nanoGPT` | 5 | 3 |
| `karpathy/nanochat` | 6 | 1 |
| `karpathy/makemore` | 4 | 1 |
| **合计** | **20** | **6** |

## 短少原因（如实记录）

- 这 4 个仓库都是 karpathy 的小型教程仓库，代码量小、维护良好，历史提交大部分是**重构 / 加配置 / 改注释 / 打印调整**，真正的「干净、CPU 可复现的功能 bug」非常稀少。
- 深挖 4 个仓库的完整提交历史后，能找到的干净功能 bug 合计约 6 个。
- 计划的「20 个严格样本」目标因此**未能达成**，且未通过降低隐藏测试 / 溯源 / 三次重放门槛来凑数（符合计划「不降门槛」的原则）。

## 后续

- [ ] 6 个严格样本已全部构建并通过三次重放验证（见 `benchmarks/catalogs/strict.yaml`）。
- [ ] 探索集（至少 10 个）单独构建，不与严格集合并。
- [ ] 2C 实验规模按实际 6 个严格样本重新校准。
