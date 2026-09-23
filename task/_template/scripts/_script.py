#!/usr/bin/env python3
"""任务脚本骨架：argparse 薄壳 + exit code 语义 + runs/ 产物落盘。

复制后替换 <name>/<script_name>。契约规则（脚本规范）：
- argparse 严格模式；--json 输出机器可读结果
- exit code: 0 成功 / 2 needs_human（附原因）/ 3 fatal
- 不 import agent/、不跨任务 import；可 import 顶层 hubkit/ 契约库
- 超长输出截断（摘要 ≤4k chars），全文落 runs/ 供 read 追查
- 幂等设计：同参数重跑安全
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

RUN_DIR = Path(os.environ.get("AGENT_RUN_DIR", "runs")).resolve()


def run(slug: str, limit: int) -> dict:
    """确定性主逻辑；返回机器可读结果 dict。

    TODO: 实现任务逻辑。契约级复用（读 hub / 校验 / 渲染）走 hubkit：
        from hubkit import readers, validators
    """
    _ = slug, limit
    return {"ok": True}


def main() -> int:
    parser = argparse.ArgumentParser(description="<script_name>: TODO")
    parser.add_argument("slug", help="工作区 slug")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    args = parser.parse_args()

    out_dir = RUN_DIR / args.slug
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = run(args.slug, args.limit)
    except RuntimeError as exc:  # needs_human 类失败
        print(f"needs_human: {exc}", file=sys.stderr)
        return 2

    (out_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
