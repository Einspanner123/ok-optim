#!/usr/bin/env python3
"""hello task 脚本: M0 冒烟——验证 env 注入与脚本执行通道。

exit code: 0 成功 / 2 needs_human / 3 fatal（任务通用契约）。
不 import agent/ 内核，可独立执行与单测。
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(
        description="hello 冒烟脚本: 输出环境注入快照"
    )
    parser.add_argument(
        "--json", action="store_true", help="输出机器可读 JSON（默认人类可读）"
    )
    parser.add_argument("--echo", default=None, help="回显任意文本（验证参数透传）")
    args = parser.parse_args()

    payload = {
        "task": "hello",
        "script": "hello.py",
        "env": {
            "FOO": os.environ.get("FOO"),
            "AGENT_TASK": os.environ.get("AGENT_TASK"),
            "AGENT_SLUG": os.environ.get("AGENT_SLUG"),
            "AGENT_RUN_DIR": os.environ.get("AGENT_RUN_DIR"),
        },
        "python": sys.version.split()[0],
        "cwd": os.getcwd(),
        "echo": args.echo,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for key, val in payload["env"].items():
            print(f"{key} = {val}")
        print(f"python = {payload['python']}")
        print(f"cwd = {payload['cwd']}")
        if args.echo:
            print(f"echo = {args.echo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
