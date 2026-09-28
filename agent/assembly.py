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


# 解码参数：env（DECODE_*）未设置时的默认值
# （2026-09-24 二次修订：temperature 0 贪心触发长会话重复退化（"Let me try" ×26），
# 抬到 0.2 实测仍不足（三次 run 均以重复循环告终），再抬到 0.4；frequency_penalty
# 经 samplingParams 原样直传 vLLM，专惩重复 token，代价小于继续升温）
DECODE_DEFAULTS = {
    "DECODE_TEMPERATURE": 0.4,
    "DECODE_TOP_P": 0.95,
    "DECODE_FREQUENCY_PENALTY": 0.3,
    "DECODE_MAX_TOKENS": 16384,
}
DECODE_FLOAT_RANGES = {
    "DECODE_TEMPERATURE": (0.0, 2.0),
    "DECODE_TOP_P": (0.0, 1.0),
    "DECODE_FREQUENCY_PENALTY": (-2.0, 2.0),
    "DECODE_MIN_P": (0.0, 1.0),
    "DECODE_PRESENCE_PENALTY": (-2.0, 2.0),
    "DECODE_REPETITION_PENALTY": (0.1, 2.0),
}
DECODE_INT_RANGES = {
    "DECODE_MAX_TOKENS": (64, 200_000),
    "DECODE_SEED": (0, 2**31 - 1),
    "DECODE_TOP_K": (-1, 200),  # -1 = 端点默认（关闭截断）
}
# 扩展参数：仅当 env 显式设置时才进 samplingParams（未设置 = 交给端点默认，
# 避免"发送默认值反而改变端点行为"，如 top_k 显式值会开启截断分布）
DECODE_OPTIONAL = {
    "DECODE_SEED": "seed",
    "DECODE_TOP_K": "top_k",
    "DECODE_MIN_P": "min_p",
    "DECODE_PRESENCE_PENALTY": "presence_penalty",
    "DECODE_REPETITION_PENALTY": "repetition_penalty",
}
# env 键 → OpenAI 请求体参数名（全量表驱动）
_DECODE_PARAM_NAMES = {
    "DECODE_TEMPERATURE": "temperature",
    "DECODE_TOP_P": "top_p",
    "DECODE_FREQUENCY_PENALTY": "frequency_penalty",
    "DECODE_MAX_TOKENS": "max_tokens",
    **DECODE_OPTIONAL,
}


def _decode_value(key: str, raw: str | None, default=None):
    """解析单个 DECODE_* 键；返回 (camelCase 参数名, 值)。

    raw 未设置时：有 default（核心参数）用默认值；否则返回 (参数名, None)
    （扩展参数未发送）。"""
    param = _DECODE_PARAM_NAMES[key]
    if raw is None or raw == "":
        if default is None:
            return (param, None)
        return (param, default)  # 代码默认值，视为已在合法范围内
    if key in DECODE_FLOAT_RANGES:
        try:
            value = float(raw)
        except ValueError:
            raise PreflightError(f"{key} 必须是数字: {raw!r}")
        lo, hi = DECODE_FLOAT_RANGES[key]
    elif key in DECODE_INT_RANGES:
        try:
            value = int(raw)
        except ValueError:
            raise PreflightError(f"{key} 必须是整数: {raw!r}")
        lo, hi = DECODE_INT_RANGES[key]
    else:  # pragma: no cover - 表驱动遗漏防护
        raise PreflightError(f"未知的解码参数键: {key}")
    if not (lo <= value <= hi):
        raise PreflightError(f"{key} 超出范围 [{lo}, {hi}]: {value}")
    return (param, value)


def decode_audit(env: dict[str, str]) -> dict:
    """审计视角的解码参数全量记录：核心参数为实际值，扩展参数未设置记 None。"""
    audit = {}
    for key, default in DECODE_DEFAULTS.items():
        param, value = _decode_value(key, env.get(key), default)
        audit[param] = value
    for key in DECODE_OPTIONAL:
        param, value = _decode_value(key, env.get(key))
        audit[param] = value  # 扩展参数未设置时为 None（未发送，端点默认）
    return audit


def decode_params(env: dict[str, str]) -> tuple[dict, int]:
    """从 env 快照解析解码参数（DECODE_*），带类型与范围校验。

    优先级：--set（进快照的 overrides）> .env > 父进程 > 代码默认值。
    核心参数（temperature/top_p/frequency_penalty）始终发送，max_tokens 走
    pi 的 maxTokens 字段；扩展参数（seed/top_k/min_p/presence_penalty/
    repetition_penalty）仅显式设置时发送。返回 (samplingParams, max_tokens)。
    """
    parsed = [
        _decode_value(key, env.get(key), default)
        for key, default in DECODE_DEFAULTS.items()
    ]
    parsed += [
        _decode_value(key, env.get(key)) for key in DECODE_OPTIONAL
    ]
    sampling = {param: value for param, value in parsed if value is not None}
    max_tokens = sampling.pop("max_tokens")
    return sampling, max_tokens


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

    sampling, max_tokens = decode_params(env)

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
                # 解码参数：env（DECODE_*）注入，缺省回落 DECODE_DEFAULTS；
                # samplingParams 原样直传请求体（含 vLLM 专属 frequency_penalty）
                "maxTokens": max_tokens,
                "samplingParams": sampling,
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
