#!/usr/bin/env python3
"""search_arxiv: arXiv Atom API 检索（discover 时间窗口护栏执行点）。

按关键词 + 分类查询，按 INGEST_YEAR_FROM/TO 过滤（年份窗口护栏在此执行），
submittedDate 降序。限流 ≥3s（官方政策），响应磁盘缓存。
bioRxiv 不经本脚本覆盖（无公开关键词 API）——discover 走 scholar_lookup 的
S2 keyword 模式（S2 索引 bioRxiv 预印本）+ web_search 兜底。
exit: 0 成功（含 0 结果）。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET

from _state import _stdio_json
from _net import http_get, load_dotenv

ATOM = "https://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom"}


def search(query: str, limit: int, year_from: str | None,
           year_to: str | None) -> list[dict]:
    resp = http_get(ATOM, params={
        "search_query": query, "start": 0, "max_results": min(limit * 2, 100),
        "sortBy": "submittedDate", "sortOrder": "descending",
    }, headers={
        # arXiv API 对 httpx 默认的 accept-encoding: zstd 返回 406（已知问题），
        # 显式声明 Accept 并收窄编码集
        "Accept": "application/atom+xml",
        "Accept-Encoding": "gzip, deflate",
    })
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    out: list[dict] = []
    for entry in root.findall("a:entry", NS):
        published = entry.findtext("a:published", "", NS) or ""
        year = published[:4]
        if year_from and year < year_from:
            continue
        if year_to and year > year_to:
            continue
        raw_id = entry.findtext("a:id", "", NS) or ""
        arxiv_id = re.sub(r"^https?://arxiv\.org/abs/", "", raw_id)
        arxiv_id = re.sub(r"v\d+$", "", arxiv_id)
        out.append({
            "arxiv_id": arxiv_id,
            "title": " ".join((entry.findtext("a:title", "", NS) or "").split()),
            "published": published[:10],
            "authors": [a.findtext("a:name", "", NS)
                        for a in entry.findall("a:author", NS)],
            "summary": " ".join((entry.findtext("a:summary", "", NS) or "").split())[:600],
            "paper_url": f"https://arxiv.org/abs/{arxiv_id}",
        })
        if len(out) >= limit:
            break
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="search_arxiv: arXiv 检索")
    parser.add_argument("query", help="arXiv 检索式，如 'all:single-cell foundation model' 或 cat:q-bio.GN AND all:scRNA-seq")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    load_dotenv()

    try:
        papers = search(args.query, args.limit,
                        os.environ.get("INGEST_YEAR_FROM"),
                        os.environ.get("INGEST_YEAR_TO"))
    except Exception as exc:
        print(f"fatal: arXiv 查询失败: {exc}", file=sys.stderr)
        return 3
    _stdio_json({"status": "ok", "count": len(papers), "papers": papers},
                args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
