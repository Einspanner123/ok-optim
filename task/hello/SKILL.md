---
name: hello
description: M0 冒烟任务——验证 env 注入、脚本执行通道与轨迹输出。运行 hello 脚本并向用户汇报注入的环境变量。
---

# hello 任务

M0 验收件。目的：验证启动器 → pi → path-guard → task 脚本 的完整链路。

## 流程

1. 运行脚本（唯一正确的调用形态）:

   ```bash
   uv run python task/hello/scripts/hello.py --json
   ```

2. 读取脚本的 JSON 输出，向用户汇报:
   - `env.FOO` 的值（本次是否注入了 FOO）
   - `env.AGENT_TASK` / `AGENT_SLUG`（启动器注入的运行时变量）
   - `python` 版本与 `cwd`

3. 如需额外验证参数解析，可运行:

   ```bash
   uv run python task/hello/scripts/hello.py --json --echo arbitrary-text
   ```

## 边界

- 不要尝试 write/edit/修改任何文件（会被 path-guard 拒绝）
- 不要运行白名单外的 bash 命令
- 任务完成后简短汇报结论即可，不要展开额外工作

## 完成标准

- hello 脚本以 exit 0 运行且输出了合法 JSON
- 汇报中明确给出 FOO 注入值（或指出未注入）
