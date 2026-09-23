#!/usr/bin/env python3
"""audit_scan: 检索记录表 × hub 交叉扫描（只读），输出异常清单。

异常类别:
    hash_mismatch     ledger 已固化条目与 hub 当前数据重算哈希不一致
    unavailable       ledger 最新 verdict=unavailable
    not_applied       ledger verdict 已定但 applied=false（如 likely 待人工）
    missing_in_ledger hub 条目无 ledger 记录
    empty_column      hub CSV 行空列（github_stars 除外）/ PDF 或 repo 缺失
指定 --key 时输出该论文的详细复核信息（用户有疑问时的单点重查入口）。
exit: 0 无异常或有 key / 3 存在异常（--json 报告完整）。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from _ingest import HUB, _stdio_json, latest_by_key, load_ledger, paper_hash
from hubkit.schema import CSV_COLUMNS, ENTRIES_ROOT

# github_stars 允许为空（HF 条目或未标注）
NULLABLE = {"github_stars"}


def scan(key: str | None) -> dict:
    ledger = latest_by_key(load_ledger())
    anomalies: list[dict] = []

    # ledger → hub 固化哈希复核
    by_title = {r["title"]: r for r in ledger.values()}
    for k, rec in ledger.items():
        if key and k != key:
            continue
        if rec["verdict"] == "unavailable":
            anomalies.append({"kind": "unavailable", "key": k,
                              "detail": rec.get("evidence", "")[:200]})
        elif rec["verdict"] == "likely":
            anomalies.append({"kind": "not_applied", "key": k,
                              "detail": "likely 待人工确认"})
        elif rec["verdict"] in ("official", "author_maintained"):
            hub_row = by_title.get(rec["title"])  # hub 侧按标题对账（见下）
            current = {
                "key": k, "title": rec["title"], "verdict": rec["verdict"],
                "repo_url": rec["repo_url"], "commit": rec["commit"],
            }
            hub_commit = _hub_commit(rec["title"])
            current["commit"] = hub_commit or rec["commit"]
            if hub_commit and paper_hash(current) != rec["paper_hash"]:
                anomalies.append({"kind": "hash_mismatch", "key": k,
                                  "detail": f"ledger={rec['paper_hash']} "
                                            f"hub 重算≠已固化值"})

    # hub → ledger / 空列
    csv_path = HUB / ENTRIES_ROOT / "models.csv"
    if not csv_path.is_file():
        return {"anomalies": [{"kind": "fatal", "key": "-",
                               "detail": f"models.csv 缺失: {csv_path}"}]}
    with csv_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    titles = {r["paper_title"] for r in rows}
    for row in rows:
        name = row["model_name"]
        matched = next((k for k, rec in ledger.items()
                        if rec["title"] == row["paper_title"]), None)
        if not matched:
            anomalies.append({"kind": "missing_in_ledger", "key": name,
                              "detail": "hub 条目无检索记录表登记（存量基线除外，人工判断）"})
        empty = [c for c in CSV_COLUMNS if c not in NULLABLE and not row[c]]
        entry = HUB / ENTRIES_ROOT / name
        if not (entry / "paper" / f"{name}.pdf").is_file():
            empty.append("paper_pdf(file)")
        if not (entry / "repo").is_dir():
            empty.append("repo(dir)")
        if empty:
            anomalies.append({"kind": "empty_column", "key": name,
                              "detail": f"空列/缺失: {empty}"})

    # ledger 记录的 title 不在 hub（none 除外）
    for k, rec in ledger.items():
        if key and k != key:
            continue
        if rec["verdict"] in ("official", "author_maintained") \
                and rec["title"] not in titles:
            anomalies.append({"kind": "not_applied", "key": k,
                              "detail": "已定性但 hub 无对应条目（未 apply？）"})

    if key:
        rec = ledger.get(key)
        return {"key": key, "record": rec, "anomalies": anomalies}

    return {"scanned_hub_rows": len(rows), "ledger_keys": len(ledger),
            "anomaly_count": len(anomalies), "anomalies": anomalies}


def _hub_commit(title: str) -> str | None:
    csv_path = HUB / ENTRIES_ROOT / "models.csv"
    if not csv_path.is_file():
        return None
    with csv_path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["paper_title"] == title:
                return row["commit_hash"]
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="audit_scan: ledger × hub 交叉扫描")
    parser.add_argument("key", nargs="?", default=None,
                        help="指定检索记录表主键单点复核")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = scan(args.key)
    has_anomaly = bool(result.get("anomalies"))
    if args.json:
        _stdio_json(result)
    else:
        if args.key:
            print(f"单点复核 [{args.key}]:")
            print(json.dumps(result.get("record"), ensure_ascii=False, indent=2))
        else:
            print(f"扫描完成: hub {result.get('scanned_hub_rows', 0)} 行 / "
                  f"ledger {result.get('ledger_keys', 0)} 键")
        if has_anomaly:
            print(f"异常 {len(result['anomalies'])} 项:")
            for a in result["anomalies"]:
                print(f"  [{a['kind']}] {a['key']}: {a['detail'][:160]}")
        else:
            print("无异常")
    return 3 if has_anomaly else 0


if __name__ == "__main__":
    sys.exit(main())
