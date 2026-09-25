import argparse
import contextlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agent import assembly, launcher


class InteractionTests(unittest.TestCase):
    def test_tools_follow_effective_mode(self):
        for requested, tty, expected in [(False, True, False), (True, True, True), (True, False, False)]:
            with self.subTest(requested=requested, tty=tty), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "task/hello").mkdir(parents=True)
                (root / ".venv/bin").mkdir(parents=True)
                (root / ".venv/bin/python").touch()
                run = root / "run"
                run.mkdir()
                spec = assembly.SkillSpec("hello", "", [], [], {"hello": {"args": {}}})
                args = argparse.Namespace(task="hello", set=[], interactive=requested,
                                          output="quiet", tool_budget=60, timeout=1800)
                stdout = io.StringIO()
                stdout.isatty = lambda: tty
                with contextlib.ExitStack() as stack:
                    stack.enter_context(contextlib.redirect_stdout(stdout))
                    stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
                    for obj, name, value in [
                        (launcher, "REPO_ROOT", root),
                        (launcher, "RENDERERS", {"quiet": Mock(return_value=Mock())}),
                    ]:
                        stack.enter_context(patch.object(obj, name, value))
                    for obj, name, value in [
                        (launcher, "find_pi", ("node", "pi")),
                        (launcher.envguard, "load_env_file", {}),
                        (assembly, "load_skill", spec),
                        (assembly, "make_run_dir", run),
                        (assembly, "preflight", None),
                        (assembly, "render_models_json", None),
                        (assembly, "render_guard", None),
                        (launcher.journal, "finalize", SimpleNamespace(exit_code=2)),
                        (launcher.journal, "print_summary", None),
                    ]:
                        stack.enter_context(patch.object(obj, name, return_value=value))
                    json_run = stack.enter_context(patch.object(launcher, "run_pi_json", return_value=0))
                    tui_run = stack.enter_context(patch.object(launcher.subprocess, "call", return_value=0))
                    self.assertEqual(launcher.cmd_run(args), 2)
                    active, inactive = (tui_run, json_run) if expected else (json_run, tui_run)
                    active.assert_called_once()
                    inactive.assert_not_called()
                    cmd = active.call_args.args[0]
                    env = active.call_args.kwargs["env"] if expected else active.call_args.args[1]
                    self.assertEqual(env["AGENT_INTERACTIVE"], "1" if expected else "0")
                    self.assertEqual(cmd[cmd.index("--tools") + 1],
                                     "read,bash,ask_user" if expected else "read,bash")
                    extensions = [cmd[i+1] for i, v in enumerate(cmd) if v == "-e"]
                    self.assertEqual(any(v.endswith("/ask-user.ts") for v in extensions), expected)
                    self.assertEqual("ask_user" in cmd[-1], expected)
                    if not expected:
                        self.assertIn("pending / needs_human", cmd[-1])
                        self.assertNotIn("NOT_INTERACTIVE", cmd[-1])
                    self.assertIn("--no-extensions", cmd)


if __name__ == "__main__":
    unittest.main()
