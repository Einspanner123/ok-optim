#!/usr/bin/env python3
"""apply_entry: staged 产物落位到 single-cell-hub（hub 唯一写入口）。

前置：临时拼接七规则预检全绿 + --confirm。落位后跑全量校验，失败自动回滚。
打印人工 git 步骤清单（agent 不执行 git 提交）。
exit: 0 成功 / 2 needs_human / 3 fatal。
"""

from __future__ import annotations

import argparse
import sys

from _ingest import (
    HUB, IngestError, _stdio_json, apply_to_hub, load_candidate,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="apply_entry: 落位入库（唯一 hub 写入口）")
    parser.add_argument("slug")
    parser.add_argument("--confirm", action="store_true",
                        help="确认落位（缺省只预检不写）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        cand = load_candidate(args.slug)
        if not args.confirm:
            print("needs_human: apply 前必须 --confirm（契约：落位前人工/交互确认）",
                  file=sys.stderr)
            return 2
        git_steps = apply_to_hub(HUB, cand)
    except IngestError as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        return 3

    payload = {
        "slug": args.slug, "model_name": cand["model_name"], "applied": True,
        "git_steps": git_steps,
    }
    if args.json:
        _stdio_json(payload)
    else:
        print(f"✅ 已落位: single_cell_models/{cand['model_name']}")
        print("人工 git 步骤（agent 不执行提交）:")
        for step in git_steps:
            print(f"  {step}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
