# IssueFlow

[English](README.md) · [阶段一评估记录](docs/phase-1-evaluation.md) · [三分钟演示稿](docs/demo-script.md)

IssueFlow 是一个面向 Apple Silicon Mac 的、可复现的单 Agent 软件修复 MVP。它让一个真实 DeepSeek Agent 处理 5 个固定版本的 `karpathy/micrograd` 案例，在默认断网的 Docker 沙箱中执行登记过的验证命令，并把全过程保存到 SQLite 和可下载的 JSON。

## 先理解项目全貌

一次修复会依次经过下面 7 个环节：

```mermaid
flowchart LR
    A["Benchmark 案例"] --> B["故障 Git 工作区"]
    B --> C["Docker 复现"]
    C --> D["单 Agent 修复"]
    D --> E["独立复验"]
    E --> F["确定性判定 + Reviewer"]
    F --> G["SQLite / JSON / 页面"]
```

- **Benchmark**：登记仓库、固定版本、Issue、复现命令、验证命令、来源和参考补丁。
- **工作区准备**：为每次运行单独克隆仓库，把故障版本建立成干净的 Git 基线。
- **Docker 沙箱**：默认断网，只执行登记过的测试，并限制 CPU、内存、进程数和时间。
- **单 Agent**：只能使用 4 种工具——搜索、读取文件、应用补丁、运行登记测试。
- **RunService**：把复现、Agent、独立验证、diff、Reviewer 和存储串成一次完整运行。
- **Reviewer**：先执行不可绕过的功能判定，再提供模型审查意见；模型意见不能推翻失败的测试。
- **TraceStore 与页面**：按顺序保存并展示脱敏后的证据。

你可以把它理解为：Benchmark 规定题目，沙箱限定考场，Agent 解题，RunService 负责流程，Reviewer 复核，TraceStore 保存答题记录。

## 前置条件

- Apple Silicon Mac（M1 或更新）和 macOS
- Docker Desktop 已启动，并至少可用 4 GB 内存
- Git
- Python 3.11 或更新版本，推荐 Python 3.12
- 用于真实 Agent 运行的 DeepSeek API Key

## 第一次启动

在项目根目录依次执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

在**同一个终端窗口**设置 API Key：

```bash
export DEEPSEEK_API_KEY='你的-key'
```

不要把 Key 写入代码、截图、JSON 或 Git 提交。可以只检查变量是否存在，不显示真实内容：

```bash
test -n "$DEEPSEEK_API_KEY" && echo "API key 已设置"
```

然后构建环境、验证 5 个案例并启动页面：

```bash
make docker-build
make verify-benchmarks
make demo
```

浏览器打开 [http://localhost:8501](http://localhost:8501)，推荐先选 `constructed-01`，点击“开始真实修复”。运行时页面会自动检查进度；结束后可以查看成功判定、代码 diff、测试证据、Reviewer、耗时、工具调用、Token、成本和完整时间线，也可以下载 JSON。

运行数据默认保存在 `.issueflow/`。如需换目录，可设置 `ISSUEFLOW_DATA_DIR`；模型和接口地址可分别通过 `ISSUEFLOW_MODEL`、`ISSUEFLOW_BASE_URL` 调整。

## 常用验证命令

```bash
make lint                 # 代码规范与格式检查
make docker-build         # 构建固定沙箱镜像
make test                 # 全量测试，包含 Docker 端到端测试
make test-e2e             # 只验证 Docker/Git/Agent/SQLite/JSON 完整链路
make verify-benchmarks    # 验证 5 个案例：修复前失败、参考修复后通过
make verify-phase-1       # 按正确顺序执行阶段一完整验收
```

执行 `make test`、`make test-e2e` 和 `make verify-phase-1` 时 Docker 必须处于运行状态。`make verify-benchmarks` 还需要联网克隆固定版本的公开仓库。

## 5 个样本是什么

所有样本都来自 MIT 许可证的 [karpathy/micrograd](https://github.com/karpathy/micrograd)，并固定到完整的 40 位 Git 版本号。

| 案例 | 类型 | 含义 |
| --- | --- | --- |
| `historical-01` | 历史样本 | 来源于上游公开提交的共享计算图梯度修复。 |
| `constructed-01` | 自建样本 | 人为注入的一元负号回归。 |
| `constructed-02` | 自建样本 | 人为注入的幂运算梯度回归。 |
| `constructed-03` | 自建样本 | 人为注入的 ReLU 零边界回归。 |
| `constructed-04` | 自建样本 | 人为注入的除法回归。 |

“历史样本”表示缺陷与修复来自公开上游历史；“自建样本”表示 IssueFlow 在固定的上游版本上注入有记录的最小故障，不能把它描述成 micrograd 曾经真实发生过的历史 Bug。完整来源、构造说明和补丁见 [`benchmarks/micrograd.yaml`](benchmarks/micrograd.yaml)。

## 安全边界

### 预算档位

每个登记案例都声明一个档位：`historical-01` 使用 `medium`，当前的自建案例使用 `small`。

| 档位 | 工具调用 | 补丁次数 | 秒数 | 输入 Token | 输出 Token | 成本上限 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `small` | 12 | 2 | 300 | 30,000 | 6,000 | $0.05 |
| `medium` | 18 | 4 | 450 | 50,000 | 8,000 | $0.10 |
| `large` | 24 | 6 | 600 | 80,000 | 12,000 | $0.20 |

预算档位只规定 Agent 最多可以使用多少资源，不保证任何一次运行或未来样本一定修复成功。

- 阶段一页面只能选择 5 个登记案例，不能输入任意仓库或 Shell 命令。
- Docker 默认断网，容器文件系统只读；只有隔离工作区可写，并限制 CPU、内存、进程数和超时时间。
- Agent 工具和测试命令使用白名单；文件路径会检查是否逃逸工作区。
- 工具调用、补丁次数、运行时间、Token 与成本都有硬预算。
- Key 只从进程环境读取，写入轨迹前会脱敏，也不会挂载进修复容器。
- 功能成功由“故障先能复现、修复后验证通过、diff 非空、预算未耗尽”决定；Reviewer 只是附加证据。
- 阶段一不会推送代码、创建 PR、运行隐藏测试或修复任意项目。

## 已保存证据与当前局限

仓库内的[真实 Agent 轨迹](artifacts/phase-1/constructed-01-live-run.json)来自一次真实 DeepSeek 成功运行：先写入 SQLite，再脱敏导出为 JSON。[阶段一评估记录](docs/phase-1-evaluation.md)会严格区分“参考补丁回放”和“真实 Agent 结果”。

目前它仍是教学型 MVP：只覆盖一个小型 Python 项目、公开验证、单 Agent、单机和本地 SQLite。真实模型输出会有波动，API 可用性会影响运行；页面中的“总耗时”是已持久化执行步骤的耗时之和，不等同于包含模型网络等待的完整墙钟时间。
