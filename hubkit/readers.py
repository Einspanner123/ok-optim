"""hub 读接口：models.csv、模型 README 六行 bullet、双 README 条目名单。

ingest 与 optimize 共用；只读，不写不校验。
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from hubkit.schema import CSV_COLUMNS, INNER_README, MODELS_CSV, OUTER_README

# 双 README 条目名单提取
BULLET_RE = re.compile(r"^\* \*\*\((.+?)\)")
TABLE_ROW_RE = re.compile(r"^\| \*\*(.+?)\*\* \|")


def load_models_csv(hub: Path) -> tuple[list[dict], str | None]:
    """读取 models.csv。

    返回 (rows, error)；表头不符时 error 非空、rows 为空。
    行级问题（列数错误等）由调用方校验器报告，这里只做结构化解析。
    """
    csv_path = hub / MODELS_CSV
    if not csv_path.is_file():
        return [], f"models.csv 不存在: {csv_path}"
    rows: list[dict] = []
    with csv_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header != CSV_COLUMNS:
            return [], (f"CSV 表头与契约不符。\n  期望: {CSV_COLUMNS}\n  实际: {header}")
        for i, fields in enumerate(reader, start=2):
            if not fields:
                continue
            if len(fields) != len(CSV_COLUMNS):
                # 保留行号信息，交由校验器报告
                rows.append({"__line__": i, "__fields__": fields})
                continue
            rows.append(dict(zip(CSV_COLUMNS, fields)))
    return rows, None


def parse_model_readme(path: Path) -> dict | None:
    """解析模型 README 六行 bullet；文件不存在返回 None，结构异常返回部分字段。"""
    if not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    data: dict = {"title": lines[0].strip() if lines else ""}
    for line in lines:
        m = re.match(r"- Paper: \[(.+?)\]\((.+?)\) \((.+), (\d{4})\)\s*$", line)
        if m:
            data.update(paper_title=m[1], paper_url=m[2], venue=m[3].strip(), year=m[4])
            continue
        m = re.match(r"- Paper PDF: `(.+?)`\s*$", line)
        if m:
            data["paper_pdf"] = m[1]
            continue
        m = re.match(r"- (Official|Author-maintained)( code)? repository: (\S+)\s*$", line)
        if m:
            data["repo_kind"] = m[1]
            data["repo_url"] = m[3]
            continue
        m = re.match(r"- Verification: (.+)$", line)
        if m:
            data["verification"] = m[1]
            continue
        m = re.match(r"- Framework/license(/commit)?: (.+)$", line)
        if m:
            data["flc"] = m[2]
            continue
        m = re.match(r"- Status: (.+)$", line)
        if m:
            data["status"] = m[1]
            continue
    return data


def parse_entries(readme_text: str) -> tuple[list[str], list[str]]:
    """从双 README 提取 bullet 名单与对比表名单（保持出现顺序）。"""
    bullets: list[str] = []
    table: list[str] = []
    in_table = False
    for line in readme_text.splitlines():
        if line.startswith("## 📊"):
            in_table = True
            continue
        if in_table and line.startswith("## "):
            in_table = False
        m = BULLET_RE.match(line)
        if m:
            bullets.append(m[1])
            continue
        if in_table:
            m = TABLE_ROW_RE.match(line)
            if m and m[1] != "Model":
                table.append(m[1])
    return bullets, table


def read_outer_readme(hub: Path) -> str:
    return (hub / OUTER_README).read_text(encoding="utf-8")


def read_inner_readme(hub: Path) -> str:
    p = hub / INNER_README
    return p.read_text(encoding="utf-8") if p.is_file() else ""
