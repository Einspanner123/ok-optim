"""Internal candidate/ledger storage. No CLI and no hub writes."""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
HUB = REPO_ROOT / "single-cell-hub"
INGEST_ROOT = REPO_ROOT / ".ingest"
LEDGER = INGEST_ROOT / "ledger.jsonl"
VERDICTS = {"official", "author_maintained", "likely", "none", "unavailable"}
NONE_EVIDENCE_MARKERS = ("全文无仓库链接", "no runnable code")


class IngestError(Exception):
    """Operation needs correction or human input."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def contained(root: Path, path: Path) -> Path:
    if not path.resolve().is_relative_to(root.resolve()):
        raise IngestError(f"路径超出工作区: {path}")
    return path


def candidate_dir(slug: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", slug):
        raise IngestError(f"非法 slug: {slug!r}")
    return contained(INGEST_ROOT, INGEST_ROOT / "candidates" / slug)


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_candidate_raw(slug: str) -> dict:
    path = contained(candidate_dir(slug), candidate_dir(slug) / "candidate.json")
    if not path.is_file():
        raise IngestError(f"candidate.json 不存在: {path}")
    cand = json.loads(path.read_text(encoding="utf-8"))
    cand["slug"] = slug
    return cand


def update_candidate(slug: str, patch: dict) -> dict:
    if not isinstance(patch, dict):
        raise IngestError("payload 必须是 JSON object")
    path = candidate_dir(slug) / "candidate.json"
    cand = load_candidate_raw(slug) if path.exists() else {"slug": slug}

    def merge(base, values):
        for key, value in values.items():
            if key == "slug":
                continue
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                merge(base[key], value)
            else:
                base[key] = value

    merge(cand, patch)
    if not cand.get("paper_title") or not cand.get("paper_url"):
        raise IngestError("候选必须包含 paper_title / paper_url")
    cand["updated_at"] = now_iso()
    atomic_json(path, cand)
    return cand


def ledger_key(cand: dict) -> str:
    if cand.get("doi"):
        return "doi:" + cand["doi"].strip().lower()
    title = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", cand["paper_title"].lower())).strip()
    return "title:" + hashlib.sha256(title.encode()).hexdigest()[:12]


def paper_hash(rec: dict) -> str:
    basis = "|".join(str(rec.get(k, "")) for k in ("key", "title", "verdict", "repo_url", "commit"))
    return hashlib.sha256(basis.encode()).hexdigest()[:12]


def load_ledger() -> list[dict]:
    if not LEDGER.is_file():
        return []
    return [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines() if line.strip()]


def latest_by_key(records: list[dict]) -> dict[str, dict]:
    return {rec["key"]: rec for rec in records}


def append_ledger(cand: dict, verdict: str, evidence: str, *, applied: bool = False) -> dict:
    if verdict not in VERDICTS or (applied and verdict not in {"official", "author_maintained"}):
        raise IngestError("非法台账结论")
    if verdict == "none" and not any(mark in evidence for mark in NONE_EVIDENCE_MARKERS):
        raise IngestError("none 缺少现有契约要求的确定性证据")
    rec = {"key": ledger_key(cand), "title": cand["paper_title"], "verdict": verdict,
           "repo_url": cand.get("repo_url", ""), "commit": cand.get("commit_hash", ""),
           "evidence": evidence, "applied": applied, "checked_at": now_iso()}
    rec["paper_hash"] = paper_hash(rec)
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def progress(slug: str, status: str, detail: dict) -> None:
    """Publish the existing report/task-result contract from actual script outcomes."""
    cand = load_candidate_raw(slug)
    report = (f"# {cand['paper_title']}\n\n状态: {status}\n\n"
              + "```json\n" + json.dumps({"candidate": cand, "result": detail}, ensure_ascii=False, indent=2)
              + "\n```\n")
    (candidate_dir(slug) / "report.md").write_text(report, encoding="utf-8")
    run_id, run_path = os.environ.get("AGENT_RUN_ID"), os.environ.get("AGENT_RUN_DIR")
    if not run_id or not run_path:
        return
    run = contained(REPO_ROOT / "runs", Path(run_path))
    path = run / "ingest.json"
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"run_id": run_id, "items": {}}
    if state.get("run_id") != run_id:
        raise IngestError("run identity mismatch")
    state["items"][slug] = {"status": status, **detail}
    atomic_json(path, state)
    applied = len({item["model_name"] for item in state["items"].values()
                   if item["status"] == "applied"})
    target = int(os.environ.get("INGEST_MAX_NEW", "1"))
    done = applied >= target
    result = {"version": 1, "run_id": run_id, "task": "ingest",
              "slug": os.environ.get("AGENT_SLUG", "seed"),
              "status": "done" if done else "needs_human",
              "validation_status": "passed" if done else "skipped",
              "checks": [{"name": "applied_entries", "status": "passed" if done else "skipped"}],
              "reason": f"已应用 {applied}/{target} 篇；逐篇结果见 ingest.json", "items": state["items"]}
    atomic_json(run / "task_result.json", result)
    (run / "report.md").write_text("# Ingest\n\n" + result["reason"] + "\n\n" + "\n".join(
        f"- {key}: {item['status']}" for key, item in state["items"].items()) + "\n", encoding="utf-8")


def _stdio_json(payload: dict, as_json: bool = True) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2 if as_json else None))
