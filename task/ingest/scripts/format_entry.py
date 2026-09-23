#!/usr/bin/env python3
"""format_entry: candidate.json → staged 全套入库产物（不落位）。

产物（.ingest/candidates/<slug>/staged/）:
    README.md                 模型 README 六行 bullet
    csv_row.csv               models.csv 追加行
    bullet_outer.md           外层 README 条目块
    bullet_inner.md           内层 README 条目块
    table_row.md              对比表行
    gitignore_exception.txt   例外块（或"（无）"）
    summary.json              渲染摘要

exit: 0 成功 / 2 needs_human / 3 fatal。
"""

from __future__ import annotations

import argparse
import json
import sys

from _ingest import (
    HUB, IngestError, build_row, candidate_dir, load_candidate, now_iso,
    repo_dir, staged_dir, status_tail, _stdio_json,
)
from hubkit import ignore_rules, render


def suggest_gitignore_exception(row: dict, cdir) -> str:
    """模拟快照落入 hub 后哪些代码相关 <2MB 文件会被忽略规则吞掉。"""
    spec = ignore_rules.load_ignore_spec(HUB)
    exc_lines: list[str] = []
    if spec and repo_dir(cdir).is_dir():
        for f in repo_dir(cdir).rglob("*"):
            if not f.is_file() or not ignore_rules.is_code_file(f):
                continue
            rel = (f"single_cell_models/{row['model_name']}/repo/"
                   + f.relative_to(repo_dir(cdir)).as_posix())
            if spec.match_file(rel):
                exc_lines.append(f"!{rel}")
    if exc_lines:
        return ("# 例外：{} 源码快照内的代码相关文件（gitignore 大类规则误伤，需可追踪）\n{}"
                .format(row["model_name"], "\n".join(exc_lines)))
    return "（无）"


def run(slug: str) -> dict:
    cand = load_candidate(slug)
    cdir = candidate_dir(slug)
    row = build_row(cand)
    staged = staged_dir(slug)
    staged.mkdir(parents=True, exist_ok=True)

    (staged / "README.md").write_text(
        render.render_model_readme(row["model_name"], row, cand["verdict"],
                                   status_tail(cand)), encoding="utf-8")
    (staged / "csv_row.csv").write_text(render.render_csv_row(row), encoding="utf-8")
    (staged / "bullet_outer.md").write_text(
        render.render_bullet(row, "single_cell_models/"), encoding="utf-8")
    (staged / "bullet_inner.md").write_text(
        render.render_bullet(row, ""), encoding="utf-8")
    (staged / "table_row.md").write_text(render.render_table_row(row), encoding="utf-8")
    exc_text = suggest_gitignore_exception(row, cdir)
    (staged / "gitignore_exception.txt").write_text(exc_text + "\n", encoding="utf-8")

    summary = {
        "slug": slug, "model_name": row["model_name"], "verdict": cand["verdict"],
        "commit_hash": row["commit_hash"], "staged": sorted(
            p.name for p in staged.iterdir()), "checked_at": now_iso(),
    }
    (staged / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="format_entry: 渲染 staged 入库产物")
    parser.add_argument("slug")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        summary = run(args.slug)
    except IngestError as exc:
        print(f"needs_human: {exc}", file=sys.stderr)
        return 2
    except render.ContractError as exc:
        print(f"needs_human: {exc}", file=sys.stderr)
        return 2
    if args.json:
        _stdio_json(summary)
    else:
        print(f"staged 产物就绪: {staged_dir(args.slug)}")
        for f in summary["staged"]:
            print(f"  - {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
