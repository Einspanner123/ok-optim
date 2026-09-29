"""Internal staging and filesystem application. Hub formatting belongs to hubkit.

Stateless: the entry payload comes from the caller (CLI --payload), materials
live under the run workdir (paper/, repo/), and staged files under staged/.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import _state as state
from _state import IngestError
from hubkit import readers, render, validators, ignore_rules
from hubkit.schema import (CSV_COLUMNS, INDEX_FILES, COMMIT_HASH_PLACEHOLDER, COMMIT_RE,
                          ENTRIES_ROOT, entry_name, entry_path)


def digest(path: Path) -> str | None:
    if not path.exists():
        return None
    if path.is_symlink():
        raise IngestError(f"不接受符号链接: {path}")
    h = hashlib.sha256()
    if path.is_file():
        with path.open("rb") as fh:
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(block)
    else:
        for file in sorted(path.rglob("*")):
            if file.is_symlink():
                raise IngestError(f"不接受符号链接: {file}")
            if file.is_file():
                h.update(file.relative_to(path).as_posix().encode())
                h.update(digest(file).encode())
    return h.hexdigest()


def load_entry(payload: dict) -> tuple[dict, dict]:
    """Validate the caller-supplied entry payload; return (entry, csv_row)."""
    if not isinstance(payload, dict):
        raise IngestError("payload 必须是 JSON object")
    missing = state.missing_entry_fields(payload)
    if missing:
        raise IngestError(f"条目缺少字段: {missing}")
    entry_name(payload["model_name"])
    if payload["verdict"] not in {"official", "author_maintained"}:
        raise IngestError("仅 official / author_maintained 可暂存和入库")
    entry = {**payload}
    entry.setdefault("commit_hash", COMMIT_HASH_PLACEHOLDER)
    if not (COMMIT_RE.fullmatch(entry["commit_hash"]) or entry["commit_hash"] == COMMIT_HASH_PLACEHOLDER):
        raise IngestError("非法 commit_hash")
    row = {key: str(entry.get(key, "") or "") for key in CSV_COLUMNS}
    return entry, row


def material_paths(name: str) -> tuple[Path, Path]:
    """Materials for the current entry: run workdir paper/ + repo/."""
    work = state.workdir()
    paper = state.contained(work, work / "paper")
    pdfs = sorted(paper.glob("*.pdf")) if paper.is_dir() else []
    preferred = paper / f"{name}.pdf"
    if preferred in pdfs:
        pdf = preferred
    elif len(pdfs) == 1 or (pdfs and len({digest(p) for p in pdfs}) == 1):
        pdf = pdfs[0]
    else:
        raise IngestError("paper/ 缺 PDF 或存在多个不同 PDF，请确认材料")
    repo = state.contained(work, work / "repo")
    if not repo.is_dir() or not any(repo.iterdir()):
        raise IngestError("repo/ 源码快照缺失")
    state.contained(work, pdf)
    return pdf, repo


def baseline(hub: Path, name: str) -> dict:
    return {rel: digest(state.contained(hub, hub / rel)) for rel in (*INDEX_FILES, entry_path(name))}


def materialize(hub: Path, staged: Path, summary: dict, tmp: Path) -> None:
    """Build a read-only view of existing entries plus the exact prospective files."""
    entries = tmp / ENTRIES_ROOT
    entries.mkdir(parents=True)
    if (hub / ENTRIES_ROOT).is_dir():
        for old in (hub / ENTRIES_ROOT).iterdir():
            if old.is_dir() and old.name != summary["model_name"]:
                (entries / old.name).symlink_to(old.resolve(), target_is_directory=True)
    for rel in summary["files"]:
        target = tmp / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(staged / rel, target)
    root = tmp / entry_path(summary["model_name"])
    (root / "paper").mkdir()
    shutil.copyfile(summary["pdf"], root / "paper" / f"{summary['model_name']}.pdf")
    shutil.copytree(summary["repo"], root / "repo")


def check(hub: Path, only: str | None = None) -> dict:
    rep = validators.Report()
    validators.validate(hub, only, rep)
    return {"ok": rep.ok, "error_count": len(rep.errors), "errors": rep.errors}


def stage(payload: dict) -> dict:
    entry, row = load_entry(payload)
    work, hub = state.workdir(), state.HUB
    work.mkdir(parents=True, exist_ok=True)
    pdf, repo = material_paths(row["model_name"])
    rows, error = readers.load_models_csv(hub)
    if error:
        raise IngestError(error)
    indexes = {rel: (hub / rel).read_text(encoding="utf-8") if (hub / rel).exists() else ""
               for rel in INDEX_FILES}
    commit = row["commit_hash"]
    status = ("no runnable code (.py) in repository snapshot" if not any(repo.rglob("*.py"))
              else "repository snapshot acquired from official source (commit unavailable)"
              if commit == COMMIT_HASH_PLACEHOLDER
              else f"repository cloned from official source at commit {commit}")
    base = baseline(hub, row["model_name"])
    files = render.entry_files(row, entry["verdict"], status, rows, indexes,
                               ignore_rules.snapshot_exception(hub, row["model_name"], repo))
    with tempfile.TemporaryDirectory(prefix="ingest-stage-") as folder:
        prepared = Path(folder)
        for rel, content in files.items():
            target = prepared / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        summary = {"version": 2, "model_name": row["model_name"], "baseline": base,
                   "pdf": str(pdf), "pdf_hash": digest(pdf), "repo": str(repo), "repo_hash": digest(repo),
                   "files": {rel: digest(prepared / rel) for rel in files}, "checked_at": state.now_iso()}
        with tempfile.TemporaryDirectory(prefix="ingest-check-") as tmp:
            materialize(hub, prepared, summary, Path(tmp))
            summary["validation"] = check(Path(tmp), only=row["model_name"])
        state.atomic_json(prepared / "summary.json", summary)
        staged = state.contained(work, work / "staged")
        if staged.exists():
            shutil.rmtree(staged)
        prepared.rename(staged)
    result = {"model_name": row["model_name"], "staged": str(staged), **summary["validation"]}
    state.progress(row["model_name"], "staged" if result["ok"] else "validation_failed", result)
    return result


def apply() -> dict:
    """Apply the staged entry under the run workdir; the exact staged files are applied."""
    work, hub = state.workdir(), state.HUB
    staged = state.contained(work, work / "staged")
    summary_path = state.contained(staged, staged / "summary.json")
    if not summary_path.is_file():
        raise IngestError("缺少 stage_entry 产物，请先运行 stage_entry")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    name = summary.get("model_name")
    if summary.get("version") != 2 or not name:
        raise IngestError("旧版或不完整的 staged，请重新 stage")
    entry_name(name)
    pdf, repo = material_paths(name)
    if (summary["baseline"] != baseline(hub, name)
            or summary["pdf"] != str(pdf) or summary["repo"] != str(repo)
            or summary["pdf_hash"] != digest(pdf) or summary["repo_hash"] != digest(repo)):
        raise IngestError("材料或 hub 已变化，请重新 stage")
    expected = {*INDEX_FILES, f"{entry_path(name)}/README.md"}
    if set(summary["files"]) != expected:
        raise IngestError("staged 文件集合不符合契约")
    for rel, saved_hash in summary["files"].items():
        if digest(state.contained(staged, staged / rel)) != saved_hash:
            raise IngestError(f"staged 内容已变化: {rel}")
    with tempfile.TemporaryDirectory(prefix="ingest-apply-") as folder:
        temp = Path(folder)
        view = temp / "view"
        materialize(hub, staged, summary, view)
        validation = check(view, only=name)
        if not validation["ok"]:
            raise IngestError("应用前校验失败: " + json.dumps(validation, ensure_ascii=False))
        affected = [*INDEX_FILES, entry_path(name)]
        backup = temp / "backup"
        existing = set()
        for rel in affected:
            source = state.contained(hub, hub / rel)
            if source.exists():
                existing.add(rel)
                dest = backup / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                if source.is_dir():
                    shutil.copytree(source, dest)
                else:
                    shutil.copyfile(source, dest)
        try:
            for rel in affected:
                target, source = hub / rel, view / rel
                if target.is_dir():
                    shutil.rmtree(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                if source.is_dir():
                    shutil.copytree(source, target)
                else:
                    shutil.copyfile(source, target)
            validation = check(hub, only=name)
            if not validation["ok"]:
                raise IngestError("应用后校验失败: " + json.dumps(validation, ensure_ascii=False))
        except Exception:
            for rel in affected:
                target = hub / rel
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
                if rel in existing:
                    source = backup / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if source.is_dir():
                        shutil.copytree(source, target)
                    else:
                        shutil.copyfile(source, target)
            raise
    result = {"model_name": name, "applied": True, **validation,
              "git_steps": [f"cd {hub}", f"git add {entry_path(name)} " + " ".join(INDEX_FILES),
                            f'git commit -m "Update {name} entry"']}
    state.progress(name, "applied", result)
    return result
