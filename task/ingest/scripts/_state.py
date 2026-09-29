"""ingest 共享底座（无状态）：run 工作区、结果落盘、通用小工具。

无状态契约（architecture.md「任务通用契约」）：
- 跨 run 不保存业务状态（无 candidate/ledger）。本次运行的条目材料（paper/、
  repo/）与产物（staged/）都写在 launcher 注入的 AGENT_RUN_DIR 下
- HTTP 缓存 runs/.cache/ingest/ 纯内容寻址，无业务语义，跨 run 复用安全
- 本模块无 CLI，不直接写 single-cell-hub
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
HUB = REPO_ROOT / "single-cell-hub"
RUNS_ROOT = REPO_ROOT / "runs"

ENTRY_FIELDS = ("model_name", "paper_title", "paper_url", "year",
                "venue", "repo_url", "framework", "license", "verdict")


class IngestError(Exception):
    """Operation needs correction or human input."""


def missing_entry_fields(entry: dict) -> list[str]:
    """Fields required by staging."""
    return [field for field in ENTRY_FIELDS if not entry.get(field)]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def contained(root: Path, path: Path) -> Path:
    if not path.resolve().is_relative_to(root.resolve()):
        raise IngestError(f"路径超出工作区: {path}")
    return path


def workdir() -> Path:
    """本次运行的工作区 = AGENT_RUN_DIR（runs/<ts>/<task>/<slug>/）。

    经 launcher 运行时由 assembly 注入；缺失说明绕过了 ok CLI，拒绝执行。
    """
    run_dir = os.environ.get("AGENT_RUN_DIR")
    if not run_dir:
        raise IngestError("AGENT_RUN_DIR 未注入：请经 ok CLI 运行，不要独立执行任务脚本")
    return contained(RUNS_ROOT, Path(run_dir))


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def progress(name: str, status: str, detail: dict) -> None:
    """Publish the run result contract from actual script outcomes.

    name 是条目身份（model_name）。applied 计数达到 INGEST_MAX_NEW 才算 done。
    """
    run_id, run_path = os.environ.get("AGENT_RUN_ID"), os.environ.get("AGENT_RUN_DIR")
    if not run_id or not run_path:
        return
    run = contained(RUNS_ROOT, Path(run_path))
    path = run / "ingest.json"
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"run_id": run_id, "items": {}}
    if state.get("run_id") != run_id:
        raise IngestError("run identity mismatch")
    state["items"][name] = {"status": status, **detail}
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
