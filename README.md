# ok-optim-agent

围绕 [single-cell-hub](./single-cell-hub)（20 个单细胞基础模型标准化快照）的两个
agent 任务：**ingest**（检索论文与官方仓库，按契约下载入库）与 **optimize**
（推理侧昇腾 NPU 适配与分级验证）。

Agent runtime 基于 [pi](https://pi.dev)（自包含于 `agent/vendor`，`ok setup` 一键
安装），项目自身只含启动器（`agent/`）与任务能力（`task/`）。LLM 调用全部经
launcher 审计（journal/usage/权限约束），人工执行 git 提交。

## 快速开始

```bash
uv sync && uv run ok setup                    # 环境准备（两步）
cp .env.example .env                          # 填 LLM endpoint 三项
uv run ok run --task hello                    # 冒烟验证
```

## 运行方案（--config）

重复使用的参数组合可固化为 YAML 方案，模板见
[configs/ingest.profiles.example.yml](configs/ingest.profiles.example.yml)：

```bash
uv run ok run --task ingest --config configs/ingest.profiles.yml --profile discover
```

- 优先级：`--set` > 方案文件 > `.env` > 父进程环境
- 文件支持 `defaults` + 命名 `profiles`；单方案文件可省 `profiles` 键直接用顶层
- `env` 键限 skill.yaml 白名单内，未知键/未知顶层键拒绝启动（fail-closed）
- flags（interactive/output/tool_budget/timeout/thinking）同样可固化
- `INGEST_APPLY_AUTHORIZED=1` 可固化进方案（命名建议带 `-apply` 后缀），
  写入端由 `apply_entry` 机械校验该值，缺省即 needs_human

## 设计文档

[docs/architecture.md](docs/architecture.md) 是项目**唯一**设计文档，分四部分：

- **Part I 系统架构**：目录结构、ok CLI、pi 集成、工具面、三层沙盒、任务权限矩阵
- **Part II 抽象层**：任务通用契约、hub 契约（格式事实源）、检索接口、验证分级——架构定义的接口/契约/模板
- **Part III 下游任务实例化**：ingest / optimize 按抽象落地的差异
- **Part IV 运营**：批处理、里程碑、风险兜底、端到端验证

Agent 行为边界见 [AGENTS.md](AGENTS.md)（pi 自动加载）。
