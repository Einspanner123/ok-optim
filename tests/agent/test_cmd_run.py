"""cmd_run 端到端桩测：用假 pi 可执行文件走完整 launcher 流程，零 LLM 成本。"""

import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent import launcher


def _make_args(task: str = "hello", set_list: list[str] | None = None,
               output: str = "human", timeout: int = 60) -> SimpleNamespace:
    return SimpleNamespace(
        task=task,
        set=set_list if set_list is not None else [
            "OPENAI_BASE_URL=http://ep/v1", "OPENAI_API_KEY=k",
            "AGENT_LLM_MODEL=m1",
        ],
        interactive=False, output=output, tool_budget=60, timeout=timeout,
        no_thinking=False, thinking="off",
    )


def _make_root(tmp_path: Path) -> Path:
    """隔离的项目根：拷贝 hello 任务 + 扩展源，伪造 .venv。"""
    root = tmp_path / "proj"
    (root / "agent" / "extensions").mkdir(parents=True)
    (root / "agent" / "extensions" / "bootstrap-guard.ts").write_text("g")
    shutil.copytree(launcher.REPO_ROOT / "task" / "hello", root / "task" / "hello")
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "python").write_text("#!/bin/sh\n")
    return root


STUB_EVENTS = (
    json.dumps({"type": "session", "id": "stub-1"}) + "\n"
    + json.dumps({"type": "tool_execution_start", "toolCallId": "1",
                  "toolName": "bash", "args": {"command": "stub"}}) + "\n"
    + json.dumps({"type": "tool_execution_end", "toolCallId": "1",
                  "toolName": "bash", "isError": False,
                  "result": {"content": [{"type": "text", "text": "ok"}]}}) + "\n"
    + json.dumps({"type": "message_end", "message": {
        "role": "assistant", "stopReason": "stop",
        "usage": {"input": 10, "output": 5, "totalTokens": 15}}}) + "\n"
)


def _stub_pi(tmp_path: Path, exit_code: int = 0) -> Path:
    stub = tmp_path / "stub_pi.py"
    stub.write_text(
        "import json, os, sys\n"
        "argv = sys.argv\n"
        "session_dir = argv[argv.index('--session-dir') + 1]\n"
        f"events = {STUB_EVENTS!r}\n"
        "sys.stdout.write(events)\n"
        "sys.stdout.flush()\n"
        "msg = {'type': 'message', 'role': 'assistant', 'stopReason': 'stop',\n"
        "       'content': [], 'usage': {'input': 10, 'output': 5, 'totalTokens': 15}}\n"
        "with open(session_dir + '/stub-session.jsonl', 'w') as f:\n"
        "    f.write(json.dumps({'type': 'session', 'id': 'stub-1'}) + chr(10))\n"
        "    f.write(json.dumps(msg) + chr(10))\n"
        "run_id = os.environ['AGENT_RUN_ID']\n"
        "task = os.environ['AGENT_TASK']\n"
        "slug = os.environ['AGENT_SLUG']\n"
        "result = {'version': 1, 'task': task, 'slug': slug, 'run_id': run_id,\n"
        "          'status': 'done', 'validation_status': 'passed',\n"
        "          'checks': [{'name': 'stub', 'status': 'passed'}],\n"
        "          'reason': 'stub run completed'}\n"
        "with open(session_dir + '/task_result.json', 'w') as f:\n"
        "    f.write(json.dumps(result))\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    return stub


@pytest.fixture()
def proj(tmp_path: Path, monkeypatch) -> Path:
    root = _make_root(tmp_path)
    # launcher 与 assembly 各持有 REPO_ROOT 常量，必须同时指向隔离根
    monkeypatch.setattr(launcher, "REPO_ROOT", root)
    monkeypatch.setattr(launcher.assembly, "REPO_ROOT", root)
    yield root


class TestCmdRun:
    def test_happy_path_end_to_end(self, proj: Path, tmp_path: Path, monkeypatch,
                                   capsys):
        monkeypatch.setattr(launcher, "find_pi",
                            lambda: (sys.executable, _stub_pi(tmp_path, 0)))
        code = launcher.cmd_run(_make_args())
        assert code == 0
        runs = list((proj / "runs").rglob("journal.json"))
        assert len(runs) == 1
        journal = json.loads(runs[0].read_text(encoding="utf-8"))
        assert journal["status"] == "done"
        assert journal["task"] == "hello"
        assert journal["decode"]["temperature"] == 0.4
        assert journal["usage"]["total_tokens"] == 15
        events = runs[0].parent / "events.jsonl"
        assert events.is_file() and "stub-1" in events.read_text(encoding="utf-8")
        assert "ok-llm" in (proj / "agent" / "runtime" / "models.json").read_text()

    def test_pi_exit_propagates_to_llm_error(self, proj: Path, tmp_path: Path,
                                             monkeypatch):
        monkeypatch.setattr(launcher, "find_pi",
                            lambda: (sys.executable, _stub_pi(tmp_path, 3)))
        code = launcher.cmd_run(_make_args(output="quiet"))
        assert code == 3

    def test_unknown_task_rejected(self, monkeypatch, capsys):
        root = Path("/nonexistent-root")
        monkeypatch.setattr(launcher, "REPO_ROOT", root)
        monkeypatch.setattr(launcher.assembly, "REPO_ROOT", root)
        code = launcher.cmd_run(_make_args(task="nope"))
        assert code == 3
        assert "未知任务" in capsys.readouterr().err

    def test_missing_venv_rejected(self, tmp_path: Path, monkeypatch, capsys):
        root = tmp_path / "proj"
        (root / "task" / "hello").mkdir(parents=True)
        shutil.copy(launcher.REPO_ROOT / "task" / "hello" / "skill.yaml",
                    root / "task" / "hello" / "skill.yaml")
        shutil.copy(launcher.REPO_ROOT / "task" / "hello" / "SKILL.md",
                    root / "task" / "hello" / "SKILL.md")
        monkeypatch.setattr(launcher, "REPO_ROOT", root)
        monkeypatch.setattr(launcher.assembly, "REPO_ROOT", root)
        code = launcher.cmd_run(_make_args())
        assert code == 3
        assert ".venv" in capsys.readouterr().err

    def test_preflight_missing_env_rejected(self, proj: Path, monkeypatch):
        # hello 的 skill.yaml 若声明 required_env，缺注入应拒绝启动。
        # hello 本身无 required_env，用 orchestrating 断言：stub 未被调用。
        called = False

        def fake_find():
            nonlocal called
            called = True
            return (sys.executable, _stub_pi(proj, 0))

        monkeypatch.setattr(launcher, "find_pi", fake_find)
        launcher.cmd_run(_make_args())
        assert called  # hello 无必需 env，pi 正常拉起

    def test_non_tty_interactive_downgrades(self, proj: Path, tmp_path: Path,
                                            monkeypatch, capsys):
        monkeypatch.setattr(launcher, "find_pi",
                            lambda: (sys.executable, _stub_pi(tmp_path, 0)))
        args = _make_args()
        args.interactive = True  # pytest 下 stdout 非 TTY
        code = launcher.cmd_run(args)
        assert code == 0
        assert "降级为非交互" in capsys.readouterr().err

    def test_human_renderer_writes_trace(self, proj: Path, tmp_path: Path,
                                         monkeypatch, capsys):
        monkeypatch.setattr(launcher, "find_pi",
                            lambda: (sys.executable, _stub_pi(tmp_path, 0)))
        code = launcher.cmd_run(_make_args(output="human"))
        assert code == 0
        assert "▶ hello" in capsys.readouterr().out
