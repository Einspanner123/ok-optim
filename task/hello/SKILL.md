---
name: hello
description: Smoke-test environment injection, script execution, and run evidence.
---

# hello task

Verify the launcher, pi, path guard, and task-script execution path.

## Procedure

1. Run the declared script:

   ```bash
   uv run python task/hello/scripts/hello.py --json
   ```

2. Read its JSON result and report the injected `env.FOO` value, `env.AGENT_TASK`, `env.AGENT_SLUG`, the Python version, and the working directory.

3. To verify argument parsing when needed, run:

   ```bash
   uv run python task/hello/scripts/hello.py --json --echo arbitrary-text
   ```

## Boundaries

- Do not attempt direct write/edit operations.
- Do not run commands outside the bash allowlist.
- Report the result briefly and stop.

## Completion

- The hello script exits 0 and returns valid JSON.
- The report states whether FOO was injected.
- `python_executable` is inside the project `.venv/`.
- The script writes `task_result.json` for this run_id; the journal verifies it.
