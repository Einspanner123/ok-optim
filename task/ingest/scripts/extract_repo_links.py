#!/usr/bin/env python3
"""extract_repo_links: 论文 PDF 全文确定性提取仓库链接（仓库发现公理的实现）。

输出: 仓库域 URL 清单（github/gitlab/huggingface/zenodo/gitee）+ Code Availability
段落原文 + 全文缓存（workdir/cache/fulltext.txt）。检索不出 → found=false（确定性
none 证据，LLM 不得推翻）。PDF 从 AGENT_RUN_DIR 的 paper/ 读取（无状态，无 slug
参数）。exit: 0 成功（含 found=false）/ 3 fatal（PDF 缺失或存在多个不同 PDF）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from _state import _stdio_json, workdir

REPO_HOSTS = (r"github\.com", r"gitlab\.com", r"huggingface\.co",
              r"zenodo\.org", r"gitee\.com")
URL_RE = re.compile(
    r"https?://(?:www\.)?(" + "|".join(REPO_HOSTS) + r")/[^\s\)\],;\"'>\]]+",
    re.I)
CODE_AVAIL_RE = re.compile(
    r"(code\s+availability[:\s].{0,1200}?)"
    r"(?=\n\s*\n|\n[A-Z][^\n:]{0,48}:\s*\n|$)",  # 节头=短整行以冒号结尾
    re.I | re.S)
# 末尾斜杠与句点收尾、DOI/引用编号噪声清理
TAIL_NOISE = re.compile(r"[.)]+$")


def extract(pdf_path: Path) -> dict:
    from pypdf import PdfReader
    reader = PdfReader(str(pdf_path))
    pages = [(p.extract_text() or "") for p in reader.pages]
    text = "\n".join(pages)

    links: list[str] = []
    for m in URL_RE.finditer(text):
        url = TAIL_NOISE.sub("", m.group(0))
        if url not in links:
            links.append(url)

    avail = ""
    m = CODE_AVAIL_RE.search(text)
    if m:
        avail = " ".join(m.group(1).split())[:1200]

    return {"pages": len(pages), "text": text, "links": links,
            "availability": avail}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="extract_repo_links: PDF 全文确定性提取仓库链接")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    work = workdir()
    paper = work / "paper"
    pdfs = sorted(paper.glob("*.pdf")) if paper.is_dir() else []
    if not pdfs:
        print("fatal: paper/ 无 PDF，先运行 download_pdf", file=sys.stderr)
        return 3
    if len(pdfs) > 1:
        print("fatal: paper/ 存在多个 PDF，请确认材料", file=sys.stderr)
        return 3
    try:
        result = extract(pdfs[0])
    except Exception as exc:  # pypdf 解析失败
        print(f"fatal: PDF 解析失败: {exc}", file=sys.stderr)
        return 3

    cache = work / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "fulltext.txt").write_text(result["text"], encoding="utf-8")

    payload = {
        "found": bool(result["links"]),
        "links": result["links"], "availability": result["availability"],
        "pages": result["pages"], "fulltext_cache": str(cache / "fulltext.txt"),
    }
    if not payload["found"]:
        payload["none_evidence"] = "全文无仓库链接"
    if args.json:
        _stdio_json(payload)
    else:
        mark = f"{len(payload['links'])} 个链接" if payload["found"] \
            else "无仓库链接（确定性 none 证据）"
        print(f"extract_repo_links: {mark}")
        for url in payload["links"]:
            print(f"  - {url}")
        if payload["availability"]:
            print(f"  availability: {payload['availability'][:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
