"""launcher: 启动器 CLI。

用法:
    uv run python main.py run --task hello --set FOO=bar [--no-interactive]
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
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from agent import assembly, envguard, journal
from agent.assembly import PreflightError

REPO_ROOT = Path(__file__).resolve().parent.parent
EXIT_OK = 0
EXIT_NEEDS_HUMAN = 2
EXIT_FATAL = 3


def find_pi() -> str:
    """定位 pi 可执行文件。"""
    pi = shutil.which("pi")
    if pi:
        return pi
    for candidate in (
        Path.home() / ".local" / "bin" / "pi",
        Path("/root/.local/bin/pi"),
    ):
        if candidate.is_file():
            return str(candidate)
    raise SystemExit(
        "pi 未安装。安装: npm install -g --ignore-scripts @earendil-works/pi-coding-agent "
        "(npmmirror: --registry=https://registry.npmmirror.com)"
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


def build_prompt(task: str, spec: assembly.SkillSpec, overrides: dict[str, str]) -> str:
    """组装首条任务指令：任务名 + 注入参数 + 指向 SKILL.md。"""
    lines = [
        f"执行任务: {task}",
        "",
        f"完整任务说明在 task/{task}/SKILL.md —— 请先完整阅读它，再按其流程执行。",
        "",
    ]
    if overrides:
        lines.append("本次注入的参数（环境变量，只读）:")
        for key, val in sorted(overrides.items()):
            shown = val if len(val) <= 120 else val[:117] + "..."
            lines.append(f"  {key}={shown}")
        lines.append("")
    if spec.optional_env:
        lines.append(
            f"可选参数（本次未注入则忽略）: {', '.join(spec.optional_env)}"
        )
    lines.append(
        "约束提醒: 你没有任何直接写盘工具（write/edit 会被拒绝）；"
        "一切操作通过运行 task 脚本完成（bash 仅允许运行当前任务的脚本与只读命令）。"
    )
    return "\n".join(lines)


def render_event(event: dict, out) -> None:
    """把单个 pi json 事件渲染为人类可读轨迹。"""
    etype = event.get("type")

    if etype == "message_update":
        ame = event.get("assistantMessageEvent") or {}
        delta = ame.get("delta")
        if not delta:
            return
        if ame.get("type") == "thinking_delta":
            out.write(delta)
            out.flush()
        elif ame.get("type") == "text_delta":
            out.write(delta)
            out.flush()

    elif etype == "tool_execution_start":
        tool = event.get("toolName", "?")
        args = event.get("args") or {}
        args_str = json.dumps(args, ensure_ascii=False)
        if len(args_str) > 200:
            args_str = args_str[:197] + "..."
        print(f"\n[tool] {tool} {args_str}")

    elif etype == "tool_execution_end":
        result = event.get("result")
        text = ""
        if isinstance(result, dict):
            content = result.get("content")
            if isinstance(content, list):
                text = " ".join(
                    block.get("text", "") for block in content if isinstance(block, dict)
                )
            elif content is not None:
                text = str(content)
        text = text.replace("\n", " ")
        if len(text) > 300:
            text = text[:297] + "..."
        flag = "ERR" if event.get("isError") else "ok"
        print(f"[tool:{flag}] {text}")

    elif etype == "message_end":
        message = event.get("message") or {}
        if message.get("role") == "assistant" and message.get("stopReason") == "error":
            print(f"\n[error] LLM 请求失败: {message.get('errorMessage', '?')}")

    elif etype == "auto_retry_start":
        print("[retry] 瞬时错误，自动重试...")


def run_pi_json(pi_bin: str, cmd: list[str], env: dict[str, str], run_dir: Path) -> int:
    """非交互: pi -p --mode json，逐行转发渲染 + 原始事件落盘。"""
    events_path = run_dir / "events.jsonl"
    with events_path.open("w", encoding="utf-8") as sink, subprocess.Popen(
        cmd, env=env, cwd=str(REPO_ROOT), stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1,
    ) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            if not line:
                continue
            sink.write(line + "\n")
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # pi 的非 json 输出（警告等）直接透传
                print(line)
                continue
            render_event(event, sys.stdout)
        proc.wait()
        return proc.returncode


def cmd_run(args: argparse.Namespace) -> int:
    task = args.task
    if not (REPO_ROOT / "task" / task).is_dir():
        print(f"未知任务: {task}（task/{task}/ 不存在）", file=sys.stderr)
        return EXIT_FATAL

    overrides = parse_set(args.set)
    dotenv = envguard.load_env_file(REPO_ROOT / ".env")

    interactive = (not args.no_interactive) and sys.stdout.isatty()

    try:
        spec = assembly.load_skill(task)
    except PreflightError as exc:
        print(f"[preflight] {exc}", file=sys.stderr)
        return EXIT_FATAL

    slug = resolve_slug(task, overrides, dotenv)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = assembly.make_run_dir(task, slug, ts)

    pi_bin = find_pi()
    agent_dir = REPO_ROOT / "agent" / "runtime"
    agent_vars = {
        "AGENT_TASK": task,
        "AGENT_SLUG": slug,
        "AGENT_RUN_DIR": str(run_dir),
        "AGENT_INTERACTIVE": "1" if interactive else "0",
        "PI_CODING_AGENT_DIR": str(agent_dir),
    }

    env = envguard.build_snapshot(dotenv, overrides, agent_vars)

    try:
        # preflight（required_env + skills symlink）在快照构造后执行
        assembly.preflight(task, env)
        assembly.render_models_json(env)
    except PreflightError as exc:
        print(f"[preflight] {exc}", file=sys.stderr)
        return EXIT_FATAL

    model_id = env.get("AGENT_LLM_MODEL", "")
    prompt = build_prompt(task, spec, overrides)
    session_name = f"{task}/{slug}"

    # pi 公共参数: 显式装配（不依赖 .pi/ 项目目录）
    common_args = [
        "--approve",
        "--provider", "ok-llm", "--model", model_id,
        "--name", session_name,
        "--session-dir", str(run_dir),
        "--thinking", "off",
        "--skill", str(REPO_ROOT / "task" / task),  # SKILL.md 即任务说明书
        "-e", str(REPO_ROOT / "agent" / "extensions" / "path-guard.ts"),
        "-e", str(REPO_ROOT / "agent" / "extensions" / "ask-user.ts"),
    ]

    if interactive:
        # 交互模式: pi TUI 接管终端（ask_user 可用）
        cmd = [pi_bin, *common_args, prompt]
        code = subprocess.call(cmd, env=env, cwd=str(REPO_ROOT))
        summary = journal.finalize(run_dir, task, slug, code, interactive=True)
        journal.print_summary(summary, run_dir)
        return EXIT_OK if code == 0 else EXIT_FATAL

    # 非交互: json 事件流模式
    cmd = [pi_bin, "-p", "--mode", "json", *common_args, prompt]
    print(f"[launcher] pi = {pi_bin}")
    print(f"[launcher] task={task} slug={slug} model=ok-llm/{model_id}")
    print(f"[launcher] run_dir={run_dir}\n")
    code = run_pi_json(pi_bin, cmd, env, run_dir)
    summary = journal.finalize(run_dir, task, slug, code, interactive=False)
    journal.print_summary(summary, run_dir)
    return EXIT_OK if code == 0 else EXIT_FATAL


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
        "--no-interactive", action="store_true",
        help="禁用交互（ask_user 降级为 needs_human 分支）",
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

    # 简写兼容: 首参数不是子命令时按 run 处理（uv run ok --task hello ≡ ok run --task hello）
    argv = sys.argv[1:]
    if argv and argv[0] not in {"run", "batch", "pending", "status"}:
        argv = ["run", *argv]

    args = parser.parse_args(argv)
    sys.exit(args.func(args))
