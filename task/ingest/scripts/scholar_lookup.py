#!/usr/bin/env python3
"""scholar_lookup: 种子解析与论文元数据（S2 Graph API，discover 主通道）。

种子五形态分流（契约「检索接口抽象」）:
    doi:<doi> / arxiv:<id> / pmid:<id>  → S2 单篇 lookup
    URL                                  → 提取 DOI/arXiv id，提取失败报错转 fetch_page
    纯标题（无空格也接受）               → S2 title match
    含空格的关键词串                     → S2 keyword search（discover 自主发现引擎，
                                           S2 索引 bioRxiv 预印本，受 INGEST_YEAR_FROM/TO 过滤）

fields: 标题/作者/年份/venue/DOI/abstract/openAccessPdf/引用数。
限流 1req/s；响应磁盘缓存。exit: 0 成功（含 0 结果）/ 2 needs_human（形态无法解析）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time

from _state import _stdio_json
from _net import http_get, load_dotenv

S2 = "https://api.semanticscholar.org/graph/v1"
FIELDS = ("title,externalIds,year,venue,authors,abstract,openAccessPdf,"
          "citationCount,publicationDate")


def classify(seed: str) -> tuple[str, str]:
    for prefix, sid in (("doi:", "DOI:"), ("arxiv:", "ARXIV:"), ("pmid:", "PMID:")):
        if seed.startswith(prefix):
            return "paper", sid + seed[len(prefix):]
    if seed.startswith(("http://", "https://")):
        m = (re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})", seed)
             or re.search(r"doi\.org/(10\.[^\s]+)$", seed))
        if not m:
            raise SystemExit("needs_human: URL 无法提取 DOI/arXiv id，"
                             "请先 fetch_page 取 citation 元数据再以 doi:/arxiv: 重试")
        val = m.group(1)
        sid = f"ARXIV:{val}" if "arxiv" in seed.lower() else f"DOI:{val}"
        return "paper", sid
    return ("search", seed)


def paper_detail(s2_id: str) -> dict:
    resp = http_get(f"{S2}/paper/{s2_id}", params={"fields": FIELDS})
    if resp.status_code == 404:
        return {"status": "not_found", "s2_id": s2_id}
    resp.raise_for_status()
    return resp.json()


def keyword_search(query: str, limit: int) -> list[dict]:
    import os
    params: dict = {"query": query, "fields": FIELDS, "limit": min(limit, 100)}
    y_from, y_to = os.environ.get("INGEST_YEAR_FROM"), os.environ.get("INGEST_YEAR_TO")
    if y_from and y_to:
        params["year"] = f"{y_from}-{y_to}"
    elif y_from:
        params["year"] = f"{y_from}-"
    elif y_to:
        params["year"] = f"-{y_to}"
    resp = http_get(f"{S2}/paper/search", params=params)
    # S2 匿名搜索档共享池常 429：退避重试（5s/15s）
    for wait in (5, 15, 0):
        if resp.status_code != 429 or wait == 0:
            break
        time.sleep(wait)
        resp = http_get(f"{S2}/paper/search", params=params)
    if resp.status_code == 404:
        return []
    if resp.status_code == 429:
        raise SystemExit("needs_human: S2 搜索持续限流（429），请稍后重试或申请免费 key")
    resp.raise_for_status()
    return resp.json().get("data", [])


def compact(p: dict) -> dict:
    """供 agent 消费的精简视图 + 后续脚本的必要字段。"""
    ext = p.get("externalIds") or {}
    oa = (p.get("openAccessPdf") or {}).get("url")
    doi = ext.get("DOI")
    paper_url = (f"https://doi.org/{doi}" if doi else
                 (f"https://arxiv.org/abs/{ext.get('ArXiv')}" if ext.get("ArXiv")
                  else p.get("url", "")))
    return {
        "title": p.get("title"), "year": p.get("year"), "venue": p.get("venue"),
        "doi": doi, "arxiv_id": ext.get("ArXiv"), "pmid": ext.get("PubMed"),
        "paper_url": paper_url, "openaccesspdf_url": oa,
        "authors": [a.get("name") for a in (p.get("authors") or [])],
        "citation_count": p.get("citationCount"), "abstract": (p.get("abstract") or "")[:600],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="scholar_lookup: S2 种子解析/检索")
    parser.add_argument("seed", help="doi:... / arxiv:... / pmid:... / URL / 标题 / 关键词")
    parser.add_argument("--limit", type=int, default=10, help="keyword 模式返回条数")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    load_dotenv()

    mode, ident = classify(args.seed)
    if mode == "paper":
        result = paper_detail(ident)
        payload = ({"status": "ok", "mode": "paper", "paper": compact(result)}
                   if result.get("title") else result)
    else:
        hits = keyword_search(ident, args.limit)
        payload = {"status": "ok", "mode": "search", "count": len(hits),
                   "papers": [compact(p) for p in hits]}
    _stdio_json(payload, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
