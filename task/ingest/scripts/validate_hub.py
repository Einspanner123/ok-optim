#!/usr/bin/env python3
"""validate_hub: single-cell-hub 契约校验 CLI（薄壳）。

契约逻辑全部在顶层 hubkit/ 包（可执行契约库，ingest/optimize 共用）；
本脚本只做 argparse、输出渲染与 exit code 语义，保持任务脚本规范
（--json 机器可读 / exit 0 成功 / 3 fatal）。

用法:
    validate_hub.py --hub <path>            # 全量体检
    validate_hub.py --hub <path> --model UCE  # 单模型
    validate_hub.py --hub <path> --json

exit code: 0 全绿 / 3 存在 error（报告完整输出）。不 import agent/ 层。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hubkit import validators


def main() -> int:
    parser = argparse.ArgumentParser(
        description="single-cell-hub 契约校验（七条契约规则 + 孤儿检查）"
    )
    parser.add_argument("--hub", default="single-cell-hub", help="hub 根目录")
    parser.add_argument("--model", default=None, help="只校验指定模型")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    args = parser.parse_args()

    hub = Path(args.hub).resolve()
    if not hub.is_dir():
        print(f"hub 目录不存在: {hub}", file=sys.stderr)
        return 3

    rep = validators.Report()
    validators.validate(hub, args.model, rep)

    payload = {
        "hub": str(hub),
        "scope": args.model or "all",
        "ok": rep.ok,
        "error_count": len(rep.errors),
        "errors": rep.errors,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"hub = {hub}")
        print(f"结果: {'✅ 全绿' if rep.ok else f'❌ {len(rep.errors)} 个 error'}")
        cur = None
        for e in rep.errors:
            if (e["check"], e["model"]) != cur:
                cur = (e["check"], e["model"])
                print(f"\n[{e['check']}] {e['model']}")
            print(f"  - {e['message']}")
    return 0 if rep.ok else 3


if __name__ == "__main__":
    sys.exit(main())
