#!/usr/bin/env python3
"""fetch_page: 通用网页取证（1req/2s/域名，按 URL 缓存）。

want=code_availability  提取 Code Availability 段落原文（官方性强证据，
                        经 agent 登记进 candidate.json 的 code_availability 字段）
want=metadata           提取 citation_* 元数据（citation_pdf_url 供 PDF 多源链）
want=full（默认）       全文落缓存 cache/pages/，返回路径 + 头部摘录

exit: 0 成功 / 3 fatal（请求失败）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys

from _ingest import INGEST_ROOT, _stdio_json
from _net import http_get, load_dotenv

PAGES = INGEST_ROOT / "cache" / "pages"
AVAIL_RE = re.compile(
    r"(?:code\s+(?:and\s+data\s+)?availability|data\s+and\s+code\s+availability)"
    r"[^\n]{0,80}?[:：]?\s*(.{100,1500}?)(?=\n\s*\n|</p>|$)", re.I | re.S)
META_RE = re.compile(
    r'<meta\s+name="([^"]+)"\s+content="([^"]*)"', re.I)
STRIP = re.compile(r"<[^>]+>")


def strip_html(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    return STRIP.sub(" ", text)


def extract_availability(html: str) -> str:
    m = AVAIL_RE.search(html) or AVAIL_RE.search(strip_html(html))
    if not m:
        return ""
    return " ".join(m.group(1).split())[:1200]


def extract_metadata(html: str) -> dict:
    out: dict[str, str] = {}
    for name, content in META_RE.findall(html):
        name = name.lower()
        if name.startswith("citation_"):
            out[name[len("citation_"):]] = content.strip()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="fetch_page: 网页取证")
    parser.add_argument("url")
    parser.add_argument("--want", choices=["code_availability", "metadata", "full"],
                        default="full")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    load_dotenv()

    try:
        resp = http_get(args.url)
        resp.raise_for_status()
    except Exception as exc:
        print(f"fatal: fetch 失败: {exc}", file=sys.stderr)
        return 3
    html = resp.text
    PAGES.mkdir(parents=True, exist_ok=True)
    page_path = PAGES / (hashlib.sha256(args.url.encode()).hexdigest()[:16] + ".html")
    if not page_path.is_file():
        page_path.write_text(html, encoding="utf-8")

    payload: dict = {"url": args.url, "cache": str(page_path)}
    if args.want == "code_availability":
        payload["code_availability"] = extract_availability(html)
    elif args.want == "metadata":
        payload["metadata"] = extract_metadata(html)
    else:
        text = strip_html(html)
        payload["text_head"] = " ".join(text.split())[:2000]
        payload["text_len"] = len(text)
    _stdio_json(payload, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
