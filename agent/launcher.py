"""launcher: 启动器 CLI。

用法:
    uv run python main.py run --task hello --set FOO=bar [--interactive]
    uv run python main.py batch ...   (M4)
    uv run python main.py pending     (M4)
    uv run python main.py status      (M4)

run 流程: envguard 快照 -> assembly preflight/渲染 -> 拉起 pi ->
流式渲染思考与工具轨迹 -> journal 归档。
启动器自身不发起任何 LLM 调用。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import threading
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from agent import assembly, bootstrap, envguard, journal
from agent.assembly import PreflightError

REPO_ROOT = Path(__file__).resolve().parent.parent
EXIT_OK = 0
EXIT_NEEDS_HUMAN = 2
EXIT_FATAL = 3


def find_pi() -> tuple[str, str]:
    """定位 vendor pi runtime，返回 [node, cli.js]。不依赖全局安装与 PATH。"""
    try:
        return bootstrap.pi_runtime()
    except SystemExit as exc:
        raise SystemExit(
            f"{exc}（pi 不走全局安装，由 agent/vendor 自包含提供）"
        )


def parse_set(items: list[str] | None) -> dict[str, str]:
    """解析 --set KEY=VALUE 列表。"""
    result: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"--set 格式错误（应为 KEY=VALUE）: {item}")
        key, _, val = item.partition("=")
        if not key:
            raise SystemExit(f"--set KEY 为空: {item}")
        result[key] = val
    return result


def resolve_slug(task: str, overrides: dict[str, str], dotenv: dict[str, str]) -> str:
    """确定本次运行的 slug（运行目录名 / session 名）。"""
    if task == "ingest":
        name = overrides.get("INGEST_MODEL_NAME") or dotenv.get("INGEST_MODEL_NAME")
        if name:
            return name
        seed = overrides.get("INGEST_SEED_URL") or dotenv.get("INGEST_SEED_URL") or "seed"
        # arxiv:2305.16175 -> arxiv-2305.16175（目录名安全化）
        return "".join(c if c.isalnum() or c in "-._" else "-" for c in seed)[:80]
    if task == "optimize":
        name = overrides.get("OPTIMIZE_MODEL_NAME") or dotenv.get("OPTIMIZE_MODEL_NAME")
        return name or "adhoc"
    return task


def build_prompt(task: str, spec: assembly.SkillSpec, overrides: dict[str, str],
                 *, interactive: bool = False) -> str:
    """Build the English run instruction. Response language is in the system prompt."""
    lines = [
        f"Execute task: {task}",
        "",
        f"Read task/{task}/SKILL.md in full, then follow its workflow.",
        "",
    ]
    if interactive:
        lines.append("This is an interactive run. Use the available ask_user tool when a human decision is required.")
    else:
        lines.append("This is a noninteractive run; no human can answer during execution. "
                     "Record pending / needs_human for required decisions, continue independent work, "
                     "and finish safely. Do not probe the run mode.")
        lines.append("Write the final user-facing answer in Simplified Chinese, even though "
                     "these instructions and tool feedback are in English.")
    if overrides:
        lines.append("Injected task parameters (read-only environment):")
        for key, val in sorted(overrides.items()):
            if key not in spec.required_env + spec.optional_env:
                continue
            if any(part in key.upper() for part in ("SECRET", "TOKEN", "PASSWORD", "API_KEY")):
                val = "[redacted]"
            shown = val if len(val) <= 120 else val[:117] + "..."
            lines.append(f"  {key}={shown}")
        lines.append("")
    if spec.optional_env:
        lines.append(f"Optional parameters (ignore if absent): {', '.join(spec.optional_env)}")
    lines.append("You have no direct write/edit tools. Write through declared task scripts only; "
                 "bash allows those scripts and approved read-only commands.")
    lines.append("The working directory is already the project root. Do not use cd, shell operators, "
                 "or variable expansion.")
    return "\n".join(lines)


# ---- 输出渲染器（--output human|quiet|raw）----
# 展示层约定：pi --mode json 事件流是唯一输入；events.jsonl 永远存全量原始
# 事件，渲染器只裁剪 stdout 呈现，不损失任何信息。

DIM, RESET = "\033[2m", "\033[0m"
CYAN, RED = "\033[36m", "\033[31m"


def _color(out, code: str, text: str) -> str:
    return f"{code}{text}{RESET}" if hasattr(out, "isatty") and out.isatty() else text


def _render_tool_args(tool: str, args: dict) -> str:
    """按工具类型渲染调用行：bash 命令、read 相对路径，其余 JSON 截断。"""
    if tool == "bash" and isinstance(args.get("command"), str):
        cmd = args["command"]
        return "$ " + (cmd[:197] + "..." if len(cmd) > 200 else cmd)
    if tool == "read" and isinstance(args.get("path"), str):
        path = args["path"]
        prefix = str(REPO_ROOT) + "/"
        if path.startswith(prefix):
            path = path[len(prefix):]
        return path
    args_str = json.dumps(args, ensure_ascii=False)
    return (args_str[:197] + "...") if len(args_str) > 200 else args_str


def _tool_result_text(event: dict) -> str:
    """提取工具结果纯文本（不做 JSON 美化；全文永远在 events.jsonl）。"""
    result = event.get("result")
    if not isinstance(result, dict):
        return ""
    content = result.get("content")
    if isinstance(content, list):
        return " ".join(
            block.get("text", "") for block in content if isinstance(block, dict)
        )
    return str(content) if content is not None else ""


class HumanRenderer:
    """默认：流式轨迹（思考/答案分段，工具块化）+ 收尾横幅。

    工具成功 → 单行摘要（字节数 + 耗时）；失败 → 展开错误文本（≤300 字符）。
    show_thinking=False 时思考段整体隐藏（--no-thinking）。
    """

    def __init__(self, out, err, show_thinking: bool = True) -> None:
        self.out, self.err = out, err
        self.show_thinking = show_thinking
        self._stream: str | None = None  # thinking | text | tool | None
        self._t0: float | None = None

    def header(self, task: str, slug: str, model: str, run_dir: Path) -> None:
        print(f"▶ {task}/{slug} · {model}", file=self.out)
        print(file=self.out)

    def _separator(self, label: str) -> None:
        print("\n" + _color(self.out, DIM, f"┈ {label} " + "┈" * 32), file=self.out)

    def line(self, raw: str, event: dict | None) -> None:
        if event is None:  # pi 的非 json 输出（警告等）直接透传
            print(raw, file=self.out)
            return
        etype = event.get("type")
        if etype == "message_update":
            ame = event.get("assistantMessageEvent") or {}
            delta = ame.get("delta")
            if not delta:
                return
            kind = ame.get("type")
            if kind == "thinking_delta":
                if not self.show_thinking:
                    return
                if self._stream != "thinking":
                    self._separator("thinking")
                    self._stream = "thinking"
                print(_color(self.out, DIM,
                             "▏ " + delta.replace("\n", "\n▏ ")),
                      end="", flush=True, file=self.out)
            elif kind == "text_delta":
                if self._stream != "text":
                    # thinking→answer、tool/初始→answer 都要开段
                    self._separator("answer")
                self._stream = "text"
                print(delta, end="", flush=True, file=self.out)
        elif etype == "tool_execution_start":
            if self._stream in ("thinking", "text"):
                print(file=self.out)  # 收口流式段
            tool = event.get("toolName", "?")
            args = event.get("args") or {}
            print("\n" + _color(self.out, CYAN, f"┌─ {tool} " + "─" * 32),
                  file=self.out)
            print(_render_tool_args(tool, args), file=self.out)
            self._t0 = time.monotonic()
            self._stream = "tool"
        elif etype == "tool_execution_end":
            text = _tool_result_text(event)
            dt = time.monotonic() - self._t0 if self._t0 is not None else 0.0
            if event.get("isError"):
                single = text.replace("\n", " ")
                body = single[:297] + "..." if len(single) > 300 else single
                print(_color(self.out, RED, "└─ ✗ ") + body, file=self.out)
            else:
                size = len(text.encode("utf-8"))
                print(f"└─ ✓ {size}B · {dt:.1f}s", file=self.out)
            self._stream = None
        elif etype == "message_end":
            message = event.get("message") or {}
            if message.get("role") == "assistant" and message.get("stopReason") == "error":
                print(f"\n{_color(self.out, RED, '✗ LLM: ')}"
                      f"{message.get('errorMessage', '?')}", file=self.out)
        elif etype == "auto_retry_start":
            print(_color(self.out, DIM, "⟳ retry"), file=self.out)

    def footer(self, summary, run_dir: Path, elapsed: float) -> None:
        u = summary.usage
        print(f"\n{'═' * 62}", file=self.out)
        print(f"{'✅' if summary.exit_code == 0 else '❌'} {summary.status} · "
              f"tools {summary.tool_calls} · tokens {u.input}→{u.output} · "
              f"{elapsed:.1f}s", file=self.out)
        if summary.last_error:
            print(f"  last error: {summary.last_error[:200]}", file=self.out)
        print(f"  run: {run_dir}", file=self.out)
        if summary.session_file:
            print(f"  session: {run_dir / summary.session_file}", file=self.out)


class QuietRenderer:
    """CI/脚本消费：全程静默，结束时输出一行 JSON 摘要。"""

    def __init__(self, out, err) -> None:
        self.out, self.err = out, err

    def header(self, task: str, slug: str, model: str, run_dir: Path) -> None:
        pass

    def line(self, raw: str, event: dict | None) -> None:
        pass  # 轨迹不看 stdout；需要时读 events.jsonl

    def footer(self, summary, run_dir: Path, elapsed: float) -> None:
        payload = {
            "task": summary.task,
            "slug": summary.slug,
            "status": summary.status,
            "exit": summary.exit_code,
            "run_dir": str(run_dir),
            "tool_calls": summary.tool_calls,
            "tokens": summary.usage.total_tokens,
            "duration_s": round(elapsed, 1),
        }
        if summary.last_error:
            payload["last_error"] = summary.last_error[:200]
        print(json.dumps(payload, ensure_ascii=False), file=self.out)


class RawRenderer:
    """机器管道：pi 事件行原样透传，launcher 只当审计代理。"""

    def __init__(self, out, err) -> None:
        self.out, self.err = out, err

    def header(self, task: str, slug: str, model: str, run_dir: Path) -> None:
        pass

    def line(self, raw: str, event: dict | None) -> None:
        print(raw, file=self.out)

    def footer(self, summary, run_dir: Path, elapsed: float) -> None:
        pass  # 结果读 journal.json；退出码即结论


RENDERERS = {"human": HumanRenderer, "quiet": QuietRenderer, "raw": RawRenderer}


def run_pi_json(cmd: list[str], env: dict[str, str], run_dir: Path,
                renderer, timeout_s: float = 0.0) -> int:
    """Stream Pi events and enforce a wall-clock deadline independent of output."""
    events_path = run_dir / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    timed_out = threading.Event()
    budget_limit = int(env.get("AGENT_TOOL_BUDGET", "60"))
    tool_requests = 0

    def stop_group(proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            def force_stop() -> None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            killer = threading.Timer(5, force_stop)
            killer.daemon = True
            killer.start()
        else:
            proc.terminate()

    with events_path.open("w", encoding="utf-8") as sink, subprocess.Popen(
        cmd, env=env, cwd=str(REPO_ROOT), stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1,
        start_new_session=(os.name == "posix"),
    ) as proc:
        assert proc.stdout is not None
        def expire() -> None:
            if proc.poll() is None:
                timed_out.set()
                stop_group(proc)
        timer = threading.Timer(timeout_s, expire) if timeout_s > 0 else None
        if timer:
            timer.daemon = True
            timer.start()
        try:
            for line in proc.stdout:
                line = line.rstrip("\n")
                if not line:
                    continue
                sink.write(line + "\n")
                sink.flush()
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    renderer.line(line, None)
                    continue
                if isinstance(event, dict) and event.get("type") == "tool_execution_start":
                    tool_requests += 1
                    if tool_requests > budget_limit + 3:
                        print("[budget] Tool requests continued after exhaustion; terminating run",
                              file=sys.stderr)
                        stop_group(proc)
                        return 2
                renderer.line(line, event if isinstance(event, dict) else None)
            proc.wait()
            if timed_out.is_set():
                print(f"[timeout] Run exceeded {timeout_s:.0f} seconds", file=sys.stderr)
                return 2
            return proc.returncode
        except BaseException:
            stop_group(proc)
            raise
        finally:
            if timer:
                timer.cancel()


# ---- --config 运行方案：flags 合并 ----
# 优先级：CLI 显式 > config flags > 内置默认。argparse 侧对应选项 default=None
# 作哨兵；config 值的合法性在此统一校验（fail-closed）。

_PROFILE_FLAGS: dict = {
    "interactive": False, "output": "human", "tool_budget": 60,
    "timeout": 1800, "thinking": "off",
}


def _apply_profile_flags(args: argparse.Namespace, flags: dict) -> None:
    """校验并合并 config flags；CLI 已显式传入的选项不被覆盖。"""
    for key, value in flags.items():
        if key not in _PROFILE_FLAGS:
            raise SystemExit(f"[config] 未知 flag: {key}"
                             f"（允许: {', '.join(sorted(_PROFILE_FLAGS))}）")
        if key == "output" and value not in sorted(RENDERERS):
            raise SystemExit(f"[config] output 非法: {value!r}")
        if key == "thinking" and value not in ("off", "minimal", "low", "medium", "high"):
            raise SystemExit(f"[config] thinking 非法: {value!r}")
        if key in ("tool_budget", "timeout") and (
                isinstance(value, bool) or not isinstance(value, int)):
            raise SystemExit(f"[config] {key} 必须为整数")
        if getattr(args, key, None) is None:
            setattr(args, key, value)
    for key, default in _PROFILE_FLAGS.items():
        if getattr(args, key, None) is None:
            setattr(args, key, default)

def cmd_run(args: argparse.Namespace) -> int:
    task = args.task
    if (not re.fullmatch(r"[A-Za-z0-9_-]+", task)
            or not (REPO_ROOT / "task" / task).is_dir()
            or (REPO_ROOT / "task" / task).resolve() != REPO_ROOT / "task" / task):
        print(f"未知任务: {task}（task/{task}/ 不存在）", file=sys.stderr)
        return EXIT_FATAL

    overrides = parse_set(args.set)
    dotenv = envguard.load_env_file(REPO_ROOT / ".env")

    try:
        spec = assembly.load_skill(task)
        profile_env, profile_flags = assembly.load_profile(
            args.config, args.profile, spec)
    except PreflightError as exc:
        print(f"[preflight] {exc}", file=sys.stderr)
        return EXIT_FATAL
    # 优先级：--set > --config 方案 > .env > 父进程（方案 env 合并进 overrides）
    overrides = {**profile_env, **overrides}
    _apply_profile_flags(args, profile_flags)

    interactive = args.interactive and sys.stdout.isatty()
    if args.interactive and not sys.stdout.isatty():
        print("[mode] stdout 非 TTY，--interactive 降级为非交互", file=sys.stderr)

    slug = resolve_slug(task, overrides, dotenv)
    run_id = uuid.uuid4().hex
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")

    # 项目 venv 是 agent 全部 bash 子进程的默认 python 环境（启动时自动注入，
    # argv 受控下 agent 无法自行 source）；缺失即拒绝启动，不代装
    venv = REPO_ROOT / ".venv"
    if not (venv / "bin" / "python").exists():
        print("[preflight] 项目 .venv 不存在或缺少 python，请先运行: uv sync",
              file=sys.stderr)
        return EXIT_FATAL

    try:
        run_dir = assembly.make_run_dir(task, slug, ts)
    except PreflightError as exc:
        print(f"[preflight] {exc}", file=sys.stderr)
        return EXIT_FATAL

    node_bin, pi_entry = find_pi()
    agent_dir = REPO_ROOT / "agent" / "runtime"
    agent_vars = {
        "AGENT_PROJECT_ROOT": str(REPO_ROOT),
        "AGENT_SCRIPTS_JSON": json.dumps(list(spec.scripts)),
        "AGENT_SCRIPT_ENV_JSON": json.dumps(spec.required_env + spec.optional_env),
        "AGENT_RUN_ID": run_id,
        "AGENT_TASK": task,
        "AGENT_SLUG": slug,
        "AGENT_RUN_DIR": str(run_dir),
        "AGENT_INTERACTIVE": "1" if interactive else "0",
        "AGENT_TOOL_BUDGET": str(args.tool_budget),
        "PI_CODING_AGENT_DIR": str(agent_dir),
        # 禁直跑令牌: bootstrap-guard 校验，缺它 pi 拒绝启动
        "AGENT_INVOKED_BY_LAUNCHER": uuid.uuid4().hex,
    }

    env = envguard.build_snapshot(dotenv, overrides, agent_vars, venv=venv)

    try:
        # preflight（required_env）在快照构造后执行
        assembly.preflight(task, env)
        decode = assembly.decode_audit(env)
        assembly.render_models_json(env)
        assembly.render_guard()
    except PreflightError as exc:
        print(f"[preflight] {exc}", file=sys.stderr)
        return EXIT_FATAL

    model_id = env.get("AGENT_LLM_MODEL", "")
    prompt = build_prompt(task, spec, overrides, interactive=interactive)
    session_name = f"{task}/{slug}"

    # pi 公共参数: 显式装配（不依赖 .pi/ 项目目录）
    common_args = [
        "--approve",
        "--no-extensions",  # Only reviewed extensions explicitly listed below.
        "--no-prompt-templates",
        "--append-system-prompt", str(REPO_ROOT / "agent" / "prompts" / "system.md"),
        "--tools", "read,bash,ask_user" if interactive else "read,bash",
        "-e", str(REPO_ROOT / "agent" / "extensions" / "bootstrap-guard.ts"),
        "--provider", "ok-llm", "--model", model_id,
        "--name", session_name,
        "--session-dir", str(run_dir),
        "--thinking", args.thinking,
        "--skill", str(REPO_ROOT / "task" / task),  # SKILL.md 即任务说明书
        "-e", str(REPO_ROOT / "agent" / "extensions" / "budget-guard.ts"),
        "-e", str(REPO_ROOT / "agent" / "extensions" / "path-guard.ts"),
    ]
    if interactive:
        common_args.extend(["-e", str(REPO_ROOT / "agent" / "extensions" / "ask-user.ts")])

    renderer = None
    elapsed = 0.0
    try:
        if interactive:
            cmd = [node_bin, pi_entry, *common_args, prompt]
            code = subprocess.call(cmd, env=env, cwd=str(REPO_ROOT))
        else:
            renderer = RENDERERS[args.output](sys.stdout, sys.stderr,
                                              show_thinking=not args.no_thinking) \
                if args.output == "human" else RENDERERS[args.output](sys.stdout,
                                                                      sys.stderr)
            cmd = [node_bin, pi_entry, "-p", "--mode", "json", *common_args, prompt]
            renderer.header(task, slug, model_id, run_dir)
            t0 = time.monotonic()
            code = run_pi_json(cmd, env, run_dir, renderer, args.timeout)
            elapsed = time.monotonic() - t0
    except KeyboardInterrupt:
        code = 130
    except OSError as exc:
        print(f"[launcher] runtime failed: {exc}", file=sys.stderr)
        code = EXIT_FATAL
    summary = journal.finalize(run_dir, task, slug, code, interactive,
                               run_id=run_id, decode=decode)
    if interactive:
        journal.print_summary(summary, run_dir)
    elif renderer is not None:
        renderer.footer(summary, run_dir, elapsed)
    return summary.exit_code



def cmd_batch(args: argparse.Namespace) -> int:
    print("batch 驱动在 M4 里程碑实现。", file=sys.stderr)
    return EXIT_FATAL


def cmd_pending(args: argparse.Namespace) -> int:
    print("pending 流程在 M4 里程碑实现。", file=sys.stderr)
    return EXIT_FATAL


def cmd_status(args: argparse.Namespace) -> int:
    runs_root = REPO_ROOT / "runs"
    if not runs_root.is_dir():
        print("尚无运行记录（runs/ 不存在）。")
        return EXIT_OK
    found = 0
    for jpath in sorted(runs_root.rglob("journal.json")):
        data = json.loads(jpath.read_text(encoding="utf-8"))
        rel = jpath.parent.relative_to(runs_root)
        print(
            f"{rel}  status={data.get('status')}  "
            f"tool_calls={data.get('tool_calls')}  "
            f"session={data.get('session_file') or '-'}"
        )
        found += 1
    if not found:
        print("尚无运行记录。")
    return EXIT_OK


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ok-optim-agent",
        description="single-cell-hub ingest/optimize agent 启动器",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="单次任务")
    p_run.add_argument("--task", required=True, help="任务名（task/ 下的目录名）")
    p_run.add_argument(
        "--set", action="append", metavar="KEY=VALUE",
        help="注入环境变量（可多次）",
    )
    p_run.add_argument(
        "--config", default=None,
        help="运行方案 YAML（相对项目根，参照 configs/ingest.profiles.example.yml）",
    )
    p_run.add_argument(
        "--profile", default=None,
        help="选择 --config 中的命名方案（profiles 键）",
    )
    p_run.add_argument(
        "--interactive", action="store_true", default=None,
        help="交互模式（TUI；默认非交互，需要人工判断时记录 needs_human）",
    )
    p_run.add_argument(
        "--output", choices=sorted(RENDERERS), default=None,
        help="非交互输出样式: human=人类轨迹 / quiet=一行 JSON 摘要 / raw=事件透传",
    )
    p_run.add_argument(
        "--tool-budget", type=int, default=None,
        help="工具调用预算（budget-guard 机械护栏，超限强制收尾）",
    )
    p_run.add_argument(
        "--no-thinking", action="store_true",
        help="隐藏思考流输出（human 输出模式；思考仍完整记录在 session）",
    )
    p_run.add_argument(
        "--thinking", default=None,
        choices=["off", "minimal", "low", "medium", "high"],
        help="推理档位（透传 pi；off=无思考流；需要端点/模型支持）",
    )
    p_run.add_argument(
        "--timeout", type=int, default=None,
        help="会话墙钟超时秒数（0 关闭；超时终止记 needs_human）",
    )
    p_run.set_defaults(func=cmd_run)

    p_batch = sub.add_parser("batch", help="批量任务（M4）")
    p_batch.add_argument("--task", required=True)
    p_batch.add_argument("--models", default=None)
    p_batch.set_defaults(func=cmd_batch)

    p_pending = sub.add_parser("pending", help="待人工项（M4）")
    p_pending.set_defaults(func=cmd_pending)

    p_status = sub.add_parser("status", help="查看运行记录")
    p_status.set_defaults(func=cmd_status)

    p_setup = sub.add_parser("setup", help="一键安装 pi runtime（幂等）")
    p_setup.set_defaults(func=lambda _args: (bootstrap.main() or EXIT_OK))

    # 简写兼容: 首参数不是子命令时按 run 处理（uv run ok --task hello ≡ ok run --task hello）
    argv = sys.argv[1:]
    if argv and argv[0] not in {"run", "batch", "pending", "status", "setup"}:
        argv = ["run", *argv]

    args = parser.parse_args(argv)
    sys.exit(args.func(args))
