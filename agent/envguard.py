"""envguard: 进程层沙盒。

从 .env 与父进程构造白名单环境快照传给 pi 子进程：
- 基础系统变量（PATH/HOME 等）最小透传
- 配置键（OPENAI_* 等）仅白名单内放行，.env 优先于父进程
- AGENT_* 运行时变量由 launcher 注入
脚本以只读方式消费 env；agent 在 bash 里 export 只影响自己的子进程，
污染不到这份快照。
"""

from __future__ import annotations

import os
from pathlib import Path

# 最小系统变量：pi / node / uv 运行所需
BASE_VARS = [
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TERM",
    "TMPDIR",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "npm_config_registry",
]

# 外层配置键白名单：.env 中的其余键一律不透传
CONFIG_KEYS = [
    # LLM endpoint
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "AGENT_LLM_MODEL",
    # optimize 任务用（NPU 验证子进程）
    "NPU_PYTHON",
    # 网络
    "ALL_PROXY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
]

# launcher 注入的运行时变量
AGENT_KEYS = [
    "AGENT_TASK",
    "AGENT_SLUG",
    "AGENT_RUN_DIR",
    "AGENT_INTERACTIVE",
    "PI_CODING_AGENT_DIR",
    "PI_OFFLINE",
]


def load_env_file(path: Path) -> dict[str, str]:
    """解析 .env（KEY=VALUE，支持注释与成对引号）。文件不存在返回空。"""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key:
            values[key] = val
    return values


def build_snapshot(
    dotenv: dict[str, str],
    overrides: dict[str, str],
    agent_vars: dict[str, str],
) -> dict[str, str]:
    """构造白名单 env 快照。

    优先级：overrides（--set）> .env > 父进程环境。
    overrides 中的任务参数键（如 FOO）不在白名单也放行——这是任务注入通道。
    """
    env: dict[str, str] = {}
    for key in BASE_VARS:
        if key in os.environ:
            env[key] = os.environ[key]
    for key in CONFIG_KEYS:
        # .env 优先于父进程（外层配置集中管理）
        if key in dotenv:
            env[key] = dotenv[key]
        elif key in os.environ:
            env[key] = os.environ[key]
    # --set 注入（任务参数 + 显式覆盖）
    env.update(overrides)
    # launcher 运行时变量
    env.update(agent_vars)
    return env
