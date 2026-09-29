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

GITHUB_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)"
    r"(?:\.git)?(?:/.*)?$", re.I)
BARE_REPO_RE = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")


def repo_key(repo_url: str) -> str:
    """归一化仓库地址为去重主键（小写）。

    GitHub URL / ``owner/repo`` → ``owner/repo``（剥 .git、尾斜杠与子路径）；
    其余仓库域（gitlab/HF/zenodo…）保留小写完整 URL。
    canonical 语义（2026-09-29 决策）：GitHub 改名后旧地址会重定向到新全名，
    本函数只做文本归一化；canonical 全名（gh api ``meta.full_name``）由 ingest
    probe 解析，并以新地址为键——仓库地址变了即视为另一篇论文。
    """
    if not isinstance(repo_url, str) or not repo_url.strip():
        raise ValueError("repo_url 为空")
    url = repo_url.strip()
    m = GITHUB_URL_RE.match(url)
    if m:
        return f"{m[1]}/{m[2]}".lower()
    m = BARE_REPO_RE.match(url)
    if m and ".." not in url:
        return f"{m[1]}/{m[2]}".lower()
    return url.rstrip("/").lower()


def _compact(row: dict) -> dict:
    return {key: row[key] for key in ("model_name", "paper_title", "year",
                                      "venue", "repo_url")}


def _rows(hub: Path) -> list[dict]:
    rows, error = load_models_csv(hub)
    if error:
        raise ValueError(error)
    return [row for row in rows if "__fields__" not in row]


def find_by_repo(hub: Path, repo_url: str) -> dict | None:
    """按仓库主键查已入库条目；未入库返回 None。"""
    key = repo_key(repo_url)
    for row in _rows(hub):
        try:
            if repo_key(row["repo_url"]) == key:
                return row
        except ValueError:
            continue
    return None


def find_by_model(hub: Path, model_name: str) -> dict | None:
    """model_name 占用检查（大小写不敏感）；未占用返回 None。"""
    if not isinstance(model_name, str) or not model_name.strip():
        raise ValueError("model_name 为空")
    want = model_name.strip().lower()
    for row in _rows(hub):
        if row["model_name"].lower() == want:
            return row
    return None


def list_entries(hub: Path) -> list[dict]:
    """全部已入库条目的紧凑名单（保持 CSV 行序）。"""
    return [_compact(row) for row in _rows(hub)]


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
