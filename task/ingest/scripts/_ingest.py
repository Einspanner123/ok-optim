"""ingest 脚本共享层：candidate 读写、staged 产物、临时拼接预检、hub 落位。

仅 task/ingest/scripts/ 内部使用（同任务共享，不跨任务 import）。
落位写动作（apply）只经 apply_entry 调用本模块。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from hubkit import ignore_rules, render, validators
from hubkit.schema import COMMIT_HASH_PLACEHOLDER, MODELS_CSV

REPO_ROOT = Path(__file__).resolve().parents[3]
HUB = REPO_ROOT / "single-cell-hub"
INGEST_ROOT = REPO_ROOT / ".ingest"
LEDGER = INGEST_ROOT / "ledger.jsonl"

VERDICTS = {"official", "author_maintained", "likely", "none", "unavailable"}
# none 终态仅接受的确定性证据标记（extract_repo_links / repo 快照 0 .py）
NONE_EVIDENCE_MARKERS = ("全文无仓库链接", "no runnable code")

CSV_COLUMNS = [
    "model_name", "paper_title", "year", "venue", "paper_url",
    "repo_url", "github_stars", "framework", "license", "commit_hash",
]

REQUIRED_FIELDS = ["model_name", "paper_title", "year", "venue", "paper_url",
                   "repo_url", "framework", "license", "verdict"]


class IngestError(Exception):
    """needs_human 类失败（exit 2）。"""


def candidates_root() -> Path:
    return INGEST_ROOT / "candidates"


def candidate_dir(slug: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", slug):
        raise IngestError(f"非法 slug: {slug!r}")
    return candidates_root() / slug


def load_candidate_raw(slug: str) -> dict:
    """宽松版：网络/取证阶段（verdict 未定）用，仅要求 slug 与基本字段存在。"""
    path = candidate_dir(slug) / "candidate.json"
    if not path.is_file():
        raise IngestError(f"candidate.json 不存在: {path}")
    cand = json.loads(path.read_text(encoding="utf-8"))
    cand.setdefault("slug", slug)
    return cand


def load_candidate(slug: str) -> dict:
    """严格版：format/apply 阶段用——要求 verdict 已定且可入库。"""
    cand = load_candidate_raw(slug)
    missing = [f for f in REQUIRED_FIELDS if not cand.get(f)]
    if missing:
        raise IngestError(f"candidate.json 缺少必填字段: {missing}")
    if cand["verdict"] not in VERDICTS:
        raise IngestError(f"verdict 非法: {cand['verdict']!r}")
    if cand["verdict"] == "likely":
        raise IngestError("verdict=likely 需人工确认后方可 format/apply（needs_human）")
    if cand["verdict"] == "none":
        raise IngestError("verdict=none 不入库：请直接 ledger_update 登记，无需 format")
    if cand["verdict"] == "unavailable":
        raise IngestError("verdict=unavailable 为瞬态，重查成功后再 format")
    commit = cand.setdefault("commit_hash", COMMIT_HASH_PLACEHOLDER)
    if not (re.fullmatch(r"[0-9a-f]{40}", commit) or commit == COMMIT_HASH_PLACEHOLDER):
        raise IngestError(f"commit_hash 非法: {commit!r}")
    cand.setdefault("github_stars", "")
    return cand


def build_row(cand: dict) -> dict:
    row = {c: str(cand.get(c, "") or "") for c in CSV_COLUMNS}
    row["model_name"] = cand["model_name"]
    return row


def paper_pdf(cand_dir: Path, name: str) -> Path:
    return cand_dir / "paper" / f"{name}.pdf"


def repo_dir(cand_dir: Path) -> Path:
    return cand_dir / "repo"


def status_tail(cand: dict) -> str:
    """Status 行 "PDF downloaded; " 之后的仓库获取方式说明（含 0 .py 原因）。"""
    repo = repo_dir(candidate_dir(cand["slug"]))
    py_count = sum(1 for _ in repo.rglob("*.py")) if repo.is_dir() else 0
    if py_count == 0:
        return "no runnable code (.py) in repository snapshot"
    commit = cand.get("commit_hash", COMMIT_HASH_PLACEHOLDER)
    if commit == COMMIT_HASH_PLACEHOLDER:
        return "repository snapshot acquired from official source (commit unavailable)"
    return f"repository cloned from official source at commit {commit}"


def staged_dir(slug: str) -> Path:
    return candidate_dir(slug) / "staged"


# ---- 临时拼接预检 / 落位（apply_entry 与 validate_entry 共用） ----

def stage_splice(hub: Path, cand: dict, tmp: Path) -> None:
    """把 candidate + staged 产物拼接到 tmp（hub 结构副本），供七规则预检。"""
    name = cand["model_name"]
    cand_dir = candidate_dir(cand["slug"])
    # 仅复制受影响的顶层文件与 single_cell_models 的表头件
    (tmp / "single_cell_models").mkdir(parents=True)
    for fname in ("README.md", ".gitignore"):
        src = hub / fname
        if src.is_file():
            shutil.copy(src, tmp / fname)
    for fname in ("README.md", "models.csv"):
        shutil.copy(hub / "single_cell_models" / fname, tmp / "single_cell_models" / fname)

    # 新条目目录三件套
    entry = tmp / "single_cell_models" / name
    (entry / "paper").mkdir(parents=True)
    (entry / "repo").mkdir()
    shutil.copy(staged_dir(cand["slug"]) / "README.md", entry / "README.md")
    pdf = paper_pdf(cand_dir, name)
    if not pdf.is_file():
        raise IngestError(f"论文 PDF 缺失: {pdf}")
    shutil.copy(pdf, entry / "paper" / pdf.name)
    if repo_dir(cand_dir).is_dir():
        shutil.copytree(repo_dir(cand_dir), entry / "repo", dirs_exist_ok=True)

    # CSV 追加一行（三键查重）
    csv_path = tmp / "single_cell_models" / "models.csv"
    text = csv_path.read_text(encoding="utf-8")
    row = build_row(cand)
    if not text.endswith("\n"):
        text += "\n"
    text += render.render_csv_row(row)
    csv_path.write_text(text, encoding="utf-8")

    # 双 README 拼接 + gitignore 例外块
    bullet_out = render.render_bullet(row, "single_cell_models/")
    bullet_in = render.render_bullet(row, "")
    table_row = render.render_table_row(row)
    (tmp / "README.md").write_text(
        render.splice_readme((tmp / "README.md").read_text(encoding="utf-8"),
                             bullet_out, table_row), encoding="utf-8")
    inner = tmp / "single_cell_models" / "README.md"
    inner.write_text(
        render.splice_readme(inner.read_text(encoding="utf-8"), bullet_in, table_row),
        encoding="utf-8")
    exc = (staged_dir(cand["slug"]) / "gitignore_exception.txt").read_text(encoding="utf-8")
    if exc.strip() and exc.strip() != "（无）":
        gi = tmp / ".gitignore"
        gi.write_text(gi.read_text(encoding="utf-8") + "\n" + exc, encoding="utf-8")


def validate_spliced(tmp: Path, name: str) -> validators.Report:
    rep = validators.Report()
    validators.validate(tmp, name, rep)
    return rep


def apply_to_hub(hub: Path, cand: dict) -> list[str]:
    """落位到真实 hub；返回打印给人工的 git 步骤。先备份，全量校验失败即回滚。"""
    name = cand["model_name"]
    tmp = Path(_mk_tmp())
    try:
        stage_splice(hub, cand, tmp)
        rep = validate_spliced(tmp, name)
        if not rep.ok:
            raise IngestError("临时拼接预检未全绿，拒绝落位: "
                              + json.dumps(rep.errors, ensure_ascii=False)[:1000])

        # 备份将被修改的文件
        backups = {}
        for rel in ("README.md", ".gitignore", "single_cell_models/README.md",
                    "single_cell_models/models.csv"):
            src = hub / rel
            if src.is_file():
                backups[rel] = src.read_text(encoding="utf-8")
        try:
            entry = hub / "single_cell_models" / name
            if entry.exists():
                shutil.rmtree(entry)
            shutil.copytree(tmp / "single_cell_models" / name, entry)
            for rel in backups:
                shutil.copy(tmp / rel, hub / rel)
        except Exception:
            for rel, text in backups.items():
                (hub / rel).write_text(text, encoding="utf-8")
            raise

        # 落位后全量回归
        full = validators.Report()
        validators.validate(hub, None, full)
        if not full.ok:
            for rel, text in backups.items():
                (hub / rel).write_text(text, encoding="utf-8")
            shutil.rmtree(hub / "single_cell_models" / name, ignore_errors=True)
            raise IngestError("落位后全量校验失败，已回滚: "
                              + json.dumps(full.errors, ensure_ascii=False)[:1000])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return [
        f"cd {hub}",
        f"git add single_cell_models/{name} single_cell_models/models.csv",
        "git add single_cell_models/README.md README.md .gitignore",
        f'git commit -m "feat: add {name} entry"',
    ]


def _mk_tmp() -> str:
    return tempfile.mkdtemp(prefix="ingest-splice-")


# ---- ledger ----

def ledger_key(cand: dict) -> str:
    doi = cand.get("doi")
    if doi:
        return f"doi:{doi}"
    norm = re.sub(r"[^\w\s]", "", cand["paper_title"].lower()).strip()
    norm = re.sub(r"\s+", " ", norm)
    return "title:" + hashlib.sha256(norm.encode()).hexdigest()[:12]


def paper_hash(rec: dict) -> str:
    basis = "|".join(str(rec.get(k, "")) for k in
                     ("key", "title", "verdict", "repo_url", "commit"))
    return hashlib.sha256(basis.encode()).hexdigest()[:12]


def load_ledger() -> list[dict]:
    if not LEDGER.is_file():
        return []
    out = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def latest_by_key(records: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for rec in records:
        out[rec["key"]] = rec  # append-only，后写覆盖
    return out


def append_ledger(rec: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    rec["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stdio_json(payload: dict, as_json: bool = True) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2 if as_json else None)
          if as_json else json.dumps(payload, ensure_ascii=False)[:4000])
