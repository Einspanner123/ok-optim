#!/usr/bin/env python3
"""validate_entry: staged 产物临时拼接到 hub 副本跑七条契约规则（不落位）。

流程：stage_splice（candidate + staged → temp hub 结构）→ hubkit.validators
单模型校验。exit: 0 全绿 / 2 needs_human（candidate 缺陷）/ 3 校验未过（报告完整）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from _ingest import (
    HUB, IngestError, _stdio_json, load_candidate, stage_splice, validate_spliced,
)


def run(slug: str) -> dict:
    cand = load_candidate(slug)
    tmp = Path(tempfile.mkdtemp(prefix="ingest-validate-"))
    try:
        stage_splice(HUB, cand, tmp)
        rep = validate_spliced(tmp, cand["model_name"])
        return {
            "slug": slug, "model_name": cand["model_name"], "ok": rep.ok,
            "error_count": len(rep.errors), "errors": rep.errors,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="validate_entry: staged 产物七规则预检（不落位）")
    parser.add_argument("slug")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = run(args.slug)
    except IngestError as exc:
        print(f"needs_human: {exc}", file=sys.stderr)
        return 2
    if args.json:
        _stdio_json(result)
    else:
        mark = "✅ 全绿" if result["ok"] else f"❌ {result['error_count']} 个 error"
        print(f"validate_entry [{result['model_name']}]: {mark}")
        for e in result["errors"]:
            print(f"  [{e['check']}] {e['message']}")
    return 0 if result["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
