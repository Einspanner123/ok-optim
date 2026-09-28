---
name: hello
description: Smoke-test environment injection, script execution, and run evidence.
---

# hello task

验证启动器、pi、path guard 与任务脚本执行链路。

## Procedure

1. 运行已声明脚本：

   ```bash
   uv run python task/hello/scripts/hello.py --json
   ```

2. 读取其 JSON 结果，汇报注入的 `env.FOO` 值、`env.AGENT_TASK`、`env.AGENT_SLUG`、
   Python 版本与工作目录。

3. 需要验证参数解析时运行：

   ```bash
   uv run python task/hello/scripts/hello.py --json --echo arbitrary-text
   ```

## Boundaries

- 不尝试直接 write/edit 操作。
- 不运行 bash 白名单外的命令。
- 简短汇报结果即停。

## Completion

- hello 脚本 exit 0 且返回合法 JSON。
- 汇报中说明 FOO 是否被注入。
- `python_executable` 位于项目 `.venv/` 内。
- 脚本为本 run_id 写入 `task_result.json`；journal 会校验它。
