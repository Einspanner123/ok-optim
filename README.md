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
- flags（interactive/output/tool_budget/failure_limit/timeout/thinking）同样可固化
- `INGEST_APPLY_AUTHORIZED=1` 可固化进方案（命名建议带 `-apply` 后缀），
  写入端由 `apply_entry` 机械校验该值，缺省即 needs_human

## 设计文档

[docs/architecture.md](docs/architecture.md) 是项目**唯一**设计文档，按通用设计文档规范组织；
未实现的功能一律标注 **`状态：未实现（TBD）`**，并列出"已定"与"待定"。

| 章节 | 内容 |
|---|---|
| §1–§4 | 摘要、背景与动机、目标与非目标、功能与非功能需求 |
| §5–§6 | 总体设计（系统上下文 / 分层与目录 / ok CLI / 决策记录 / 备选框架对比）、运行时（pi 集成与采样参数） |
| §7 | 安全模型：威胁模型、三层约束、bash 命令规划器、可读路径契约、fail-closed 两种形态、任务权限矩阵、交互模型 |
| §8 | 编排与护栏：工具预算与失败重复、`task_result.json` 结束协议、退出码、运行内提示词 |
| §9–§12 | hub 契约（格式事实源）、任务通用契约、检索接口抽象、NPU 验证分级抽象 |
| §13–§14 | 下游任务实例化（ingest 已实现 / optimize 未实现）、运营（批处理、里程碑与实现状态） |
| §15–§18 | 测试与质量门禁、失败模式与风险、开放问题与已知缺口、附录（术语表、中英双版本约定） |

实现状态：**M0 / M0.5 / M1 / M2 已实现；M3（optimize）、M4（批量与待人工项）未实现。**

Agent 行为边界见 [AGENTS.md](AGENTS.md)（pi 自动加载）。
