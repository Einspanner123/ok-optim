# task/_template —— skill 标准结构模板

所有 `task/<name>/` 必须遵循本结构（契约定义：docs/architecture.md Part II
「任务通用契约」节）。新建任务时复制本目录并替换占位符。

```text
task/<name>/
├── skill.yaml         # 机器可读元数据：name / description / required_env / optional_env / scripts 清单
├── SKILL.md           # Agent Skills 标准：流程、工具用法、边界、何时问人、完成标准（唯一生效版）
├── SKILL_CN.md        # SKILL.md 的中文直译版（供模型与人工中文阅读；逐段对应，改英文版后须同步）
├── references/        # 任务专属知识（语料笔记、领域事实），按需创建；Agent Skills 标准可选目录名
└── scripts/           # 确定性 py 脚本薄壳，可独立执行与单测
```

## 分层规则

| 层 | 放什么 | 不放什么 |
|---|---|---|
| `hubkit/`（顶层包） | hub 契约：schema 常量、读接口、校验器、渲染纯函数 | 任务编排、网络策略、exit code 语义 |
| `task/<name>/scripts/` | argparse CLI 薄壳：参数解析、`--json`、exit code（0/2/3）、runs/ 产物落盘、网络限流与缓存 | 契约逻辑（上收 hubkit） |
| `SKILL.md` | LLM 编排说明书：目标 → 流程 → 工具 → 边界 → 何时问人 → 完成标准 | 重复 hubkit 已固化的格式细节（引用即可） |

- task 脚本可 `import hubkit`；任务之间互不 import；不 import `agent/`
- LLM 不直接写盘：一切产物写入经本任务 scripts
- 契约变更：改 architecture.md → 同步 hubkit/ → 全量回归（validate_hub）

## 新建任务清单

1. `cp -r task/_template task/<name>`
2. 填 `skill.yaml`：`required_env` 经 assembly preflight 校验，缺失即拒绝启动
3. 填 `SKILL.md` 各节（参见模板内 TODO 标记），并同步 `SKILL_CN.md` 中文对照版
4. 每个脚本按 `scripts/_script.py` 骨架实现，并在 `skill.yaml` 的 `scripts:` 登记
   （path-guard 依据该清单拦截未声明脚本的 bash 调用）
