#!/usr/bin/env python3
"""validate_hub: single-cell-hub 契约校验器（七条契约规则 + 孤儿检查）。

契约事实源: docs/architecture.md Part II「hub 契约」节 —— 本实现与其一一对应。
ingest 是 hub 唯一写入方，本脚本属于写入方自证合规；optimize 不使用本脚本。

用法:
    validate_hub.py --hub <path>            # 全量体检
    validate_hub.py --hub <path> --model UCE  # 单模型
    validate_hub.py --hub <path> --json

exit code: 0 全绿 / 3 存在 error（报告完整输出）。不 import agent/ 层。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

import pathspec

CSV_COLUMNS = [
    "model_name", "paper_title", "year", "venue", "paper_url",
    "repo_url", "github_stars", "framework", "license", "commit_hash",
]
KEY_COLUMNS = ["model_name", "repo_url", "commit_hash"]
CODE_EXTS = {
    ".py", ".pyi", ".r", ".sh", ".yaml", ".yml", ".toml", ".json",
    ".pkl", ".ipynb", ".cfg", ".ini", ".txt",
}  # .txt: requirements.txt 等属代码运行所需
CODE_SIZE_LIMIT = 2 * 1024 * 1024
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class Report:
    def __init__(self) -> None:
        self.errors: list[dict] = []

    def error(self, check: str, model: str, msg: str) -> None:
        self.errors.append({"check": check, "model": model, "message": msg})

    @property
    def ok(self) -> bool:
        return not self.errors


def load_rows(hub: Path, rep: Report) -> list[dict]:
    csv_path = hub / "single_cell_models" / "models.csv"
    if not csv_path.is_file():
        rep.error("csv_schema", "-", f"models.csv 不存在: {csv_path}")
        return []
    with csv_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header != CSV_COLUMNS:
            rep.error("csv_schema", "-",
                      f"CSV 表头与契约不符。\n  期望: {CSV_COLUMNS}\n  实际: {header}")
            return []
        rows = []
        for i, fields in enumerate(reader, start=2):
            if not fields:
                continue
            if len(fields) != len(CSV_COLUMNS):
                rep.error("csv_schema", fields[0] if fields else f"line{i}",
                          f"第 {i} 行列数 {len(fields)} != 10")
                continue
            rows.append(dict(zip(CSV_COLUMNS, fields)))
    return rows


def check_csv_schema(rows: list[dict], rep: Report) -> None:
    seen: dict[str, list[str]] = {k: [] for k in KEY_COLUMNS}
    for r in rows:
        name = r["model_name"]
        if not name:
            rep.error("csv_schema", "-", "存在空 model_name 行")
        if not re.fullmatch(r"(19|20)\d{2}", r["year"]):
            rep.error("csv_schema", name, f"year 非法: {r['year']!r}")
        for col in ("paper_url", "repo_url"):
            if not r[col].startswith(("http://", "https://")):
                rep.error("csv_schema", name, f"{col} 非法: {r[col]!r}")
        if r["github_stars"] and not r["github_stars"].isdigit():
            rep.error("csv_schema", name, f"github_stars 非法: {r['github_stars']!r}")
        if not (COMMIT_RE.fullmatch(r["commit_hash"]) or r["commit_hash"] == "unavailable"):
            rep.error("csv_schema", name,
                      f"commit_hash 必须为 40 位 hex 或 'unavailable': {r['commit_hash']!r}")
        if not r["framework"] or not r["license"]:
            rep.error("csv_schema", name, "framework/license 不得为空")
        for key in KEY_COLUMNS:
            seen[key].append(r[key])
    for key, vals in seen.items():
        dupes = {v for v in vals if vals.count(v) > 1}
        for v in dupes:
            rep.error("csv_schema", v, f"键 {key} 重复: {v!r}")


def parse_model_readme(path: Path) -> dict | None:
    """解析模型 README 六行 bullet；结构异常返回部分字段。"""
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


def check_model_entry(name: str, mdir: Path, row: dict, rep: Report) -> None:
    # ---- structure: 目录三件套 ----
    readme = mdir / "README.md"
    pdf = mdir / "paper" / f"{name}.pdf"
    repo = mdir / "repo"
    if not readme.is_file():
        rep.error("structure", name, f"缺少模型 README: {readme}")
        return
    if not pdf.is_file():
        rep.error("structure", name, f"缺少论文 PDF: {pdf}")
    elif not pdf.read_bytes()[:5].startswith(b"%PDF"):
        rep.error("structure", name, f"PDF magic 不符: {pdf}")
    elif pdf.stat().st_size < 50 * 1024:
        rep.error("structure", name, f"PDF 小于 50KB: {pdf} ({pdf.stat().st_size}B)")
    if not repo.is_dir():
        rep.error("structure", name, f"缺少 repo 快照: {repo}")
        return

    # ---- readme_csv: 六行与 CSV 一致 ----
    data = parse_model_readme(readme)
    if data is None:
        rep.error("structure", name, "模型 README 不可读")
        return
    if data["title"] != f"# {name}":
        rep.error("readme_csv", name, f"标题应为 '# {name}': {data['title']!r}")
    for field, col in (("paper_title", "paper_title"), ("paper_url", "paper_url"),
                       ("year", "year")):
        if field in data and data[field] != row[col]:
            rep.error("readme_csv", name, f"README {field}={data[field]!r} != CSV {row[col]!r}")
    if "paper_pdf" in data and data["paper_pdf"] != f"paper/{name}.pdf":
        rep.error("readme_csv", name, f"Paper PDF 路径异常: {data['paper_pdf']!r}")
    if "venue" in data:
        if row["venue"] not in data["venue"]:
            rep.error("readme_csv", name, f"README venue {data['venue']!r} 不含 CSV venue {row['venue']!r}")
    else:
        rep.error("readme_csv", name, "缺少 Paper 行")
    if "repo_url" in data:
        if data["repo_url"] != row["repo_url"]:
            rep.error("readme_csv", name,
                      f"README repo {data['repo_url']!r} != CSV {row['repo_url']!r}")
        is_hf = "huggingface.co" in row["repo_url"]
        expect_kind = "Author-maintained" if is_hf else "Official"
        if is_hf and data["repo_kind"] != "Author-maintained":
            rep.error("readme_csv", name,
                      f"HF 仓库措辞必须为 Author-maintained: 实际 {data['repo_kind']}")
    else:
        rep.error("readme_csv", name, "缺少 repository 行")
    if "flc" in data:
        flc = data["flc"].rstrip(".")
        parts = [p.strip() for p in flc.split(" / ")]
        if len(parts) == 3:
            fw, lic, commit = parts[0], parts[1], parts[2].strip("`")
            if not license_loose(fw, row["framework"]):
                rep.error("readme_csv", name,
                          f"framework 语义不符: {fw!r} vs CSV {row['framework']!r}")
            if not license_loose(lic, row["license"]):
                rep.error("readme_csv", name,
                          f"license 语义不符: {lic!r} vs CSV {row['license']!r}")
            if commit != row["commit_hash"]:
                rep.error("readme_csv", name,
                          f"commit 不符: {commit!r} != CSV {row['commit_hash']!r}")
        elif len(parts) == 2:
            # 形态 b: `Framework/license: F / L` —— commit 融入 Status 叙述
            fw, lic = parts
            if not license_loose(fw, row["framework"]):
                rep.error("readme_csv", name,
                          f"framework 语义不符: {fw!r} vs CSV {row['framework']!r}")
            if not license_loose(lic, row["license"]):
                rep.error("readme_csv", name,
                          f"license 语义不符: {lic!r} vs CSV {row['license']!r}")
            if row["commit_hash"] != "unavailable" and \
                    row["commit_hash"] not in data.get("status", ""):
                rep.error("readme_csv", name, "Framework/license 形态要求 Status 中含 commit hash")
        else:
            rep.error("readme_csv", name, f"Framework/license 行格式异常: {flc!r}")
    else:
        rep.error("readme_csv", name, "缺少 Framework/license 行")

    # ---- code_presence: .py >= 1 ----
    py_count = sum(1 for _ in repo.rglob("*.py"))
    if py_count == 0:
        status = data.get("status", "")
        if not re.search(r"no (runnable )?code|no python|0 \.py|incomplete", status, re.I):
            rep.error("code_presence", name,
                      f"repo 内无 .py，Status 必须说明原因: {status[:80]!r}")

    # ---- wording: 成句 + PDF downloaded ----
    ver = data.get("verification", "")
    if "verification" not in data:
        rep.error("wording", name, "缺少 Verification 行")
    elif len(ver.split()) < 6 or not re.search(r"[.;)]\s*$", ver):
        rep.error("wording", name, f"Verification 不是成句: {ver[:80]!r}")
    status = data.get("status")
    if status is None:
        rep.error("wording", name, "缺少 Status 行")
    elif "PDF downloaded" not in status:
        rep.error("wording", name, f"Status 不含 'PDF downloaded': {status[:80]!r}")


def check_ignore_safety(hub: Path, name: str, rep: Report) -> None:
    gi = hub / ".gitignore"
    if not gi.is_file():
        rep.error("ignore_safety", name, "hub .gitignore 不存在")
        return
    spec = pathspec.GitIgnoreSpec.from_lines(
        pathspec.patterns.GitWildMatchPattern, gi.read_text(encoding="utf-8").splitlines()
    )
    repo = hub / "single_cell_models" / name / "repo"
    if not repo.is_dir():
        return
    for f in repo.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix.lower() not in CODE_EXTS or f.stat().st_size >= CODE_SIZE_LIMIT:
            continue
        rel = f.relative_to(hub).as_posix()
        if spec.match_file(rel):
            rep.error("ignore_safety", name, f"代码文件被 gitignore 吞掉: {rel}")


BULLET_RE = re.compile(r"^\* \*\*\((.+?)\)")
TABLE_ROW_RE = re.compile(r"^\| \*\*(.+?)\*\* \|")


def license_loose(readme_lic: str, csv_lic: str) -> bool:
    """license 语义宽松比对：README 为自由文本描述，CSV 为规范化值。

    规则：忽略大小写；一方包含另一方，或 CSV 为 'Not declared' 且 README
    含 'not declared'，视为一致（如 'Not declared' ↔ 'not declared in
    repository root'）。
    """
    a, b = readme_lic.lower().strip(), csv_lic.lower().strip()
    return a in b or b in a


def parse_entries(readme_text: str) -> tuple[list[str], list[str]]:
    """从 README 提取 bullet 名单与对比表名单（保持出现顺序）。"""
    bullets, table, in_table = [], [], False
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


def check_readme_mirror(hub: Path, rows: list[dict], rep: Report) -> dict[str, list[str]]:
    names = [r["model_name"] for r in rows]
    outer = (hub / "README.md").read_text(encoding="utf-8")
    inner_p = hub / "single_cell_models" / "README.md"
    inner = inner_p.read_text(encoding="utf-8") if inner_p.is_file() else ""
    result: dict[str, list[str]] = {}

    # 条目数徽章 = CSV 行数
    expect_badge = f"badge/Models-{len(names)}-brightgreen"
    for tag, text in (("outer", outer), ("inner", inner)):
        if not text:
            rep.error("readme_mirror", "-", f"{'外层' if tag == 'outer' else '内层'} README 缺失")
            continue
        m = re.search(r"badge/Models-(\d+)-brightgreen", text)
        if not m:
            rep.error("readme_mirror", "-", f"{tag} README 缺少条目数徽章")
        elif int(m[1]) != len(names):
            rep.error("readme_mirror", "-", f"{tag} README 徽章计数 {m[1]} != CSV 行数 {len(names)}")

    # bullet / 对比表名单与 CSV 一致
    for tag, text in (("outer", outer), ("inner", inner)):
        bullets, table = parse_entries(text)
        result[tag] = bullets
        for kind, got in (("bullet", bullets), ("对比表", table)):
            if sorted(got) != sorted(names):
                missing = sorted(set(names) - set(got))
                extra = sorted(set(got) - set(names))
                msg = f"{tag} README {kind}名单与 CSV 不一致"
                if missing:
                    msg += f"；缺: {missing}"
                if extra:
                    msg += f"；多: {extra}"
                rep.error("readme_mirror", "-", msg)

    # 双 README 镜像（容许 §5.4 两处差异）
    outer_norm = outer.replace("./single_cell_models/", "./")
    outer_norm = re.sub(r"\]\(\./models\.csv/?\)", "](./models.csv)", outer_norm)
    if inner and outer_norm != inner:
        n = sum(1 for a, b in zip(outer_norm.splitlines(), inner.splitlines()) if a != b)
        rep.error("readme_mirror", "-",
                  f"双 README 镜像不一致（归一化后仍有差异，起始 {n} 行不等）")
    return result


def check_orphans(hub: Path, rows: list[dict], outer_bullets: list[str],
                  rep: Report) -> None:
    root = hub / "single_cell_models"
    dirs = {p.name for p in root.iterdir() if p.is_dir()} if root.is_dir() else set()
    names = {r["model_name"] for r in rows}
    for d in sorted(dirs - names):
        rep.error("orphan", d, "目录存在但 CSV 无对应行")
    for n in sorted(names - dirs):
        rep.error("orphan", n, "CSV 有行但目录缺失")
    for b in sorted(set(outer_bullets) - dirs):
        rep.error("orphan", b, "外层 README 有 bullet 但目录缺失")
    for d in sorted(dirs - set(outer_bullets)):
        rep.error("orphan", d, "目录存在但外层 README 无 bullet")


def validate(hub: Path, only: str | None, rep: Report) -> None:
    rows = load_rows(hub, rep)
    if rows:
        check_csv_schema(rows, rep)
        outer_bullets = check_readme_mirror(hub, rows, rep)["outer"]
        for r in rows:
            name = r["model_name"]
            if only and name != only:
                continue
            mdir = hub / "single_cell_models" / name
            check_model_entry(name, mdir, r, rep)
            check_ignore_safety(hub, name, rep)
        if not only:
            check_orphans(hub, rows, outer_bullets, rep)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="single-cell-hub 契约校验（七条契约规则 + 孤儿检查）"
    )
    parser.add_argument("--hub", default="single-cell-hub", help="hub 根目录")
    parser.add_argument("--model", default=None, help="只校验指定模型")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    args = parser.parse_args()

    hub = Path(args.hub).resolve()
    if not hub.is_dir():
        print(f"hub 目录不存在: {hub}", file=sys.stderr)
        return 3

    rep = Report()
    validate(hub, args.model, rep)

    payload = {
        "hub": str(hub),
        "scope": args.model or "all",
        "ok": rep.ok,
        "error_count": len(rep.errors),
        "errors": rep.errors,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"hub = {hub}")
        print(f"结果: {'✅ 全绿' if rep.ok else f'❌ {len(rep.errors)} 个 error'}")
        cur = None
        for e in rep.errors:
            if (e["check"], e["model"]) != cur:
                cur = (e["check"], e["model"])
                print(f"\n[{e['check']}] {e['model']}")
            print(f"  - {e['message']}")
    return 0 if rep.ok else 3


if __name__ == "__main__":
    sys.exit(main())
