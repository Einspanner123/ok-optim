"""七条契约规则 + 孤儿检查（事实源: docs/architecture.md Part II「hub 契约」节）。

规则名与失败处置（全部 error）：
    structure       目录三件套齐全；PDF 以 %PDF 开头且 ≥50KB
    readme_csv      模型 README 六行与 CSV 行字段一致（含措辞规则）
    csv_schema      CSV 10 列名序正确；三键唯一；commit_hash 裸值合法
    readme_mirror   双 README 镜像一致；条目数徽章 = CSV 行数；名单一一对应
    code_presence   repo 内 .py ≥1；为 0 时 Status 必须说明原因（条件通过）
    ignore_safety   repo 内代码相关 <2MB 文件不被 gitignore 忽略
    wording         Verification 成句；Status 含 "PDF downloaded"

本模块无 CLI；命令入口为 task/ingest/scripts/validate_hub.py（薄壳）。
"""

from __future__ import annotations

import re
from pathlib import Path

from hubkit import ignore_rules, readers
from hubkit.schema import (
    COMMIT_HASH_PLACEHOLDER,
    COMMIT_RE,
    COUNT_BADGE,
    KEY_COLUMNS,
    PDF_MAGIC,
    PDF_MIN_BYTES,
    STATUS_NO_CODE_RE,
    STATUS_PDF_MARK,
    YEAR_RE,
)

CHECK_STRUCTURE = "structure"
CHECK_README_CSV = "readme_csv"
CHECK_CSV_SCHEMA = "csv_schema"
CHECK_README_MIRROR = "readme_mirror"
CHECK_CODE_PRESENCE = "code_presence"
CHECK_IGNORE_SAFETY = "ignore_safety"
CHECK_WORDING = "wording"
CHECK_ORPHAN = "orphan"


class Report:
    def __init__(self) -> None:
        self.errors: list[dict] = []

    def error(self, check: str, model: str, msg: str) -> None:
        self.errors.append({"check": check, "model": model, "message": msg})

    @property
    def ok(self) -> bool:
        return not self.errors


def check_csv_schema(rows: list[dict], rep: Report) -> None:
    seen: dict[str, list[str]] = {k: [] for k in KEY_COLUMNS}
    for r in rows:
        if "__fields__" in r:  # readers 保留的列数异常行
            rep.error(CHECK_CSV_SCHEMA, r["__fields__"][0] or f"line{r['__line__']}",
                      f"第 {r['__line__']} 行列数 {len(r['__fields__'])} != 10")
            continue
        name = r["model_name"]
        if not name:
            rep.error(CHECK_CSV_SCHEMA, "-", "存在空 model_name 行")
        if not YEAR_RE.fullmatch(r["year"]):
            rep.error(CHECK_CSV_SCHEMA, name, f"year 非法: {r['year']!r}")
        for col in ("paper_url", "repo_url"):
            if not r[col].startswith(("http://", "https://")):
                rep.error(CHECK_CSV_SCHEMA, name, f"{col} 非法: {r[col]!r}")
        if r["github_stars"] and not r["github_stars"].isdigit():
            rep.error(CHECK_CSV_SCHEMA, name, f"github_stars 非法: {r['github_stars']!r}")
        if not (COMMIT_RE.fullmatch(r["commit_hash"]) or r["commit_hash"] == COMMIT_HASH_PLACEHOLDER):
            rep.error(CHECK_CSV_SCHEMA, name,
                      f"commit_hash 必须为 40 位 hex 或 'unavailable': {r['commit_hash']!r}")
        if not r["framework"] or not r["license"]:
            rep.error(CHECK_CSV_SCHEMA, name, "framework/license 不得为空")
        for key in KEY_COLUMNS:
            seen[key].append(r[key])
    for key, vals in seen.items():
        dupes = {v for v in vals if vals.count(v) > 1}
        for v in dupes:
            rep.error(CHECK_CSV_SCHEMA, v, f"键 {key} 重复: {v!r}")


def check_model_entry(name: str, mdir: Path, row: dict, rep: Report) -> None:
    # ---- structure: 目录三件套 ----
    readme = mdir / "README.md"
    pdf = mdir / "paper" / f"{name}.pdf"
    repo = mdir / "repo"
    if not readme.is_file():
        rep.error(CHECK_STRUCTURE, name, f"缺少模型 README: {readme}")
        return
    if not pdf.is_file():
        rep.error(CHECK_STRUCTURE, name, f"缺少论文 PDF: {pdf}")
    elif not pdf.read_bytes()[:5].startswith(PDF_MAGIC):
        rep.error(CHECK_STRUCTURE, name, f"PDF magic 不符: {pdf}")
    elif pdf.stat().st_size < PDF_MIN_BYTES:
        rep.error(CHECK_STRUCTURE, name, f"PDF 小于 50KB: {pdf} ({pdf.stat().st_size}B)")
    if not repo.is_dir():
        rep.error(CHECK_STRUCTURE, name, f"缺少 repo 快照: {repo}")
        return

    # ---- readme_csv: 六行与 CSV 一致 ----
    data = readers.parse_model_readme(readme)
    if data is None:
        rep.error(CHECK_STRUCTURE, name, "模型 README 不可读")
        return
    if data["title"] != f"# {name}":
        rep.error(CHECK_README_CSV, name, f"标题应为 '# {name}': {data['title']!r}")
    for field, col in (("paper_title", "paper_title"), ("paper_url", "paper_url"),
                       ("year", "year")):
        if field in data and data[field] != row[col]:
            rep.error(CHECK_README_CSV, name,
                      f"README {field}={data[field]!r} != CSV {row[col]!r}")
    if "paper_pdf" in data and data["paper_pdf"] != f"paper/{name}.pdf":
        rep.error(CHECK_README_CSV, name, f"Paper PDF 路径异常: {data['paper_pdf']!r}")
    if "venue" in data:
        if row["venue"] not in data["venue"]:
            rep.error(CHECK_README_CSV, name,
                      f"README venue {data['venue']!r} 不含 CSV venue {row['venue']!r}")
    else:
        rep.error(CHECK_README_CSV, name, "缺少 Paper 行")
    if "repo_url" in data:
        if data["repo_url"] != row["repo_url"]:
            rep.error(CHECK_README_CSV, name,
                      f"README repo {data['repo_url']!r} != CSV {row['repo_url']!r}")
        is_hf = "huggingface.co" in row["repo_url"]
        if is_hf and data["repo_kind"] != "Author-maintained":
            rep.error(CHECK_README_CSV, name,
                      f"HF 仓库措辞必须为 Author-maintained: 实际 {data['repo_kind']}")
    else:
        rep.error(CHECK_README_CSV, name, "缺少 repository 行")
    if "flc" in data:
        flc = data["flc"].rstrip(".")
        parts = [p.strip() for p in flc.split(" / ")]
        if len(parts) == 3:
            fw, lic, commit = parts[0], parts[1], parts[2].strip("`")
            if not license_loose(fw, row["framework"]):
                rep.error(CHECK_README_CSV, name,
                          f"framework 语义不符: {fw!r} vs CSV {row['framework']!r}")
            if not license_loose(lic, row["license"]):
                rep.error(CHECK_README_CSV, name,
                          f"license 语义不符: {lic!r} vs CSV {row['license']!r}")
            if commit != row["commit_hash"]:
                rep.error(CHECK_README_CSV, name,
                          f"commit 不符: {commit!r} != CSV {row['commit_hash']!r}")
        elif len(parts) == 2:
            # 形态 b: `Framework/license: F / L` —— commit 融入 Status 叙述
            fw, lic = parts
            if not license_loose(fw, row["framework"]):
                rep.error(CHECK_README_CSV, name,
                          f"framework 语义不符: {fw!r} vs CSV {row['framework']!r}")
            if not license_loose(lic, row["license"]):
                rep.error(CHECK_README_CSV, name,
                          f"license 语义不符: {lic!r} vs CSV {row['license']!r}")
            if row["commit_hash"] != COMMIT_HASH_PLACEHOLDER and \
                    row["commit_hash"] not in data.get("status", ""):
                rep.error(CHECK_README_CSV, name,
                          "Framework/license 形态要求 Status 中含 commit hash")
        else:
            rep.error(CHECK_README_CSV, name, f"Framework/license 行格式异常: {flc!r}")
    else:
        rep.error(CHECK_README_CSV, name, "缺少 Framework/license 行")

    # ---- code_presence: .py >= 1 ----
    py_count = sum(1 for _ in repo.rglob("*.py"))
    if py_count == 0:
        status = data.get("status", "")
        if not STATUS_NO_CODE_RE.search(status):
            rep.error(CHECK_CODE_PRESENCE, name,
                      f"repo 内无 .py，Status 必须说明原因: {status[:80]!r}")

    # ---- wording: 成句 + PDF downloaded ----
    ver = data.get("verification", "")
    if "verification" not in data:
        rep.error(CHECK_WORDING, name, "缺少 Verification 行")
    elif len(ver.split()) < 6 or not re.search(r"[.;)]\s*$", ver):
        rep.error(CHECK_WORDING, name, f"Verification 不是成句: {ver[:80]!r}")
    status = data.get("status")
    if status is None:
        rep.error(CHECK_WORDING, name, "缺少 Status 行")
    elif STATUS_PDF_MARK not in status:
        rep.error(CHECK_WORDING, name, f"Status 不含 'PDF downloaded': {status[:80]!r}")


def check_ignore_safety(hub: Path, name: str, rep: Report) -> None:
    spec = ignore_rules.load_ignore_spec(hub)
    if spec is None:
        rep.error(CHECK_IGNORE_SAFETY, name, "hub .gitignore 不存在")
        return
    for f in ignore_rules.find_ignored_code_files(hub, name, spec):
        rel = f.relative_to(hub).as_posix()
        rep.error(CHECK_IGNORE_SAFETY, name, f"代码文件被 gitignore 吞掉: {rel}")


def license_loose(readme_lic: str, csv_lic: str) -> bool:
    """license 语义宽松比对：README 为自由文本描述，CSV 为规范化值。

    规则：忽略大小写；一方包含另一方，或 CSV 为 'Not declared' 且 README
    含 'not declared'，视为一致（如 'Not declared' ↔ 'not declared in
    repository root'）。
    """
    a, b = readme_lic.lower().strip(), csv_lic.lower().strip()
    return a in b or b in a


def check_readme_mirror(hub: Path, rows: list[dict], rep: Report) -> dict[str, list[str]]:
    names = [r["model_name"] for r in rows]
    outer = readers.read_outer_readme(hub)
    inner = readers.read_inner_readme(hub)
    result: dict[str, list[str]] = {}

    # 条目数徽章 = CSV 行数
    for tag, text in (("outer", outer), ("inner", inner)):
        if not text:
            rep.error(CHECK_README_MIRROR, "-",
                      f"{'外层' if tag == 'outer' else '内层'} README 缺失")
            continue
        m = re.search(r"badge/Models-(\d+)-brightgreen", text)
        if not m:
            rep.error(CHECK_README_MIRROR, "-", f"{tag} README 缺少条目数徽章")
        elif int(m[1]) != len(names):
            rep.error(CHECK_README_MIRROR, "-",
                      f"{tag} README 徽章计数 {m[1]} != CSV 行数 {len(names)}")

    # bullet / 对比表名单与 CSV 一致
    for tag, text in (("outer", outer), ("inner", inner)):
        bullets, table = readers.parse_entries(text)
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
                rep.error(CHECK_README_MIRROR, "-", msg)

    # 双 README 镜像（容许契约规定的两处差异）
    outer_norm = outer.replace("./single_cell_models/", "./")
    outer_norm = re.sub(r"\]\(\./models\.csv/?\)", "](./models.csv)", outer_norm)
    if inner and outer_norm != inner:
        n = sum(1 for a, b in zip(outer_norm.splitlines(), inner.splitlines()) if a != b)
        rep.error(CHECK_README_MIRROR, "-",
                  f"双 README 镜像不一致（归一化后仍有差异，起始 {n} 行不等）")
    return result


def check_orphans(hub: Path, rows: list[dict], outer_bullets: list[str],
                  rep: Report) -> None:
    root = hub / "single_cell_models"
    dirs = {p.name for p in root.iterdir() if p.is_dir()} if root.is_dir() else set()
    names = {r["model_name"] for r in rows}
    for d in sorted(dirs - names):
        rep.error(CHECK_ORPHAN, d, "目录存在但 CSV 无对应行")
    for n in sorted(names - dirs):
        rep.error(CHECK_ORPHAN, n, "CSV 有行但目录缺失")
    for b in sorted(set(outer_bullets) - dirs):
        rep.error(CHECK_ORPHAN, b, "外层 README 有 bullet 但目录缺失")
    for d in sorted(dirs - set(outer_bullets)):
        rep.error(CHECK_ORPHAN, d, "目录存在但外层 README 无 bullet")


def validate(hub: Path, only: str | None, rep: Report) -> None:
    """全量体检：七条规则 × 条目 + 孤儿检查（only 指定时跳过全表级检查）。"""
    rows, csv_err = readers.load_models_csv(hub)
    if csv_err:
        rep.error(CHECK_CSV_SCHEMA, "-", csv_err)
        return
    for r in rows:
        if "__fields__" in r:
            check_csv_schema([r], rep)
    valid_rows = [r for r in rows if "__fields__" not in r]
    if valid_rows:
        check_csv_schema(valid_rows, rep)
        outer_bullets = check_readme_mirror(hub, valid_rows, rep)["outer"]
        for r in valid_rows:
            name = r["model_name"]
            if only and name != only:
                continue
            mdir = hub / "single_cell_models" / name
            check_model_entry(name, mdir, r, rep)
            check_ignore_safety(hub, name, rep)
        if not only:
            check_orphans(hub, valid_rows, outer_bullets, rep)
