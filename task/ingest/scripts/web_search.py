#!/usr/bin/env python3
"""web_search: DuckDuckGo 广域检索（ddgs）——仅线索级。

结果永不作为官方性判定依据（契约「线索与证据两级分离」）；失败返回
status=web_search_unavailable（exit 0 降级不阻塞），agent 可改走 gh api。
exit: 0 成功或降级 / 3 fatal（参数问题）。
"""

from __future__ import annotations

import argparse
import sys

from _net import load_dotenv
from _state import _stdio_json


def search(query: str, limit: int) -> dict:
    try:
        from ddgs import DDGS
    except ImportError:
        return {"status": "web_search_unavailable", "reason": "ddgs 未安装", "results": []}
    try:
        hits = DDGS().text(query, max_results=limit)
    except Exception as exc:
        return {"status": "web_search_unavailable", "reason": str(exc)[:200], "results": []}
    return {
        "status": "ok",
        "results": [
            {"title": h.get("title"), "url": h.get("href"), "snippet": (h.get("body") or "")[:300]}
            for h in hits
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="web_search: DuckDuckGo（仅线索级）")
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    load_dotenv()
    _stdio_json({"query": args.query, **search(args.query, args.limit)}, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
