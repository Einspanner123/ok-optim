---
name: <name>
description: <one-sentence task objective>
---

# <name>

## Objective

<!-- 说明期望结果与产物在 runs/<ts>/<task>/<slug> 下的位置。
     使用有限结局：done、done_with_warnings、failed 或 skipped_incomplete。 -->

## Procedure

<!-- 编号各步骤。每步指名一个已声明的任务脚本及其失败路径。
     exit 2 = needs_human；exit 3 = fatal。记录失败阶段。 -->

1. TODO
2. TODO

## Tools

<!-- bash 仅运行已声明的任务脚本与批准的只读命令。
     直接 write/edit 不可用。read 访问受 path guard 路径白名单限制。 -->

## Boundaries

<!-- 写明 hub 读/写权限、网络使用与禁止事项。 -->

## Human decisions

<!-- 交互模式：列出用可用提问工具处理的决策。
     非交互模式：记录 pending / needs_human 并安全收尾。 -->

| Decision | Interactive | Noninteractive |
|---|---|---|
| TODO | Ask the human | Record pending |

## Completion

<!-- 定义可验证的终态结局与必需的报告/pending 产物。 -->

- TODO
