# Phase 2 Benchmark 评估

> 评估日期：2026-08-13

## 结论摘要

严格主集目标 20 个，实际构建 **6 个**；探索集 **10 个**。短少原因已如实记录，未通过降低隐藏测试 / 溯源 / 三次重放门槛来凑数。

## 选定仓库

| 仓库 | 许可证 | Python 非空行 |
| --- | --- | ---: |
| `karpathy/minGPT` | MIT | 1,057 |
| `karpathy/nanoGPT` | MIT | 1,085 |
| `karpathy/nanochat` | MIT | 6,731 |
| `karpathy/makemore` | MIT | 603 |

## 严格主集（6 个，全部三次重放通过）

| 案例 | 仓库 | Bug 类型 | 难度 | 类别 |
| --- | --- | --- | --- | --- |
| `mingpt-h01` | minGPT | 采样掩码数值错误 | small | numerical |
| `nanogpt-h01` | nanoGPT | flash 下访问缺失 bias | small | model_training |
| `nanogpt-h02` | nanoGPT | eval 模式仍 dropout | small | model_training |
| `nanogpt-h03` | nanoGPT | top_k 超词表崩溃 | small | model_training |
| `nanochat-h01` | nanochat | top_k=0 崩溃 | small | model_training |
| `makemore-h01` | makemore | RNN 递归失效 | small | model_training |

分布：类别 2 种（numerical / model_training）；难度全部 small。

## 探索集（10 个）

被拒候选与不够干净的历史修复，按失败门槛分类：

| 失败门槛 | 案例 | 说明 |
| --- | --- | --- |
| 需外部数据 | `nanogpt-x01` `nanochat-x01` | input.txt / 训练好的 tokenizer 未入库 |
| 需网络 | `nanogpt-x02` | 下载 HF 权重 |
| 在训练循环内 | `nanogpt-x03` `nanogpt-x04` | `while True` 无法干净 import |
| 设备/dtype 相关 | `nanochat-x02` `nanochat-x03` | CUDA/MPS/bf16 |
| 部分复现 | `mingpt-x01` | bug 在 notebook 里 |
| 无可观察失败 | `mingpt-x02` | 仅打印计数变化 |
| 微妙非确定性 | `nanogpt-x05` | 权重绑定初始化顺序 |

## 被拒仓库

| 仓库 | 拒绝原因 |
| --- | --- |
| `karpathy/build-nanogpt` | 无 LICENSE |
| `tinygrad/tinygrad` | HEAD 核心树 347k 行超上限 |
| `minitorch/minitorch` | 无 LICENSE |
| `google-deepmind/optax` | 核心 39k 行超上限 + JAX |
| `Lightning-AI/torchmetrics` | 规模偏大 |
| `pytorch/ignite` | 规模过大 |

## 已知局限

- 严格样本仅 6 个，不足以支撑强因果结论（计划原目标 20）。
- 类别与难度分布偏窄（2 类、全 small），代表性有限。
- 严格样本全部来自 karpathy 教程仓库，风格与规模高度相似。
- 探索集样本未做三次重放验证，仅作单独报告，不与严格集合并。
