"""Internal staging and filesystem application. Hub formatting belongs to hubkit."""
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


def load_candidate(slug: str) -> tuple[dict, dict]:
    cand = state.load_candidate_raw(slug)
    required = [*CSV_COLUMNS[:6], "framework", "license", "verdict"]
    missing = [field for field in required if not cand.get(field)]
    if missing:
        raise IngestError(f"候选缺少字段: {missing}")
    entry_name(cand["model_name"])
    if cand["verdict"] not in {"official", "author_maintained"}:
        raise IngestError("仅 official / author_maintained 可暂存和入库")
    cand.setdefault("commit_hash", COMMIT_HASH_PLACEHOLDER)
    if not (COMMIT_RE.fullmatch(cand["commit_hash"]) or cand["commit_hash"] == COMMIT_HASH_PLACEHOLDER):
        raise IngestError("非法 commit_hash")
    row = {key: str(cand.get(key, "") or "") for key in CSV_COLUMNS}
    return cand, row


def material_paths(slug: str, name: str) -> tuple[Path, Path]:
    cdir = state.candidate_dir(slug)
    paper = state.contained(cdir, cdir / "paper")
    pdfs = sorted(paper.glob("*.pdf"))
    preferred = paper / f"{name}.pdf"
    if preferred in pdfs:
        pdf = preferred
    elif len(pdfs) == 1 or (pdfs and len({digest(p) for p in pdfs}) == 1):
        pdf = pdfs[0]
    else:
        raise IngestError("PDF 缺失或存在多个不同 PDF，请确认候选材料")
    repo = state.contained(cdir, cdir / "repo")
    if not repo.is_dir():
        raise IngestError("源码快照缺失")
    state.contained(cdir, pdf)
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


def stage(slug: str) -> dict:
    cand, row = load_candidate(slug)
    cdir, hub = state.candidate_dir(slug), state.HUB
    pdf, repo = material_paths(slug, row["model_name"])
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
    files = render.entry_files(row, cand["verdict"], status, rows, indexes,
                               ignore_rules.snapshot_exception(hub, row["model_name"], repo))
    with tempfile.TemporaryDirectory(prefix="ingest-stage-", dir=cdir) as folder:
        prepared = Path(folder)
        for rel, content in files.items():
            target = prepared / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        summary = {"version": 1, "slug": slug, "model_name": row["model_name"],
                   "candidate_hash": digest(cdir / "candidate.json"), "baseline": base,
                   "pdf": str(pdf), "pdf_hash": digest(pdf), "repo": str(repo), "repo_hash": digest(repo),
                   "files": {rel: digest(prepared / rel) for rel in files}, "checked_at": state.now_iso()}
        with tempfile.TemporaryDirectory(prefix="ingest-check-") as tmp:
            materialize(hub, prepared, summary, Path(tmp))
            summary["validation"] = check(Path(tmp))
        state.atomic_json(prepared / "summary.json", summary)
        staged = state.contained(cdir, cdir / "staged")
        if staged.exists():
            shutil.rmtree(staged)
        prepared.rename(staged)
    result = {"slug": slug, "staged": str(staged), **summary["validation"]}
    state.progress(slug, "staged" if result["ok"] else "validation_failed", result)
    return result


def apply(slug: str) -> dict:
    cand, row = load_candidate(slug)
    name, hub, cdir = row["model_name"], state.HUB, state.candidate_dir(slug)
    staged = state.contained(cdir, cdir / "staged")
    summary_path = state.contained(cdir, staged / "summary.json")
    if not summary_path.is_file():
        raise IngestError("缺少 stage_entry 产物")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("version") != 1 or summary.get("slug") != slug or summary.get("model_name") != name:
        raise IngestError("旧版或不匹配的 staged，请重新 stage")
    pdf, repo = material_paths(slug, name)
    if (summary["candidate_hash"] != digest(cdir / "candidate.json")
            or summary["baseline"] != baseline(hub, name)
            or summary["pdf"] != str(pdf) or summary["repo"] != str(repo)
            or summary["pdf_hash"] != digest(pdf) or summary["repo_hash"] != digest(repo)):
        raise IngestError("候选、材料或 hub 已变化，请重新 stage")
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
        validation = check(view)
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
        ledger_before = state.LEDGER.read_bytes() if state.LEDGER.exists() else None
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
            validation = check(hub)
            if not validation["ok"]:
                raise IngestError("应用后校验失败: " + json.dumps(validation, ensure_ascii=False))
            state.append_ledger(cand, cand["verdict"], str(cand.get("code_availability") or cand.get("evidence") or ""), applied=True)
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
            if ledger_before is None:
                state.LEDGER.unlink(missing_ok=True)
            else:
                state.LEDGER.write_bytes(ledger_before)
            raise
    result = {"slug": slug, "model_name": name, "applied": True, **validation,
              "git_steps": [f"cd {hub}", f"git add {entry_path(name)} " + " ".join(INDEX_FILES),
                            f'git commit -m "Update {name} entry"']}
    state.progress(slug, "applied", result)
    return result
