"""Archive execution evidence and task outcomes; process exit 0 is not task success."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total_tokens: int = 0
    cost_total: float = 0.0

    def add(self, usage: dict) -> None:
        self.input += int(usage.get("input") or 0)
        self.output += int(usage.get("output") or 0)
        self.cache_read += int(usage.get("cacheRead") or 0)
        self.cache_write += int(usage.get("cacheWrite") or 0)
        self.total_tokens += int(usage.get("totalTokens") or 0)
        self.cost_total += float((usage.get("cost") or {}).get("total") or 0)


@dataclass
class RunSummary:
    task: str
    slug: str
    pi_exit_code: int
    interactive: bool
    run_id: str | None = None
    session_id: str | None = None
    session_file: str | None = None
    tool_calls: int = 0
    usage: Usage = field(default_factory=Usage)
    status: str = "unknown"
    runtime_status: str = "unknown"
    task_status: str = "unknown"
    validation_status: str = "unknown"
    exit_code: int = 3
    last_error: str | None = None


def _records(path: Path, *, events: bool = False) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                # stdout also includes non-JSON runtime warnings; session files do not.
                if events and not line.lstrip().startswith("{"):
                    continue
                raise ValueError(f"truncated/invalid audit record in {path.name}")
            if not isinstance(item, dict):
                raise ValueError(f"invalid audit record in {path.name}")
            records.append(item)
    return records


def _evidence(run_dir: Path, summary: RunSummary) -> list[dict]:
    events_path = run_dir / "events.jsonl"
    events = _records(events_path, events=True) if events_path.is_file() else []
    expected_id = next((e.get("id") for e in events if e.get("type") == "session"), None)
    sessions = []
    for path in sorted(run_dir.glob("*.jsonl")):
        if path.name == "events.jsonl":
            continue
        records = _records(path)
        if records and records[0].get("type") == "session":
            if expected_id is None or records[0].get("id") == expected_id:
                sessions.append((path, records))
    if len(sessions) > 1:
        raise ValueError("ambiguous session files")
    session_records = []
    if sessions:
        path, session_records = sessions[0]
        summary.session_id = session_records[0].get("id")
        summary.session_file = path.name
    elif expected_id:
        summary.session_id = expected_id

    # One source per run: never sum both stream events and session messages.
    if not summary.interactive and events:
        messages = [e.get("message", {}) for e in events if e.get("type") == "message_end"]
        summary.tool_calls = sum(e.get("type") == "tool_execution_start" for e in events)
    else:
        messages = [e.get("message", {}) for e in session_records if e.get("type") == "message"]
        summary.tool_calls = sum(
            c.get("type") == "toolCall"
            for m in messages if m.get("role") == "assistant"
            for c in m.get("content", []) if isinstance(c, dict)
        )
    if any(not isinstance(m, dict) for m in messages):
        raise ValueError("audit message must be an object")
    return messages


def _task_result(run_dir: Path, summary: RunSummary) -> None:
    path = run_dir / "task_result.json"
    if not path.exists():
        return
    if path.is_symlink():
        raise ValueError("task_result.json must not be a symlink")
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("task result must be an object")
    if type(result.get("version")) is not int or result["version"] != 1 or not summary.run_id:
        raise ValueError("unsupported task result version or missing run identity")
    for key in ("task", "slug", "run_id"):
        if result.get(key) != getattr(summary, key):
            raise ValueError(f"task result {key} does not match this run")
    status = result.get("status")
    validation = result.get("validation_status")
    if status not in {"done", "done_with_warnings", "needs_human", "failed", "skipped_incomplete"}:
        raise ValueError("invalid task status")
    if validation not in {"passed", "warnings", "failed", "skipped"}:
        raise ValueError("invalid validation status")
    checks = result.get("checks")
    if not isinstance(checks, list) or any(
        not isinstance(c, dict) or not isinstance(c.get("name"), str) or not c["name"].strip()
        or c.get("status") not in {"passed", "failed", "skipped"}
        for c in checks
    ):
        raise ValueError("invalid task checks")
    if status == "done" and (
        validation != "passed" or not checks or any(c["status"] != "passed" for c in checks)
    ):
        raise ValueError("done requires successful validation and passing checks")
    if status == "done_with_warnings" and (
        validation not in {"passed", "warnings"} or not checks
        or any(c["status"] == "failed" for c in checks)
    ):
        raise ValueError("done_with_warnings must not hide failed validation")
    if not isinstance(result.get("reason"), str) or not result["reason"].strip():
        raise ValueError("task result requires a reason")
    summary.task_status = status
    summary.validation_status = validation


def finalize(run_dir: Path, task: str, slug: str, pi_exit_code: int,
             interactive: bool, run_id: str | None = None) -> RunSummary:
    summary = RunSummary(task, slug, pi_exit_code, interactive, run_id=run_id)
    summary.runtime_status = "finished" if pi_exit_code == 0 else "pi_error"
    audit_error = None
    result_error = None
    messages = []
    try:
        messages = _evidence(run_dir, summary)
        for message in messages:
            if message.get("role") == "assistant" and message.get("usage"):
                summary.usage.add(message["usage"])
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        audit_error = str(exc)
    try:
        _task_result(run_dir, summary)
    except (OSError, ValueError, TypeError) as exc:
        result_error = str(exc)

    assistants = [m for m in messages if m.get("role") == "assistant"]
    last = assistants[-1] if assistants else {}
    if pi_exit_code != 0:
        summary.status = "interrupted" if pi_exit_code in {130, -2} else "pi_error"
        summary.runtime_status = summary.status
    elif audit_error:
        summary.status = summary.runtime_status = "audit_error"
        summary.last_error = audit_error
    elif last.get("stopReason") in {"error", "aborted"}:
        summary.status = summary.runtime_status = "llm_error"
        summary.last_error = last.get("errorMessage") or last["stopReason"]
    elif not assistants:
        summary.status = summary.runtime_status = "no_output"
    elif not summary.session_file:
        summary.status = summary.runtime_status = "audit_error"
        summary.last_error = "missing pi session file"
    elif result_error:
        summary.status = "invalid_result"
        summary.last_error = result_error
    elif (not messages or messages[-1].get("role") != "assistant"
          or last.get("stopReason") != "stop"):
        summary.status = "incomplete"
        summary.last_error = "session did not finish with a completed assistant turn"
    elif summary.task_status == "unknown":
        summary.status = "incomplete"
        summary.last_error = "task script did not publish task_result.json"
    elif summary.task_status in {"done", "done_with_warnings"} and summary.tool_calls == 0:
        summary.status = "incomplete"
        summary.last_error = "no task tool execution evidence"
    else:
        summary.status = summary.task_status
    summary.exit_code = (
        0 if summary.status in {"done", "done_with_warnings"}
        else 2 if summary.status in {"needs_human", "incomplete", "skipped_incomplete"}
        else 3
    )
    payload = asdict(summary)
    payload["usage"]["cost_total"] = round(summary.usage.cost_total, 6)
    temporary = run_dir / "journal.json.tmp"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(run_dir / "journal.json")
    return summary


def print_summary(summary: RunSummary, run_dir: Path) -> None:
    print("\n=== journal ===")
    print(f"status       : {summary.status} (pi exit={summary.pi_exit_code}, task exit={summary.exit_code})")
    print(f"runtime/task/validation: {summary.runtime_status}/{summary.task_status}/{summary.validation_status}")
    if summary.last_error:
        print(f"last error   : {summary.last_error[:200]}")
    print(f"run dir      : {run_dir}")
    if summary.session_file:
        print(f"session file : {run_dir / summary.session_file}")
    print(f"tool calls   : {summary.tool_calls}")
    u = summary.usage
    print(f"usage        : in={u.input} out={u.output} cache_r={u.cache_read} cache_w={u.cache_write} total={u.total_tokens}")
