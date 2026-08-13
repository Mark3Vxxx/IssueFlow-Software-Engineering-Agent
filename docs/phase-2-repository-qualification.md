# Phase 2 仓库资格筛选

> 状态：已冻结（用户 2026-08-13 批准）

## 选定仓库（4 个，配额合计 20）

| 优先级 | 仓库 | 许可证 | Python 非空行 | 配额 |
| ---: | --- | --- | ---: | ---: |
| 1 | `karpathy/minGPT` | MIT | 1,057 | 5 |
| 2 | `karpathy/nanoGPT` | MIT | 1,085 | 5 |
| 3 | `karpathy/nanochat` | MIT | 6,731 | 6 |
| 4 | `karpathy/makemore` | MIT | 603 | 4 |

## 被拒候选

| 仓库 | 拒绝原因 |
| --- | --- |
| `karpathy/build-nanogpt` | 无 LICENSE 文件，许可证门失败 |
| `tinygrad/tinygrad` | HEAD 核心树 347,643 行，超 10,000 行上限；需历史 revision，用户选择换掉 |

## 后续（构建严格样本前必须完成）

- [ ] 每个仓库找到 ≥3 个可复现的历史 bug（issue/PR/修复提交）。
- [ ] 构建各仓库的 CPU Docker 环境并 smoke 通过。
- [ ] 冻结严格案例清单（20 个）。
