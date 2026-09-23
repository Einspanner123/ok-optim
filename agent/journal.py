"""journal: 运行归档。

pi session 已由 --session-dir 落在 run 目录；本模块从事件流提取
usage 汇总与状态，写 journal.json 并打印摘要。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
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
        cost = usage.get("cost") or {}
        self.cost_total += float(cost.get("total") or 0)


@dataclass
class RunSummary:
    task: str
    slug: str
    pi_exit_code: int
    interactive: bool
    session_id: str | None = None
    session_file: str | None = None
    tool_calls: int = 0
    usage: Usage = field(default_factory=Usage)
    status: str = "unknown"
    last_error: str | None = None


def finalize(
    run_dir: Path,
    task: str,
    slug: str,
    pi_exit_code: int,
    interactive: bool,
) -> RunSummary:
    """收尾：解析事件流（json 模式才有），写 journal.json，返回摘要。"""
    summary = RunSummary(
        task=task, slug=slug, pi_exit_code=pi_exit_code, interactive=interactive
    )
    summary.status = "done" if pi_exit_code == 0 else "pi_error"

    events_path = run_dir / "events.jsonl"
    if events_path.is_file():
        session_id: str | None = None
        last_assistant_stop: str | None = None
        last_error: str | None = None
        for line in events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = event.get("type")
            if etype == "session":
                session_id = event.get("id")
            elif etype == "tool_execution_start":
                summary.tool_calls += 1
            elif etype == "message_end":
                message = event.get("message") or {}
                if message.get("role") == "assistant":
                    last_assistant_stop = message.get("stopReason")
                    if message.get("stopReason") == "error":
                        last_error = message.get("errorMessage")
                    u = message.get("usage")
                    if u:
                        summary.usage.add(u)
        summary.session_id = session_id
        # pi 在 LLM 失败后仍可能 exit 0（agent_settled）——以最后 assistant 状态为准
        if last_assistant_stop == "error":
            summary.status = "llm_error"
            summary.last_error = last_error
        elif summary.status == "done" and summary.tool_calls == 0 and summary.usage.input == 0:
            summary.status = "no_output"

    # 定位 session 文件（--session-dir 指向 run_dir，文件名形如 <name>__<uuid>.jsonl）
    session_rel = None
    if summary.session_id:
        for candidate in sorted(run_dir.rglob("*.jsonl")):
            if candidate.name == "events.jsonl":
                continue
            if candidate.is_file():
                session_rel = str(candidate.relative_to(run_dir))
                break
    summary.session_file = session_rel

    journal = {
        "task": task,
        "slug": slug,
        "interactive": interactive,
        "status": summary.status,
        "pi_exit_code": pi_exit_code,
        "last_error": summary.last_error,
        "session_id": summary.session_id,
        "session_file": summary.session_file,
        "tool_calls": summary.tool_calls,
        "usage": {
            "input": summary.usage.input,
            "output": summary.usage.output,
            "cache_read": summary.usage.cache_read,
            "cache_write": summary.usage.cache_write,
            "total_tokens": summary.usage.total_tokens,
            "cost_total": round(summary.usage.cost_total, 6),
        },
    }
    (run_dir / "journal.json").write_text(
        json.dumps(journal, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def print_summary(summary: RunSummary, run_dir: Path) -> None:
    print("\n=== journal ===")
    print(f"status       : {summary.status} (pi exit={summary.pi_exit_code})")
    if summary.last_error:
        print(f"last error   : {summary.last_error[:200]}")
    print(f"run dir      : {run_dir}")
    if summary.session_file:
        print(f"session file : {run_dir / summary.session_file}")
    print(f"tool calls   : {summary.tool_calls}")
    u = summary.usage
    if u.total_tokens or u.input or u.output:
        print(
            f"usage        : in={u.input} out={u.output} "
            f"cache_r={u.cache_read} cache_w={u.cache_write} total={u.total_tokens}"
        )
