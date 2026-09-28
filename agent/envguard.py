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
import re
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

# 外层配置键白名单（.env 中的其余键一律不透传），按用途归类：
CONFIG_KEYS = [
    # ── LLM endpoint ──
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "AGENT_LLM_MODEL",
    # ── 模型解码参数（assembly 渲染进 models.json）──
    # 核心参数：始终发送；未设置回落代码默认值
    "DECODE_TEMPERATURE",        # 0.0–2.0，默认 0.4
    "DECODE_TOP_P",              # 0.0–1.0，默认 0.95
    "DECODE_FREQUENCY_PENALTY",  # -2.0–2.0，默认 0.3（vLLM 专属字段直传）
    "DECODE_MAX_TOKENS",         # 64–200000，默认 16384（pi maxTokens）
    # 扩展参数：仅显式设置时才发送（未设置 = 端点默认）
    "DECODE_SEED",               # ≥0 整数；设置后同输入同输出（确定性复现）
    "DECODE_TOP_K",              # ≥-1 整数；-1 = 关闭截断
    "DECODE_MIN_P",              # 0.0–1.0；比 top_p 更稳的截断方式
    "DECODE_PRESENCE_PENALTY",   # -2.0–2.0；按是否出现过惩罚（治重复）
    "DECODE_REPETITION_PENALTY", # 0.1–2.0；乘性惩罚（治重复另一路径）
    # ── 网络 ──
    "ALL_PROXY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    # ── ingest 检索/取证 ──
    "GITHUB_TOKEN",              # gh api 认证（core 5000/h；匿名 60/h 不够 probe 用）
    "EMAIL",                     # Unpaywall API 必填参数（不发邮件、无需注册）
    # ── optimize 任务（NPU 验证子进程）──
    "NPU_PYTHON",
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
    """解析 .env（KEY=VALUE，支持注释与成对引号）。文件不存在返回空。

    注释规则：整行 `#` 开头忽略；非引号值的行内注释须以空白+`#` 起始
    （`K=v # 说明` → `v`；`K=a#b` → `a#b`，`#` 紧贴值时是值的一部分）。
    """
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
        val = re.sub(r"\s+#.*$", "", val).strip()  # 行内注释（空白+# 起）
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]  # 引号值内的 # 不受注释剥离影响（注释已先剥离）
        if key:
            values[key] = val
    return values


def build_snapshot(
    dotenv: dict[str, str],
    overrides: dict[str, str],
    agent_vars: dict[str, str],
    venv: Path | None = None,
) -> dict[str, str]:
    """构造白名单 env 快照。

    优先级：overrides（--set）> .env > 父进程环境。
    overrides 中的任务参数键（如 FOO）不在白名单也放行——这是任务注入通道。
    venv 非空时注入项目虚拟环境（等效 source activate，但对 pi 全部
    bash 子孙进程生效）：VIRTUAL_ENV 指向 venv，PATH 前置 venv/bin。
    NPU 双环境隔离不受影响——NPU_PYTHON 是绝对路径 subprocess，不经 PATH。
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
    if venv is not None:
        env["VIRTUAL_ENV"] = str(venv)
        env["PATH"] = str(venv / "bin") + os.pathsep + env.get("PATH", "")
    return env
