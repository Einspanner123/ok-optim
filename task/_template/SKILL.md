---
name: <name>
description: <one-sentence task objective>
---

# <name>

## Objective

<!-- State the desired result and artifact location under runs/<ts>/<task>/<slug>.
     Use a finite outcome such as done, done_with_warnings, failed, or skipped_incomplete. -->

## Budget and stopping rules

<!-- Hard limits, counted explicitly. Do not rely on a feeling of being "close to done".
     Without these, a run will retry a failing channel until the tool budget kills it. -->

- **Channel budget: N calls total per run** to <the expensive or rate-limited scripts>.
  Count them together; rate limits, empty results, and timeouts all count as used calls.
- **Retry budget: 2 retries per operation, then that channel is closed for this run.**
  The runtime refuses a third identical attempt after repeated failures.
- **Tool budget: `AGENT_TOOL_BUDGET`.** Stop calling tools by ~80% of it so a summary still fits.
- **Stop immediately** when the target is met or every channel is exhausted. Stopping
  honestly with recorded blockers is a success; burning the budget retrying is not.

## Procedure

<!-- Number the steps. Each step should name one declared task script and its failure path.
     Exit 2 means needs_human; exit 3 means fatal. Record the failing stage. -->

1. TODO
2. TODO

## Tools

<!-- Bash runs only declared task scripts and approved read-only commands.
     Direct write/edit is unavailable. Read access is restricted by path guard. -->

## Boundaries

<!-- Specify hub read/write permissions, network use, and prohibited actions. -->

## Human decisions

<!-- In interactive mode, list decisions that use the available question tool.
     In noninteractive mode, record pending / needs_human and finish safely. -->

| Decision | Interactive | Noninteractive |
|---|---|---|
| TODO | Ask the human | Record pending |

## Completion

<!-- Define verifiable terminal outcomes and required report or pending artifacts. -->

- TODO
