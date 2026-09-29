import argparse
import contextlib
import io
import json
import os
import sys
import time
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent import assembly, journal, launcher


class ResultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ok-result-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / "run"
        self.run.mkdir()

    def evidence(self, *, stop="stop", events=True, tools=True):
        header = {"type": "session", "id": "session-1"}
        messages = []
        if tools:
            messages.append({"role": "assistant", "stopReason": "toolUse",
                             "content": [{"type": "toolCall", "name": "bash"}],
                             "usage": {"input": 3, "output": 1, "totalTokens": 4}})
            messages.append({"role": "toolResult", "content": []})
        messages.append({"role": "assistant", "stopReason": stop,
                         "errorMessage": "test error" if stop == "error" else None,
                         "content": [{"type": "text", "text": "finished"}],
                         "usage": {"input": 7, "output": 2, "totalTokens": 9}})
        records = [header] + [{"type": "message", "message": m} for m in messages]
        (self.run / "session.jsonl").write_text("\n".join(map(json.dumps, records)) + "\n")
        if events:
            records = [header]
            if tools:
                records.append({"type": "tool_execution_start", "toolName": "bash"})
            records += [{"type": "message_end", "message": m} for m in messages]
            (self.run / "events.jsonl").write_text("\n".join(map(json.dumps, records)) + "\n")

    def result(self, *, status="done", validation="passed", run_id="run-1", **overrides):
        data = {
            "version": 1, "run_id": run_id, "task": "hello", "slug": "hello",
            "status": status, "validation_status": validation,
            "checks": [{"name": "hello", "status": "passed"}], "reason": "checked",
        }
        data.update(overrides)
        (self.run / "task_result.json").write_text(json.dumps(data))

    def finish(self, code=0, interactive=False):
        return journal.finalize(self.run, "hello", "hello", code, interactive, run_id="run-1")

    def test_zero_exit_without_evidence_is_not_success(self):
        for interactive in [False, True]:
            with self.subTest(interactive=interactive):
                s = self.finish(interactive=interactive)
                self.assertEqual((s.status, s.exit_code), ("no_output", 3))

    def test_interactive_session_is_audited(self):
        self.evidence(events=False); self.result()
        s = self.finish(interactive=True)
        self.assertEqual((s.status, s.exit_code), ("done", 0))
        self.assertEqual(s.session_file, "session.jsonl")
        self.assertEqual(s.tool_calls, 1)
        self.assertEqual(s.usage.total_tokens, 13)

    def test_json_mode_does_not_double_count_session(self):
        self.evidence(); self.result()
        s = self.finish()
        self.assertEqual(s.usage.total_tokens, 13)
        self.assertEqual(s.exit_code, 0)

    def test_llm_error_overrides_success_result_and_zero_process_exit(self):
        self.evidence(stop="error"); self.result()
        s = self.finish()
        self.assertEqual((s.status, s.exit_code), ("llm_error", 3))

    def test_process_error_overrides_success(self):
        self.evidence(); self.result()
        self.assertEqual(self.finish(1).status, "pi_error")
        self.assertEqual(self.finish(130).status, "interrupted")

    def test_missing_task_result_requires_attention(self):
        self.evidence()
        self.assertEqual((self.finish().status, self.finish().exit_code), ("incomplete", 2))

    def test_business_states_map_to_exit_codes(self):
        self.evidence()
        for status, validation, code in [
            ("done", "passed", 0), ("done_with_warnings", "warnings", 0),
            ("needs_human", "skipped", 2), ("skipped_incomplete", "skipped", 2),
            ("failed", "failed", 3),
        ]:
            with self.subTest(status=status):
                self.result(status=status, validation=validation)
                self.assertEqual(self.finish().exit_code, code)

    def test_stale_or_wrong_task_result_is_rejected(self):
        self.evidence()
        for override in [{"run_id": "old"}, {"task": "other"}, {"slug": "other"}, {"version": 99}]:
            with self.subTest(override=override):
                self.result(**override)
                self.assertEqual(self.finish().status, "invalid_result")

    def test_done_cannot_hide_failed_or_missing_checks(self):
        self.evidence()
        for override in [{"validation": "failed"}, {"checks": []},
                         {"checks": [{"name": "bad", "status": "failed"}]}]:
            with self.subTest(override=override):
                self.result(**override)
                self.assertEqual(self.finish().status, "invalid_result")

    def test_no_tool_evidence_is_not_done(self):
        self.evidence(tools=False); self.result()
        self.assertEqual(self.finish().status, "incomplete")

    def test_truncated_turn_is_not_done(self):
        self.evidence(stop="length"); self.result()
        self.assertEqual(self.finish().status, "incomplete")

    def test_missing_session_is_audit_failure(self):
        self.evidence(); self.result()
        (self.run / "session.jsonl").unlink()
        self.assertEqual(self.finish().status, "audit_error")

    def test_bad_audit_record_is_not_silently_ignored(self):
        self.evidence(); self.result()
        with (self.run / "events.jsonl").open("a") as f:
            f.write('{"type":')
        self.assertEqual(self.finish().status, "audit_error")


    def test_malformed_message_reports_audit_error(self):
        self.evidence(); self.result()
        with (self.run / "events.jsonl").open("a") as f:
            f.write(json.dumps({"type": "message_end", "message": "bad"}) + "\n")
        self.assertEqual(self.finish().status, "audit_error")

    def test_prompt_omits_provider_credentials(self):
        spec = assembly.SkillSpec("hello", "", [], ["FOO", "SERVICE_TOKEN"], {})
        prompt = launcher.build_prompt("hello", spec, {
            "FOO": "public", "OPENAI_API_KEY": "provider-secret",
            "SERVICE_TOKEN": "task-secret"})
        self.assertIn("public", prompt)
        self.assertNotIn("provider-secret", prompt)
        self.assertNotIn("task-secret", prompt)

    def test_silent_pi_process_hits_wall_clock_deadline(self):
        renderer = launcher.QuietRenderer(io.StringIO(), io.StringIO())
        with patch.object(launcher, "REPO_ROOT", self.root), contextlib.redirect_stderr(io.StringIO()):
            start = time.monotonic()
            code = launcher.run_pi_json(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                dict(os.environ), self.run, renderer, 0.2)
        self.assertEqual(code, 2)
        self.assertLess(time.monotonic() - start, 3)

    def test_system_prompt_language_rules_are_loaded_from_file(self):
        prompt = (Path(launcher.__file__).parent / "prompts/system.md").read_text()
        self.assertIn("Reasoning language:", prompt)
        self.assertIn("in English", prompt)
        self.assertIn("Noninteractive runs (default)", prompt)
        self.assertIn("Simplified Chinese", prompt)
        self.assertIn("latest substantive human request", prompt)

    def test_make_run_dir_rejects_traversal_and_reuse(self):
        with patch.object(assembly, "REPO_ROOT", self.root):
            for slug in ["../outside", "/tmp/outside", ".", ".."]:
                with self.assertRaises(assembly.PreflightError):
                    assembly.make_run_dir("hello", slug, "stamp")
            assembly.make_run_dir("hello", "hello", "stamp")
            with self.assertRaises(assembly.PreflightError):
                assembly.make_run_dir("hello", "hello", "stamp")

    def test_launcher_returns_business_exit_not_pi_exit(self):
        (self.root / "task/hello").mkdir(parents=True)
        # venv preflight 契约: launcher 要求 REPO_ROOT/.venv/bin/python 存在
        (self.root / ".venv/bin").mkdir(parents=True)
        (self.root / ".venv/bin/python").write_text("", encoding="utf-8")
        spec = assembly.SkillSpec("hello", "", [], ["FOO"], {"hello": {"args": {}}})
        args = argparse.Namespace(task="hello", set=[], interactive=False,
                                  output="quiet", tool_budget=60, timeout=1800,
                                          thinking="off", config=None, profile=None)
        for outcome, expected in [("error", 3), ("needs_human", 2), ("done", 0)]:
            def fake_pi(cmd, env, run_dir, renderer, _outcome=outcome):
                self.evidence(stop="error" if outcome == "error" else "stop")
                self.result(status="needs_human" if outcome == "needs_human" else "done",
                            validation="skipped" if outcome == "needs_human" else "passed",
                            run_id=env["AGENT_RUN_ID"])
                self.assertIn("--no-extensions", cmd)
                self.assertEqual(json.loads(env["AGENT_SCRIPTS_JSON"]), ["hello"])
                return 0
            with self.subTest(outcome=outcome), contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(launcher, "REPO_ROOT", self.root))
                stack.enter_context(patch.object(launcher, "find_pi", return_value=("node", "pi")))
                stack.enter_context(patch.object(launcher.envguard, "load_env_file", return_value={}))
                stack.enter_context(patch.object(assembly, "load_skill", return_value=spec))
                stack.enter_context(patch.object(assembly, "make_run_dir", return_value=self.run))
                stack.enter_context(patch.object(assembly, "preflight"))
                stack.enter_context(patch.object(assembly, "render_models_json"))
                stack.enter_context(patch.object(assembly, "render_guard"))
                stack.enter_context(patch.object(launcher, "run_pi_json", side_effect=fake_pi))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                self.assertEqual(launcher.cmd_run(args), expected)


if __name__ == "__main__":
    unittest.main()
