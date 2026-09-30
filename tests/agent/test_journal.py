"""journal 补充测试：print_summary 输出与 finalize 错误分支矩阵。

主体 finalize/_evidence/_task_result 行为由 tests/agent/test_results.py 覆盖；
本文件补齐该文件未触达的分支。
"""

import json
from pathlib import Path

from agent import journal


def _write_events(
    run_dir: Path, *, stop_reason: str = "stop", usage: dict | None = None, error: str | None = None
):
    """非交互模式：launcher 从 events.jsonl 取证据（message_end 事件）。"""
    run_dir.mkdir(parents=True, exist_ok=True)
    message = {
        "role": "assistant",
        "stopReason": stop_reason,
        "usage": usage or {"input": 10, "output": 5, "totalTokens": 15},
    }
    if error:
        message["errorMessage"] = error
    (run_dir / "events.jsonl").write_text(
        json.dumps({"type": "session", "id": "abc-123"})
        + "\n"
        + json.dumps({"type": "message_end", "message": message})
        + "\n",
        encoding="utf-8",
    )


def _write_session(run_dir: Path, *, stop_reason: str = "stop", usage: dict | None = None):
    """交互模式：session 文件直接存消息记录。"""
    run_dir.mkdir(parents=True, exist_ok=True)
    message = {
        "type": "message",
        "role": "assistant",
        "stopReason": stop_reason,
        "usage": usage or {"input": 10, "output": 5, "totalTokens": 15},
        "content": [],
    }
    (run_dir / "session.jsonl").write_text(
        json.dumps({"type": "session", "id": "abc-123"}) + "\n" + json.dumps(message) + "\n",
        encoding="utf-8",
    )


class TestFinalizeBranches:
    def test_llm_error_when_last_assistant_errored(self, tmp_path: Path):
        _write_events(tmp_path, stop_reason="error", error="boom")
        s = journal.finalize(tmp_path, "t", "s", 0, interactive=False)
        assert s.status == "llm_error"
        assert s.last_error == "boom"
        assert s.exit_code == 3

    def test_no_output_when_no_assistant_messages(self, tmp_path: Path):
        s = journal.finalize(tmp_path, "t", "s", 0, interactive=False)
        assert s.status == "no_output"
        assert s.exit_code == 3

    def test_interrupted_on_sigint_exit(self, tmp_path: Path):
        _write_session(tmp_path)
        s = journal.finalize(tmp_path, "t", "s", 130, interactive=False)
        assert s.status == "interrupted"
        assert s.exit_code == 3

    def test_usage_accumulated(self, tmp_path: Path):
        _write_events(tmp_path, usage={"input": 100, "output": 50, "totalTokens": 150})
        s = journal.finalize(tmp_path, "t", "s", 0, interactive=False)
        assert s.usage.input == 100
        assert s.usage.output == 50
        assert s.usage.total_tokens == 150

    def test_decode_params_recorded_in_journal(self, tmp_path: Path):
        _write_session(tmp_path)
        decode = {"temperature": 0.4, "max_tokens": 16384, "seed": None}
        journal.finalize(tmp_path, "t", "s", 0, interactive=False, decode=decode)
        data = json.loads((tmp_path / "journal.json").read_text(encoding="utf-8"))
        assert data["decode"] == decode

    def test_decode_omitted_when_not_provided(self, tmp_path: Path):
        _write_session(tmp_path)
        journal.finalize(tmp_path, "t", "s", 0, interactive=False)
        data = json.loads((tmp_path / "journal.json").read_text(encoding="utf-8"))
        assert "decode" not in data

    def test_journal_json_atomic_write(self, tmp_path: Path):
        _write_session(tmp_path)
        journal.finalize(tmp_path, "t", "s", 0, interactive=False)
        assert not (tmp_path / "journal.json.tmp").exists()
        json.loads((tmp_path / "journal.json").read_text(encoding="utf-8"))


def _write_complete_session(run_dir: Path, *, stop_reason: str = "stop"):
    """非交互完整会话：events.jsonl + 同名 session 文件，走到 task_status 判定分支。

    _write_events 只造 events.jsonl，会先在 audit_error（missing pi session file）
    被拦下，触达不到"结果缺失"这条路径，因此兜底测试需要这个更完整的 fixture。
    """
    _write_events(run_dir, stop_reason=stop_reason)
    session_id = "abc-123"
    meta = {"type": "session", "id": session_id, "version": 3}
    message = {
        "type": "message",
        "role": "assistant",
        "stopReason": stop_reason,
        "content": [],
        "usage": {"input": 10, "output": 5, "totalTokens": 15},
    }
    (run_dir / f"2026-09-29T11-50-05-336Z_{session_id}.jsonl").write_text(
        json.dumps(meta) + "\n" + json.dumps(message) + "\n", encoding="utf-8"
    )


class TestIncompleteFallback:
    """会话正常结束但没有 task_result.json 时，journal 必须自己补一份。"""

    def test_synthesizes_incomplete_result(self, tmp_path: Path):
        _write_complete_session(tmp_path)
        s = journal.finalize(tmp_path, "t", "s", 0, interactive=False, run_id="run-9")
        assert s.status == "incomplete"
        assert s.last_error == "task script did not publish task_result.json"
        result = json.loads((tmp_path / "task_result.json").read_text(encoding="utf-8"))
        assert result["status"] == "skipped_incomplete"
        assert result["validation_status"] == "skipped"
        assert result["run_id"] == "run-9"
        assert result["task"] == "t" and result["slug"] == "s"
        assert result["checks"] == [{"name": "task_result_published", "status": "skipped"}]
        assert "task_result.json" in result["reason"]

    def test_fallback_result_passes_own_validator(self, tmp_path: Path):
        # 兜底产物必须能通过 _task_result 的校验，否则 journal 会变成 invalid_result
        _write_complete_session(tmp_path)
        s = journal.finalize(tmp_path, "t", "s", 0, interactive=False, run_id="run-9")
        assert s.status == "incomplete"
        assert s.task_status == "skipped_incomplete"
        assert s.validation_status == "skipped"

    def test_existing_result_is_not_overwritten(self, tmp_path: Path):
        _write_complete_session(tmp_path)
        (tmp_path / "task_result.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "run_id": "run-9",
                    "task": "t",
                    "slug": "s",
                    "status": "skipped_incomplete",
                    "validation_status": "skipped",
                    "checks": [{"name": "x", "status": "skipped"}],
                    "reason": "real",
                }
            ),
            encoding="utf-8",
        )
        journal.finalize(tmp_path, "t", "s", 0, interactive=False, run_id="run-9")
        result = json.loads((tmp_path / "task_result.json").read_text(encoding="utf-8"))
        assert result["reason"] == "real"

    def test_no_fallback_without_run_id(self, tmp_path: Path):
        _write_complete_session(tmp_path)
        journal.finalize(tmp_path, "t", "s", 0, interactive=False)
        assert not (tmp_path / "task_result.json").exists()

    def test_no_tmp_file_left_behind(self, tmp_path: Path):
        _write_complete_session(tmp_path)
        journal.finalize(tmp_path, "t", "s", 0, interactive=False, run_id="run-9")
        assert not (tmp_path / "task_result.json.tmp").exists()


class TestPrintSummary:
    def test_prints_status_and_usage(self, tmp_path: Path, capsys):
        _write_events(tmp_path, usage={"input": 10, "output": 5, "totalTokens": 15})
        s = journal.finalize(tmp_path, "t", "s", 0, interactive=False)
        journal.print_summary(s, tmp_path)
        out = capsys.readouterr().out
        assert "status" in out
        assert "in=10" in out and "total=15" in out
        assert str(tmp_path) in out

    def test_prints_last_error_when_present(self, tmp_path: Path, capsys):
        _write_events(tmp_path, stop_reason="error", error="boom")
        s = journal.finalize(tmp_path, "t", "s", 0, interactive=False)
        journal.print_summary(s, tmp_path)
        assert "boom" in capsys.readouterr().out
