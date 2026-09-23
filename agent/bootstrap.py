"""bootstrap: pi runtime 的一键安装（uv run ok setup）。

幂等。三步：
1. Node：vendor 下没有 pinned 版本则从 npmmirror 下载 tarball 解压（不依赖系统 node）
2. pi：npm ci --prefix agent/vendor（package-lock 锁定全树）
3. 校验：vendor pi --version 必须等于 PI_VERSION

只装进 agent/vendor/，不写全局、不碰 PATH。
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = REPO_ROOT / "agent" / "vendor"

NODE_VERSION = "v22.23.2"
PI_VERSION = "0.87.0"
NODE_MIRROR = f"https://registry.npmmirror.com/-/binary/node/{NODE_VERSION}"
# 供应链锁定：官方 SHASUMS256.txt（nodejs.org/dist 与 npmmirror 双源核对一致）
NODE_SHA256 = {
    "x64": "b294a556e639d64338823920e5866c21c02741742d2e1529ee1a225c1ec9252a",
    "arm64": "013b59cfd2819703a6f4a14ab891fc46fc2a4e3f5bcd92de3fb4929b43e35b30",
}


def _arch() -> str:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    raise SystemExit(f"不支持的架构: {machine}")


def node_dir() -> Path:
    return VENDOR_DIR / f"node-{NODE_VERSION}-linux-{_arch()}"


def node_bin() -> Path:
    return node_dir() / "bin" / "node"


def pi_entry() -> Path:
    return (
        VENDOR_DIR
        / "node_modules"
        / "@earendil-works"
        / "pi-coding-agent"
        / "dist"
        / "bundle"
        / "cli.js"
    )


def pi_runtime() -> tuple[str, str]:
    """返回 [node, cli.js]；未安装则给出可执行指引。"""
    if node_bin().is_file() and pi_entry().is_file():
        return str(node_bin()), str(pi_entry())
    raise SystemExit(
        "pi runtime 未安装。先执行: uv run ok setup"
    )


def ensure_node() -> Path:
    if node_bin().is_file():
        print(f"[setup] node {NODE_VERSION} 已存在: {node_dir().relative_to(REPO_ROOT)}")
        return node_bin()
    arch = _arch()
    url = f"{NODE_MIRROR}/node-{NODE_VERSION}-linux-{arch}.tar.gz"
    tarball = VENDOR_DIR / f"node-{NODE_VERSION}-linux-{arch}.tar.gz"
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[setup] 下载 node {NODE_VERSION} ({arch}) ...")
    urllib.request.urlretrieve(url, tarball)
    digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
    if digest != NODE_SHA256[arch]:
        tarball.unlink()
        raise SystemExit(
            f"node tarball SHA256 不符，拒绝安装！\n  期望: {NODE_SHA256[arch]}\n  实际: {digest}"
        )
    print(f"[setup] sha256 校验通过: {digest[:16]}...")
    print("[setup] 解压 ...")
    with tarfile.open(tarball) as tf:
        tf.extractall(VENDOR_DIR)  # noqa: S202 - 受控目录
    tarball.unlink()
    if not node_bin().is_file():
        raise SystemExit(f"node 解压异常，缺少 {node_bin()}")
    return node_bin()


def npm_ci(node: Path) -> None:
    npm_cli = node_dir() / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"
    has_lock = (VENDOR_DIR / "package-lock.json").is_file()
    cmd = [str(node), str(npm_cli), "ci" if has_lock else "install",
           "--ignore-scripts", "--no-audit", "--no-fund",
           "--registry", "https://registry.npmmirror.com"]
    print(f"[setup] npm {'ci' if has_lock else 'install（首次生成 package-lock.json）'} ...")
    env = dict(os.environ)
    env["PATH"] = f"{node_dir() / 'bin'}:{env.get('PATH', '')}"  # npm 子进程 shebang 需要
    subprocess.run(cmd, cwd=str(VENDOR_DIR), check=True, env=env)


def verify(node: Path) -> None:
    if not pi_entry().is_file():
        raise SystemExit(f"pi 未装上，缺少 {pi_entry()}")
    result = subprocess.run(
        [str(node), str(pi_entry()), "--version"],
        capture_output=True, text=True, check=False,
    )
    version = result.stdout.strip()
    if version != PI_VERSION:
        raise SystemExit(f"pi 版本不符: 得到 {version!r}，期望 {PI_VERSION!r}")
    print(f"[setup] pi {version} 校验通过")


def main() -> None:
    if shutil.which("pi"):
        print(
            "[setup] 警告: 系统 PATH 上存在全局 pi，项目不使用它"
            "（建议 npm uninstall -g @earendil-works/pi-coding-agent 拆除）"
        )
    node = ensure_node()
    npm_ci(node)
    verify(node)
    print("[setup] 完成。运行任务: uv run ok run --task <task>")


if __name__ == "__main__":
    main()
