#!/usr/bin/env python3
"""hub_query: 以仓库地址为主键的入库去重查询（薄壳，逻辑在 hubkit/readers.py）。

去重主键 = 归一化仓库地址 owner/repo（小写；GitHub 改名经重定向后的 canonical
全名——仓库地址变了即视为另一篇论文，2026-09-29 决策）。
model_name 撞已入库条目是唯一身份类人工闸（needs_human），绝不自动合并。

用法:
    hub_query.py --repo <url|owner/repo>   # 该仓库是否已入库
    hub_query.py --model <name>            # model_name 是否已被占用
    hub_query.py --list --json             # 现有条目紧凑名单

exit: 0 查询成功（found 与否都是结论）/ 3 fatal（hub 缺失或表头不符）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hubkit import readers


def _human(payload: dict) -> None:
    print(f"hub = {payload['hub']}")
    if "query" in payload and "repo" in payload["query"]:
        mark = f"已入库 → {payload['match']['model_name']}" if payload["found"] else "未入库"
        print(f"repo 键 {payload['query']['repo']}: {mark}")
    if "query" in payload and "model" in payload["query"]:
        mark = f"已被占用 → {payload['match']['model_name']}" if payload["found"] else "未占用"
        print(f"model_name {payload['query']['model']}: {mark}")
    if "entries" in payload and "query" not in payload:
        for entry in payload["entries"]:
            print(
                f"- {entry['model_name']} ({entry['year']} {entry['venue']}): {entry['repo_url']}"
            )
        print(f"共 {payload['count']} 条")


def main() -> int:
    parser = argparse.ArgumentParser(description="hub_query: 仓库主键去重查询")
    parser.add_argument("--hub", default="single-cell-hub", help="hub 根目录")
    parser.add_argument("--repo", default=None, help="仓库 URL 或 owner/repo")
    parser.add_argument("--model", default=None, help="model_name 占用检查")
    parser.add_argument("--list", action="store_true", help="列出全部条目")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not (args.repo or args.model or args.list):
        parser.error("至少提供 --repo / --model / --list 之一")
    hub = Path(args.hub).resolve()
    if not hub.is_dir():
        print(f"fatal: hub 目录不存在: {hub}", file=sys.stderr)
        return 3
    try:
        payload: dict = {"hub": str(hub)}
        if args.repo:
            match = readers.find_by_repo(hub, args.repo)
            payload.update(
                query={"repo": readers.repo_key(args.repo)},
                found=match is not None,
                match=readers._compact(match) if match else None,
            )
        if args.model:
            match = readers.find_by_model(hub, args.model)
            payload.update(
                query={**(payload.get("query") or {}), "model": args.model},
                found=payload.get("found", False) or match is not None,
                match=readers._compact(match) if match else payload.get("match"),
            )
        if args.list:
            entries = readers.list_entries(hub)
            payload.update(entries=entries, count=len(entries))
    except ValueError as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        return 3
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _human(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
