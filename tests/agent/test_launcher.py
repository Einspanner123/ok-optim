"""launcher 纯函数与渲染层测试（无真实 pi、零 LLM 成本）。

cmd_run 端到端桩测见 test_cmd_run.py；本文件覆盖：
parse_set / resolve_slug / build_prompt / 三个渲染器 / run_pi_json。
"""

import io
import json
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent import launcher


def _summary(**kw):
    base = {
        "task": "ingest",
        "slug": "s",
        "status": "done",
        "exit_code": 0,
        "tool_calls": 3,
        "last_error": None,
        "session_file": "sess.jsonl",
    }
    usage = SimpleNamespace(input=10, output=5, total_tokens=15)
    base["usage"] = kw.pop("usage", usage)
    base.update(kw)
    return SimpleNamespace(**base)


def _event(etype: str, **extra):
    return {"type": etype, **extra}


# ---- parse_set ----


class TestParseSet:
    def test_none_returns_empty(self):
        assert launcher.parse_set(None) == {}

    def test_pairs(self):
        assert launcher.parse_set(["A=1", "B=x=y"]) == {"A": "1", "B": "x=y"}

    def test_missing_equals_raises(self):
        with pytest.raises(SystemExit, match="KEY=VALUE"):
            launcher.parse_set(["NOVALUE"])

    def test_empty_key_raises(self):
        with pytest.raises(SystemExit, match="KEY 为空"):
            launcher.parse_set(["=value"])


# ---- resolve_slug ----


class TestResolveSlug:
    def test_ingest_model_name_wins(self):
        assert launcher.resolve_slug("ingest", {"INGEST_MODEL_NAME": "UCE"}, {}) == "UCE"

    def test_ingest_seed_fallback_sanitized(self):
        slug = launcher.resolve_slug("ingest", {}, {"INGEST_SEED_URL": "arxiv:2305.16175"})
        assert slug == "arxiv-2305.16175"

    def test_ingest_seed_sanitized_truncated(self):
        slug = launcher.resolve_slug("ingest", {}, {"INGEST_SEED_URL": "a/b\\c d" * 20})
        assert "/" not in slug and "\\" not in slug and len(slug) <= 80

    def test_ingest_default_seed(self):
        assert launcher.resolve_slug("ingest", {}, {}) == "seed"

    def test_optimize_model_name(self):
        assert launcher.resolve_slug("optimize", {}, {"OPTIMIZE_MODEL_NAME": "scGPT"}) == "scGPT"

    def test_optimize_adhoc_default(self):
        assert launcher.resolve_slug("optimize", {}, {}) == "adhoc"

    def test_other_tasks_use_task_name(self):
        assert launcher.resolve_slug("hello", {}, {}) == "hello"


# ---- build_prompt ----


def _spec(required=None, optional=None):
    return SimpleNamespace(required_env=required or [], optional_env=optional or [])


class TestBuildPrompt:
    def test_contains_task_and_skill_path(self):
        p = launcher.build_prompt("hello", _spec(), {}, interactive=False)
        assert "Execute task: hello" in p
        assert "task/hello/SKILL.md" in p

    def test_interactive_mentions_ask_user(self):
        p = launcher.build_prompt("hello", _spec(), {}, interactive=True)
        assert "ask_user" in p
        assert "Simplified Chinese" not in p

    def test_noninteractive_requests_chinese_final_answer(self):
        p = launcher.build_prompt("hello", _spec(), {}, interactive=False)
        assert "Simplified Chinese" in p
        assert "pending / needs_human" in p

    def test_declared_params_listed_with_secret_redaction(self):
        spec = _spec(required=["INGEST_SEED_URL"], optional=["GITHUB_TOKEN"])
        p = launcher.build_prompt(
            "ingest",
            spec,
            {"INGEST_SEED_URL": "doi:1", "GITHUB_TOKEN": "ghp_secret", "UNDECLARED_KEY": "leak"},
            interactive=False,
        )
        assert "INGEST_SEED_URL=doi:1" in p
        assert "GITHUB_TOKEN=[redacted]" in p
        assert "UNDECLARED_KEY" not in p

    def test_long_value_truncated(self):
        spec = _spec(required=["K"])
        p = launcher.build_prompt("t", spec, {"K": "x" * 300}, interactive=False)
        assert "x" * 117 + "..." in p
        assert "x" * 118 not in p

    def test_optional_env_hint(self):
        p = launcher.build_prompt("t", _spec(optional=["A", "B"]), {})
        assert "Optional parameters" in p and "A, B" in p


# ---- 渲染器 ----


class TestHumanRenderer:
    def _r(self):
        out, err = io.StringIO(), io.StringIO()
        return launcher.HumanRenderer(out, err), out, err

    def test_header(self):
        r, out, _ = self._r()
        r.header("ingest", "UCE", "m1", Path("/runs/x"))
        assert "▶ ingest/UCE" in out.getvalue() and "m1" in out.getvalue()

    def test_non_json_line_passthrough(self):
        r, out, _ = self._r()
        r.line("some warning", None)
        assert "some warning" in out.getvalue()

    def test_text_delta_streamed_without_newline(self):
        r, out, _ = self._r()
        r.line(
            "",
            _event("message_update", assistantMessageEvent={"type": "text_delta", "delta": "hi"}),
        )
        r.line(
            "", _event("message_update", assistantMessageEvent={"type": "text_delta", "delta": "!"})
        )
        # text 段现在总是先开 ┈ answer ┈ 分隔线（含首轮）
        assert out.getvalue().endswith("hi!")
        assert "┈ answer" in out.getvalue()

    def test_thinking_opens_segment_with_gutter(self):
        r, out, _ = self._r()
        r.line(
            "",
            _event(
                "message_update", assistantMessageEvent={"type": "thinking_delta", "delta": "think"}
            ),
        )
        text = out.getvalue()
        assert "thinking" in text and "think" in text

    def test_thinking_to_text_emits_answer_separator(self):
        r, out, _ = self._r()
        r.line(
            "",
            _event(
                "message_update", assistantMessageEvent={"type": "thinking_delta", "delta": "t"}
            ),
        )
        r.line(
            "", _event("message_update", assistantMessageEvent={"type": "text_delta", "delta": "A"})
        )
        text = out.getvalue()
        assert "thinking" in text and "answer" in text and "A" in text

    def test_thinking_hidden_with_show_thinking_false(self):
        out, err = io.StringIO(), io.StringIO()
        r = launcher.HumanRenderer(out, err, show_thinking=False)
        r.line(
            "",
            _event(
                "message_update",
                assistantMessageEvent={"type": "thinking_delta", "delta": "secret reasoning"},
            ),
        )
        assert out.getvalue() == ""

    def test_empty_delta_ignored(self):
        r, out, _ = self._r()
        r.line("", _event("message_update", assistantMessageEvent={"type": "text_delta"}))
        assert out.getvalue() == ""

    def test_text_after_tool_opens_answer_segment(self):
        r, out, _ = self._r()
        r.line(
            "",
            _event(
                "tool_execution_end",
                isError=False,
                result={"content": [{"type": "text", "text": "ok"}]},
            ),
        )
        r.line(
            "",
            _event("message_update", assistantMessageEvent={"type": "text_delta", "delta": "继续"}),
        )
        text = out.getvalue()
        assert "┈ answer" in text and "继续" in text

    def test_tool_start_bash_shows_command(self):
        r, out, _ = self._r()
        r.line("", _event("tool_execution_start", toolName="bash", args={"command": "ls -la"}))
        assert "┌─ bash" in out.getvalue() and "$ ls -la" in out.getvalue()

    def test_tool_start_read_shows_relative_path(self):
        r, out, _ = self._r()
        r.line(
            "",
            _event(
                "tool_execution_start",
                toolName="read",
                args={"path": str(launcher.REPO_ROOT / "task/x.py")},
            ),
        )
        assert "task/x.py" in out.getvalue()

    def test_tool_args_truncated_over_200(self):
        r, out, _ = self._r()
        r.line("", _event("tool_execution_start", toolName="bash", args={"command": "c" * 300}))
        assert "..." in out.getvalue()

    def test_tool_end_success_single_line_summary(self, monkeypatch):
        monkeypatch.setattr(launcher.time, "monotonic", lambda: 0.0)
        r, out, _ = self._r()
        r.line("", _event("tool_execution_start", toolName="bash", args={"command": "ls"}))
        monkeypatch.setattr(launcher.time, "monotonic", lambda: 0.4)
        r.line(
            "",
            _event(
                "tool_execution_end",
                isError=False,
                result={"content": [{"type": "text", "text": '{"a": 1}'}]},
            ),
        )
        text = out.getvalue()
        assert "└─ ✓" in text
        assert "0.4s" in text
        assert '{"a": 1}' not in text  # 成功不展开内容

    def test_tool_end_error_expands_text(self):
        r, out, _ = self._r()
        r.line("", _event("tool_execution_start", toolName="bash", args={"command": "ls"}))
        r.line(
            "",
            _event(
                "tool_execution_end",
                isError=True,
                result={"content": [{"type": "text", "text": "path-guard rejected"}]},
            ),
        )
        text = out.getvalue()
        assert "└─ ✗" in text and "path-guard rejected" in text

    def test_tool_end_error_truncated_over_300(self):
        r, out, _ = self._r()
        r.line("", _event("tool_execution_start", toolName="bash", args={"command": "ls"}))
        r.line(
            "",
            _event(
                "tool_execution_end",
                isError=True,
                result={"content": [{"type": "text", "text": "e" * 400}]},
            ),
        )
        assert "..." in out.getvalue()

    def test_llm_error_line(self):
        r, out, _ = self._r()
        r.line(
            "",
            _event(
                "message_end",
                message={"role": "assistant", "stopReason": "error", "errorMessage": "conn reset"},
            ),
        )
        assert "✗ LLM: conn reset" in out.getvalue()

    def test_retry_line(self):
        r, out, _ = self._r()
        r.line("", _event("auto_retry_start"))
        assert "⟳ retry" in out.getvalue()

    def test_footer_success_banner(self):
        r, out, _ = self._r()
        r.footer(_summary(), Path("/runs/x"), 1.5)
        text = out.getvalue()
        assert "✅ done" in text and "tools 3" in text
        assert "tokens 10→5" in text and "1.5s" in text
        assert "/runs/x" in text
        assert "task exit" not in text  # 冗余字段已去除

    def test_footer_error_banner_with_last_error(self):
        r, out, _ = self._r()
        r.footer(_summary(status="llm_error", exit_code=3, last_error="boom"), Path("/runs/x"), 1.0)
        assert "❌ llm_error" in out.getvalue() and "boom" in out.getvalue()


class TestQuietRenderer:
    def test_silent_then_one_json_line(self):
        out, err = io.StringIO(), io.StringIO()
        r = launcher.QuietRenderer(out, err)
        r.header("t", "s", "m", Path("/x"))
        r.line("", _event("message_end", message={"role": "assistant"}))
        r.footer(_summary(status="llm_error", exit_code=3, last_error="e"), Path("/x"), 2.0)
        lines = [ln for ln in out.getvalue().splitlines() if ln]
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload["status"] == "llm_error" and payload["exit"] == 3
        assert payload["last_error"] == "e" and payload["tokens"] == 15


class TestRawRenderer:
    def test_raw_passthrough_only(self):
        out, err = io.StringIO(), io.StringIO()
        r = launcher.RawRenderer(out, err)
        r.header("t", "s", "m", Path("/x"))
        r.line("RAWLINE", _event("agent_start"))
        r.footer(_summary(), Path("/x"), 0.0)
        assert out.getvalue().splitlines() == ["RAWLINE"]


# ---- run_pi_json（桩 pi，不启动真实模型） ----


class TestRunPiJson:
    def _stub(self, tmp_path: Path, body: str) -> list[str]:
        stub = tmp_path / "stub_pi.py"
        stub.write_text(
            textwrap.dedent(f"""
            import sys
            sys.stdout.write({body!r})
        """),
            encoding="utf-8",
        )
        return [sys.executable, str(stub)]

    def test_events_recorded_and_renderer_called(self, tmp_path: Path):
        events = [
            json.dumps({"type": "session", "id": "s1"}),
            json.dumps(
                {
                    "type": "tool_execution_start",
                    "toolCallId": "1",
                    "toolName": "bash",
                    "args": {"command": "ls"},
                }
            ),
            "not-json junk line",
        ]
        run_dir = tmp_path / "run"
        seen = []
        r = SimpleNamespace(
            header=lambda *a: None,
            footer=lambda *a: None,
            line=lambda raw, ev: seen.append((raw, ev)),
        )
        code = launcher.run_pi_json(
            self._stub(tmp_path, "\n".join(events) + "\n"), {"AGENT_TOOL_BUDGET": "60"}, run_dir, r
        )
        assert code == 0
        recorded = (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(recorded) == 3
        assert seen[0] == (events[0], json.loads(events[0]))
        assert seen[2] == ("not-json junk line", None)  # 非 JSON 行透传

    def test_budget_exceeded_returns_2(self, tmp_path: Path):
        starts = "\n".join(
            json.dumps(
                {
                    "type": "tool_execution_start",
                    "toolCallId": str(i),
                    "toolName": "bash",
                    "args": {},
                }
            )
            for i in range(6)
        )
        run_dir = tmp_path / "run"
        r = SimpleNamespace(header=lambda *a: None, footer=lambda *a: None, line=lambda *a: None)
        code = launcher.run_pi_json(
            self._stub(tmp_path, starts + "\n"), {"AGENT_TOOL_BUDGET": "0"}, run_dir, r
        )
        assert code == 2

    def test_timeout_returns_2(self, tmp_path: Path):
        run_dir = tmp_path / "run"
        r = SimpleNamespace(header=lambda *a: None, footer=lambda *a: None, line=lambda *a: None)
        stub = tmp_path / "slow_pi.py"
        stub.write_text("import time; time.sleep(3)\n", encoding="utf-8")
        code = launcher.run_pi_json(
            [sys.executable, str(stub)], {"AGENT_TOOL_BUDGET": "60"}, run_dir, r, timeout_s=0.3
        )
        assert code == 2

    def test_exit_code_propagates(self, tmp_path: Path):
        stub = tmp_path / "fail_pi.py"
        stub.write_text("import sys; sys.exit(3)\n", encoding="utf-8")
        r = SimpleNamespace(header=lambda *a: None, footer=lambda *a: None, line=lambda *a: None)
        code = launcher.run_pi_json(
            [sys.executable, str(stub)], {"AGENT_TOOL_BUDGET": "60"}, tmp_path / "run", r
        )
        assert code == 3


# ---- --config 运行方案 ----

from agent import assembly  # noqa: E402


def _profile_spec():
    return assembly.SkillSpec(
        name="ingest",
        description="",
        required_env=["INGEST_MODE"],
        optional_env=["INGEST_APPLY_AUTHORIZED", "INGEST_MAX_NEW"],
        scripts={},
    )


class TestLoadProfile:
    def test_no_config_returns_empty(self):
        assert assembly.load_profile(None, None, _profile_spec()) == ({}, {})

    def test_profile_requires_config(self):
        with pytest.raises(assembly.PreflightError, match="--profile 需要同时提供"):
            assembly.load_profile(None, "x", _profile_spec())

    def test_single_profile_toplevel(self, tmp_path):
        f = tmp_path / "p.yml"
        f.write_text("env:\n  INGEST_MODE: discover\nflags:\n  timeout: 3600\n", encoding="utf-8")
        env, flags = assembly.load_profile(str(f), None, _profile_spec())
        assert env == {"INGEST_MODE": "discover"}
        assert flags == {"timeout": 3600}

    def test_single_profile_rejects_profile_arg(self, tmp_path):
        f = tmp_path / "p.yml"
        f.write_text("env: {INGEST_MODE: audit}\n", encoding="utf-8")
        with pytest.raises(assembly.PreflightError, match="单方案"):
            assembly.load_profile(str(f), "x", _profile_spec())

    def test_multi_profile_requires_selection(self, tmp_path):
        f = tmp_path / "p.yml"
        f.write_text(
            "profiles:\n  a: {env: {INGEST_MODE: audit}}\n  b: {env: {INGEST_MODE: discover}}\n",
            encoding="utf-8",
        )
        with pytest.raises(assembly.PreflightError, match="--profile 指定"):
            assembly.load_profile(str(f), None, _profile_spec())

    def test_unknown_profile_rejected(self, tmp_path):
        f = tmp_path / "p.yml"
        f.write_text("profiles:\n  a: {env: {INGEST_MODE: audit}}\n", encoding="utf-8")
        with pytest.raises(assembly.PreflightError, match="未知方案"):
            assembly.load_profile(str(f), "zz", _profile_spec())

    def test_unknown_top_key_rejected(self, tmp_path):
        f = tmp_path / "p.yml"
        f.write_text("profiles:\n  a: {env: {INGEST_MODE: audit}, flag: {}}\n", encoding="utf-8")
        with pytest.raises(assembly.PreflightError, match="未知键"):
            assembly.load_profile(str(f), "a", _profile_spec())

    def test_env_key_outside_whitelist_rejected(self, tmp_path):
        f = tmp_path / "p.yml"
        f.write_text("env: {INGEST_MODELNAME: x}\n", encoding="utf-8")
        with pytest.raises(assembly.PreflightError, match="白名单"):
            assembly.load_profile(str(f), None, _profile_spec())

    def test_defaults_merged_profile_wins(self, tmp_path):
        f = tmp_path / "p.yml"
        f.write_text(
            'defaults:\n  env: {INGEST_MODE: discover, INGEST_MAX_NEW: "1"}\n'
            "  flags: {tool_budget: 30}\n"
            "profiles:\n  p:\n    env: {INGEST_MODE: audit}\n"
            "    flags: {timeout: 99}\n",
            encoding="utf-8",
        )
        env, flags = assembly.load_profile(str(f), "p", _profile_spec())
        assert env == {"INGEST_MODE": "audit", "INGEST_MAX_NEW": "1"}
        assert flags == {"tool_budget": 30, "timeout": 99}

    def test_values_stringified(self, tmp_path):
        f = tmp_path / "p.yml"
        f.write_text("env: {INGEST_MAX_NEW: 5}\n", encoding="utf-8")
        env, _ = assembly.load_profile(str(f), None, _profile_spec())
        assert env["INGEST_MAX_NEW"] == "5"


class TestApplyProfileFlags:
    def _args(self, **kw):
        base = {
            "interactive": None,
            "output": None,
            "tool_budget": None,
            "timeout": None,
            "thinking": None,
            "no_thinking": False,
        }
        base.update(kw)
        return SimpleNamespace(**base)

    def test_config_fills_unset(self):
        args = self._args()
        launcher._apply_profile_flags(args, {"output": "quiet", "timeout": 99})
        assert args.output == "quiet" and args.timeout == 99

    def test_cli_explicit_wins(self):
        args = self._args(output="human")
        launcher._apply_profile_flags(args, {"output": "quiet"})
        assert args.output == "human"

    def test_builtin_default_fills_rest(self):
        args = self._args()
        launcher._apply_profile_flags(args, {})
        assert (args.tool_budget, args.thinking, args.interactive) == (60, "off", False)

    def test_unknown_flag_rejected(self):
        with pytest.raises(SystemExit, match="未知 flag"):
            launcher._apply_profile_flags(self._args(), {"flag": 1})

    def test_invalid_output_rejected(self):
        with pytest.raises(SystemExit, match="output 非法"):
            launcher._apply_profile_flags(self._args(), {"output": "json"})

    def test_budget_must_be_int(self):
        with pytest.raises(SystemExit, match="tool_budget"):
            launcher._apply_profile_flags(self._args(), {"tool_budget": "60"})
