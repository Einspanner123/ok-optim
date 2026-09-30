#!/usr/bin/env python3
"""github_search: 仓库搜索 + 官方性 probe（判定规则固化，非 LLM 判定）。

search 模式（默认）: /search/repositories 关键词搜索 → 候选清单（仅线索）。
--probe 模式: 对给定仓库采集证据 → 确定性 verdict（契约「官方性判定规则」）:
    CodeAvailability ∧ README互认  → official
    README互认 ∧ 作者匹配          → official
    仅作者匹配                     → author_maintained
    单项强证据                     → likely（needs_human，不得静默入库）
    其余                           → unverified（继续找候选）
证据: 调用方经 context 直传（无状态契约）——论文 Code Availability 声明
（来自 fetch_page）｜repo README 互认（标题/arXiv id）｜作者匹配（family name ↔
owner login/name）｜元数据旁证（非 fork、description 提及）。
context 键: paper_title / arxiv_id / authors / code_availability。
exit: 0 成功（verdict 是结论性输出）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys

from _state import _stdio_json
from _net import gh_api, gh_raw, load_dotenv


def parse_repo(repo_url: str) -> str:
    m = re.search(r"github\.com/([\w.-]+/[\w.-]+)", repo_url)
    if not m:
        raise SystemExit(f"fatal: 非 GitHub 仓库 URL: {repo_url!r}")
    full = m.group(1).rstrip("/")
    if ".." in full:
        raise SystemExit(f"fatal: 非法仓库路径: {repo_url!r}")
    return full


def search_repos(query: str, limit: int) -> list[dict]:
    resp = gh_api("/search/repositories", params={"q": query, "per_page": limit})
    resp.raise_for_status()
    return [{"full_name": it["full_name"], "url": it["html_url"],
             "stars": it["stargazers_count"], "fork": it["fork"],
             "description": (it.get("description") or "")[:200]}
            for it in resp.json().get("items", [])]


def _norm(text: str) -> list[str]:
    """归一化为词元列表（小写、去标点）。"""
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()


def author_match(authors: list[str], owner: dict, profile: dict) -> tuple[bool, str]:
    owner_tokens = set(_norm(owner.get("login", "")))
    owner_tokens |= set(_norm(profile.get("name") or ""))
    for author in authors:
        family = author.split()[-1].lower() if author else ""
        if len(family) >= 3 and family in {t for t in owner_tokens if len(t) >= 3}:
            return True, f"author family name {family!r} ↔ owner {owner.get('login')!r}"
    return False, ""


def probe(full_name: str, context: dict | None = None) -> dict:
    """官方性判定：context 为调用方直传的论文事实（无状态，不读落盘候选）。"""
    cand = context or {}
    title = cand.get("paper_title", "")
    arxiv_id = cand.get("arxiv_id", "")
    authors = cand.get("authors", [])
    code_availability = bool(cand.get("code_availability"))

    meta = gh_api(f"/repos/{full_name}")
    if meta.status_code == 404:
        return {"verdict": "unverified", "repo": full_name,
                "evidence": {"error": "repo 404"}}
    meta.raise_for_status()
    meta = meta.json()
    owner = meta.get("owner") or {}

    # canonical 改名：gh api 返回全名 ≠ 请求名（大小写差异不算）→ 标注旧地址。
    # 去重键（repo 字段）取 canonical 新地址（SKILL「仓库地址变了即另一篇论文」）。
    canonical = meta.get("full_name") or full_name
    renamed_from = full_name if canonical.lower() != full_name.lower() else ""

    readme = ""
    r = gh_api(f"/repos/{full_name}/readme",
               headers={"Accept": "application/vnd.github.raw"})
    if r.status_code == 200:
        readme = r.text.lower()
    readme_tokens = " ".join(_norm(readme))

    # README 互认：标题前三词短语（论文短名，如 "universal cell embeddings"）
    # 或 arXiv id。完整标题短语过严——多数 README 只引用短名。
    readme_rec = False
    if title:
        toks = _norm(title)
        phrase = " ".join(toks[:3])
        readme_rec = bool(phrase) and phrase in readme_tokens
    if not readme_rec and arxiv_id and arxiv_id.lower() in readme:
        readme_rec = True

    profile = {}
    p = gh_api(f"/users/{owner.get('login', '')}")
    if p.status_code == 200:
        profile = p.json()
    matched, match_detail = author_match(authors, owner, profile)

    evidence = {
        "code_availability": code_availability,
        "readme_recognition": readme_rec,
        "author_match": match_detail or False,
        "not_fork": not meta.get("fork"),
        "description": (meta.get("description") or "")[:200],
        "stars": meta.get("stargazers_count"),
    }
    if readme_rec and (code_availability or matched):
        verdict = "official"
    elif matched:
        verdict = "author_maintained"
    elif readme_rec or code_availability:
        verdict = "likely"
    else:
        verdict = "unverified"
    result = {"verdict": verdict, "repo": canonical,
              "repo_url": meta.get("html_url"), "evidence": evidence}
    if renamed_from:
        result["renamed_from"] = renamed_from
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="github_search: 搜索/probe")
    parser.add_argument("query", help="search: 关键词；--probe: 仓库 URL 或 owner/repo")
    parser.add_argument("--probe", action="store_true", help="官方性判定模式")
    parser.add_argument("--title", default=None, help="论文标题（probe 证据）")
    parser.add_argument("--arxiv-id", default=None, help="arXiv id（probe 证据）")
    parser.add_argument("--authors", default=None, help="作者，逗号分隔（probe 证据）")
    parser.add_argument("--code-availability", default=None,
                        help="论文的 Code Availability 声明/URL（probe 证据；非空即视为有声明）")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    load_dotenv()

    try:
        if args.probe:
            full = parse_repo(args.query)
            context = {
                "paper_title": args.title or "",
                "arxiv_id": args.arxiv_id or "",
                "authors": [a.strip() for a in (args.authors or "").split(",") if a.strip()],
                "code_availability": args.code_availability or "",
            }
            result = probe(full, context)
            if result["verdict"] == "likely":
                print("needs_human: 单项强证据 → likely，不得静默入库", file=sys.stderr)
        else:
            result = {"status": "ok", "results": search_repos(args.query, args.limit)}
    except SystemExit as exc:
        raise
    except Exception as exc:
        print(f"fatal: gh api 失败: {exc}", file=sys.stderr)
        return 3
    _stdio_json(result, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
