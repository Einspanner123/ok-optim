---
name: <name>
description: <one-sentence task objective>
---

# <name>

## Objective

<!-- State the desired result and artifact location under runs/<ts>/<task>/<slug>.
     Use a finite outcome such as done, done_with_warnings, failed, or skipped_incomplete. -->

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
