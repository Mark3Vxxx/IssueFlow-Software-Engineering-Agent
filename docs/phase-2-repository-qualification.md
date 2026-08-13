# Phase 2 仓库资格筛选

> 状态：候选已编码，等待用户审批后冻结 3–4 个仓库与配额。

## 候选清单（固定顺序）

| 优先级 | 仓库 | 许可证 | 目标配额 |
| ---: | --- | --- | ---: |
| 1 | `karpathy/minGPT` | MIT | 5 |
| 2 | `karpathy/nanoGPT` | MIT | 5 |
| 3 | `karpathy/build-nanogpt` | MIT | 4 |
| 4 | `tinygrad/tinygrad` | MIT | 6 |
| 5 | `karpathy/nanochat`（fallback） | MIT | — |
| 6 | `karpathy/makemore`（fallback） | MIT | — |

主候选目标配额合计 20。

## 筛选门（每个候选都必须通过）

- 许可证是 MIT / Apache-2.0 / BSD-3-Clause，且与上游许可证文件一致。
- 一个 CPU-only、版本固定的 Docker 环境能构建。
- 至少 3 个候选历史修复有可复现的失败。
- Agent 可见 checkout 能排除 post-fault 历史。
- 每次复现 120 秒内完成。

## 检查方法

运行只读检查（clone 到 `.issueflow/candidate-cache`）：

```bash
.venv/bin/python scripts/inspect_repository_candidates.py
```

输出每个仓库的许可证文件、Python 非空行数、`.py` 提交数、测试入口。

## 待办

- [ ] 运行检查脚本，记录许可证 / 行数 / 环境 smoke / 候选修复数。
- [ ] 用户审批 3–4 个仓库与配额。
- [ ] 冻结本文件的选定仓库与精确配额。
