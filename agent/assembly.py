"""assembly: 装配层。

- skill.yaml 读取与 preflight（required_env 缺失即拒绝启动并打印注入清单）
- .pi/skills/<task> symlink 校验
- models.json 渲染（OpenAI 兼容 provider，baseUrl/apiKey 从 env 快照）
- 运行工作区 runs/<ts>/<task>/<slug>/ 创建
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


class PreflightError(Exception):
    """装配失败，附人类可读说明。"""


@dataclass
class SkillSpec:
    name: str
    description: str
    required_env: list[str]
    optional_env: list[str]
    scripts: dict[str, dict]
    status_script: str | None = None


def task_dir(task: str) -> Path:
    return REPO_ROOT / "task" / task


def load_skill(task: str) -> SkillSpec:
    path = task_dir(task) / "skill.yaml"
    if not path.is_file():
        raise PreflightError(f"task '{task}' 缺少 skill.yaml: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise PreflightError(f"skill.yaml 格式错误（应为 mapping）: {path}")
    scripts = dict(data.get("scripts") or {})
    status_script = data.get("status_script")
    if status_script is not None and (not isinstance(status_script, str)
                                      or status_script not in scripts):
        raise PreflightError("status_script must name a declared task script")
    return SkillSpec(
        name=data.get("name", task),
        description=data.get("description", ""),
        required_env=list(data.get("required_env") or []),
        optional_env=list(data.get("optional_env") or []),
        scripts=scripts,
        status_script=status_script,
    )


def preflight(task: str, env: dict[str, str]) -> SkillSpec:
    """校验 skill 装配与 required_env；失败抛 PreflightError。"""
    spec = load_skill(task)

    skill_md = task_dir(task) / "SKILL.md"
    if not skill_md.is_file():
        raise PreflightError(f"task '{task}' 缺少 SKILL.md: {skill_md}")

    missing = [key for key in spec.required_env if key not in env]
    if missing:
        lines = [f"required_env 缺失，拒绝启动 task '{task}':"]
        lines += [f"  - {key}" for key in missing]
        lines.append("用 --set KEY=VALUE 注入，或在 .env 配置后重试。")
        raise PreflightError("\n".join(lines))
    return spec


def render_models_json(env: dict[str, str]) -> Path:
    """渲染 OpenAI 兼容 provider 到 agent/runtime/models.json。

    apiKey 用 $OPENAI_API_KEY 插值：pi 子进程环境里有该值（envguard 传入），
    磁盘上不落明文密钥。
    """
    agent_dir = REPO_ROOT / "agent" / "runtime"
    agent_dir.mkdir(parents=True, exist_ok=True)

    base_url = env.get("OPENAI_BASE_URL", "").rstrip("/")
    model_id = env.get("AGENT_LLM_MODEL", "")
    if not base_url or not model_id:
        raise PreflightError(
            "缺少 OPENAI_BASE_URL / AGENT_LLM_MODEL（.env 或环境变量），无法渲染 models.json"
        )

    provider = {
        "baseUrl": base_url,
        "api": "openai-completions",
        "apiKey": "$OPENAI_API_KEY",
        "compat": {
            "supportsDeveloperRole": False,
            "supportsReasoningEffort": False,
        },
        "models": [
            {
                "id": model_id,
                "name": model_id,
                "reasoning": False,
                "input": ["text"],
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                "contextWindow": 128000,
                "maxTokens": 16384,
                # 采样参数（2026-09-24 二次修订）：temperature 0 贪心触发长会话
                # 重复退化（"Let me try" ×26），抬到 0.2 实测仍不足（三次 run
                # 均以重复循环告终），再抬到 0.4；frequency_penalty 经
                # samplingParams 原样直传 vLLM，专惩重复 token，代价小于继续升温
                "samplingParams": {
                    "temperature": 0.4,
                    "top_p": 0.95,
                    "frequency_penalty": 0.3,
                },
            }
        ],
    }
    models_path = agent_dir / "models.json"
    models_path.write_text(
        json.dumps({"providers": {"ok-llm": provider}}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return models_path


def render_guard() -> Path:
    """复制禁直跑守卫到 runtime/extensions/（PI_CODING_AGENT_DIR 全局扩展位，必加载）。"""
    src = REPO_ROOT / "agent" / "extensions" / "bootstrap-guard.ts"
    dst_dir = REPO_ROOT / "agent" / "runtime" / "extensions"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


def make_run_dir(task: str, slug: str, ts: str) -> Path:
    for label, value in (("task", task), ("slug", slug), ("timestamp", ts)):
        if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}", value):
            raise PreflightError(f"invalid {label}: {value!r}")
    run_dir = REPO_ROOT / "runs" / ts / task / slug
    if run_dir.resolve() != run_dir:
        raise PreflightError("run directory must not traverse symlinks")
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir
