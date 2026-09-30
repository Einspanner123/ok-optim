---
name: <name>
description: <one-sentence task objective>
---

# <name>

## Objective

<!-- 说明期望结果与产物在 runs/<ts>/<task>/<slug> 下的位置。
     使用有限结局：done、done_with_warnings、failed 或 skipped_incomplete。 -->

## Budget and stopping rules

<!-- 硬性上限，显式计数。不要凭「差不多快做完了」的感觉。
     没有这些约束，run 会一直重试失败的通道，直到被工具预算杀掉。 -->

- **通道预算：每 run 共 N 次调用** <昂贵的或限流的脚本>。
  合并计数；限流、空结果、超时都算已消耗的调用次数。
- **重试预算：每个操作 2 次重试，之后该通道本 run 关闭。**
  运行时在反复失败后会拒绝第三次同形尝试。
- **工具预算：`AGENT_TOOL_BUDGET`。** 在其约 80% 处停止调用工具，以便最终总结还放得下。
- **立即停止**：达到目标时或所有通道耗尽时。如实停止并记录阻塞是成功；
  烧完预算反复重试不是。

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
