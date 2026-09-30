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
