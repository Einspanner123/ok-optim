#!/usr/bin/env python3
"""ledger_update: 检索记录表唯一写入方（.ingest/ledger.jsonl，append-only）。

verdict: official / author_maintained / likely / none / unavailable
  - none 仅接受确定性证据标记（"全文无仓库链接" 或 "no runnable code"）
  - official / author_maintained 写入即 applied=true（已入库）
  - likely / unavailable 恒 applied=false
exit: 0 成功 / 2 needs_human（candidate 缺陷）/ 3 fatal（参数非法）。
"""

from __future__ import annotations

import argparse
import sys

from _ingest import (
    NONE_EVIDENCE_MARKERS, IngestError, _stdio_json, append_ledger,
    ledger_key, load_candidate_raw, paper_hash,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="ledger_update: 登记检索结论")
    parser.add_argument("slug")
    parser.add_argument("--verdict", required=True,
                        choices=["official", "author_maintained", "likely",
                                 "none", "unavailable"])
    parser.add_argument("--evidence", default="", help="关键原文引用 / 确定性证据标记")
    parser.add_argument("--repo-url", default=None, help="覆盖 candidate 中的 repo_url")
    parser.add_argument("--commit", default=None, help="覆盖 candidate 中的 commit")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        cand = load_candidate_raw(args.slug)
    except IngestError as exc:
        print(f"needs_human: {exc}", file=sys.stderr)
        return 2

    if args.verdict == "none":
        if not any(mark in args.evidence for mark in NONE_EVIDENCE_MARKERS):
            print("fatal: verdict=none 仅接受确定性证据（evidence 须含 "
                  "'全文无仓库链接' 或 'no runnable code'）", file=sys.stderr)
            return 3

    rec = {
        "key": ledger_key(cand),
        "title": cand["paper_title"],
        "verdict": args.verdict,
        "repo_url": args.repo_url if args.repo_url is not None else cand.get("repo_url", ""),
        "commit": args.commit if args.commit is not None
                  else cand.get("commit_hash", ""),
        "evidence": args.evidence,
        "applied": args.verdict in ("official", "author_maintained"),
    }
    rec["paper_hash"] = paper_hash(rec)
    append_ledger(rec)

    if args.json:
        _stdio_json(rec)
    else:
        print(f"ledger 登记: key={rec['key']} verdict={rec['verdict']} "
              f"hash={rec['paper_hash']} applied={rec['applied']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
