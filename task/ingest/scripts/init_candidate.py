#!/usr/bin/env python3
"""init_candidate: 创建/合并更新候选工作区的 candidate.json（LLM 写盘唯一入口）。

LLM 不得直接写盘——检索/取证得到的结构化结果经本脚本落盘：
    首次:  init_candidate <slug> --payload '{"paper_title": "...", "paper_url": "...", ...}'
    合并:  同 slug 重跑即深度合并（agent 可补充 authors/code_availability/arxiv_id 等）

必填: paper_title / paper_url（seed 解析后即可获得）。
exit: 0 成功 / 2 needs_human / 3 fatal（JSON 非法或缺必填）。
"""

from __future__ import annotations

import argparse
import json
import sys

from _ingest import _stdio_json, candidate_dir, now_iso
from _ingest import IngestError

REQUIRED = ["paper_title", "paper_url"]
MERGEABLE = {"slug"}  # 不允许覆盖的字段


def deep_merge(base: dict, patch: dict) -> dict:
    for k, v in patch.items():
        if k in MERGEABLE:
            continue
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def main() -> int:
    parser = argparse.ArgumentParser(description="init_candidate: 创建/更新 candidate.json")
    parser.add_argument("slug")
    parser.add_argument("--payload", required=True, help="JSON 字符串（候选字段）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        patch = json.loads(args.payload)
        if not isinstance(patch, dict):
            raise ValueError("payload 必须是 JSON object")
    except ValueError as exc:
        print(f"fatal: payload 非法: {exc}", file=sys.stderr)
        return 3

    cdir = candidate_dir(args.slug)
    cdir.mkdir(parents=True, exist_ok=True)
    path = cdir / "candidate.json"

    if path.is_file():
        cand = deep_merge(json.loads(path.read_text(encoding="utf-8")), patch)
        action = "merged"
    else:
        cand = {"slug": args.slug, **patch}
        action = "created"

    missing = [f for f in REQUIRED if not cand.get(f)]
    if missing:
        print(f"fatal: 缺少必填字段: {missing}（paper_url 由种子解析获得）",
              file=sys.stderr)
        return 3
    cand["updated_at"] = now_iso()
    path.write_text(json.dumps(cand, ensure_ascii=False, indent=2), encoding="utf-8")

    payload = {"slug": args.slug, "action": action, "path": str(path),
               "fields": sorted(cand.keys())}
    _stdio_json(payload, args.json)
    if not args.json:
        print(f"candidate.json {action}: {path}")
        print(f"  字段: {', '.join(sorted(cand.keys()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
