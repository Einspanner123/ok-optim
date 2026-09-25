---
name: <name>
description: <一句话描述任务目标>
---

# <name>

## 目标

<!-- 任务要达成什么、产出物落在哪里（runs/<ts>/<task>/<slug>/）。
     完成状态必须是有限集合之一，如 done / done_with_warnings / failed / skipped_incomplete。 -->

## 流程步骤

<!-- 按阶段编号编排：每步指向一个 scripts/ 脚本 + 失败分支。
     约定：失败不静默——脚本 exit 2 走 needs_human，exit 3 终止并记录 failing_stage。 -->

1. TODO
2. TODO

## 工具用法

<!-- bash 仅限白名单：uv run python task/<name>/scripts/<已声明脚本>.py + 只读命令。
     write/edit 已被 path-guard 拒绝；产物写入只能经脚本。
     read 白名单：task/、runs/、AGENTS.md、single-cell-hub/（只读）。 -->

## 边界与禁止事项

<!-- 权限矩阵（architecture.md）：本任务对 hub 是写入方还是纯读方？
     网络能力？禁止事项逐条列出。 -->

## 何时问人

<!-- 交互模式（--interactive）：列出需要使用本轮交互工具的裁决点。
     默认非交互 / batch：不提供交互工具，直接
     改走 needs_human 分支（写 pending.json + 安全收尾），不得猜测用户意图。 -->

| 决策点 | 交互模式（--interactive） | 非交互（默认） |
|---|---|---|
| TODO | 使用已提供的交互工具 | pending |

## 完成标准（Done 的定义）

<!-- 逐条可验收：终态明确、产物完整（report/pending 三选一：staged 完整 / 已 apply / 明确 pending）。 -->

- TODO
