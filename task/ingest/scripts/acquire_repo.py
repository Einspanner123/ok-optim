#!/usr/bin/env python3
"""acquire_repo: 仓库快照三通道（契约「脚本职责要点」）。

    ① GitHub: shallow clone + git rev-parse HEAD（40 位）+ 剥 .git
       + 子模块递归实化（失败置 repo_needs_review，不静默跳过）
    ② HuggingFace: 经 hf-mirror.com 拉取文件清单，排除权重
    ③ PyPI sdist 兜底: pip download --no-deps --no-binary :all:（commit=unavailable）

排除规则：权重扩展名（.pt/.pth/.ckpt/.bin/.onnx/.safetensors/.h5ad 等）、
data/weights/checkpoints 目录、代码外 >2MB 单文件；.csv 仅在 >2MB 时排除
（GenePT 教训：小 csv 走 gitignore 例外块保留）。
exit: 0 成功 / 2 needs_human（repo_needs_review 等待人工确认）/ 3 fatal。
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _ingest import _stdio_json, candidate_dir, load_candidate_raw
from _net import http_get, load_dotenv
from hubkit.schema import CODE_EXTS

WEIGHT_EXTS = {".pt", ".pth", ".ckpt", ".bin", ".onnx", ".safetensors",
               ".h5ad", ".h5", ".npz", ".npy", ".loom"}
WEIGHT_DIRS = {"data", "weights", "checkpoints", "datasets", "figures"}
SIZE_LIMIT = 2 * 1024 * 1024


def _excluded(rel: Path, size: int) -> bool:
    ext = rel.suffix.lower()
    if ext in WEIGHT_EXTS:
        return True
    if any(part.lower() in WEIGHT_DIRS for part in rel.parts[:-1]):
        return True
    if ext == ".csv":
        return size > SIZE_LIMIT  # 小 csv 保留（GenePT 教训）
    if ext not in CODE_EXTS:
        return size > SIZE_LIMIT  # 代码外 >2MB 单文件排除
    return False


def _copy_filtered(src_root: Path, dst_root: Path) -> tuple[int, list[str]]:
    kept, dropped = 0, []
    for f in src_root.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(src_root)
        if _excluded(rel, f.stat().st_size):
            dropped.append(rel.as_posix())
            continue
        target = dst_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(f, target)
        kept += 1
    return kept, dropped


def _run_git(args: list[str], **kw) -> subprocess.CompletedProcess:
    """git 不支持应用层回退：先直连（剥离代理 env），失败再带代理重试一次。"""
    import os
    env = dict(os.environ)
    try:
        stripped = {k: v for k, v in env.items()
                    if k.lower() not in ("all_proxy", "http_proxy", "https_proxy")}
        return subprocess.run(args, env=stripped, check=True,
                              capture_output=True, timeout=600, **kw)
    except subprocess.CalledProcessError:
        if not any(k.lower() in ("all_proxy", "https_proxy") for k in env):
            raise
        return subprocess.run(args, env=env, check=True,
                              capture_output=True, timeout=600, **kw)


def _github(repo_url: str, repo_dir: Path) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="acquire-"))
    try:
        clone_dir = tmp / "clone"
        _run_git(["git", "clone", "--depth", "1", "--quiet", repo_url, str(clone_dir)])
        head = _run_git(["git", "-C", str(clone_dir),
                         "rev-parse", "HEAD"]).stdout.decode().strip()
        if not re.fullmatch(r"[0-9a-f]{40}", head):
            raise ValueError(f"commit 非法: {head!r}")
        needs_review = False
        if (clone_dir / ".gitmodules").is_file():
            r = _run_git(["git", "-C", str(clone_dir), "submodule", "update",
                          "--init", "--recursive"])
            needs_review = r.returncode != 0
        shutil.rmtree(clone_dir / ".git", ignore_errors=True)
        kept, dropped = _copy_filtered(clone_dir, repo_dir)
        return {"channel": "github", "commit": head, "files": kept,
                "dropped": dropped[:20], "submodules_needs_review": needs_review}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _huggingface(repo_url: str, repo_dir: Path) -> dict:
    m = re.search(r"huggingface\.co/([\w.-]+/[\w.-]+)", repo_url)
    if not m:
        raise ValueError(f"非 HF 仓库 URL: {repo_url!r}")
    repo_id = m.group(1)
    mirror = f"https://hf-mirror.com"
    api = http_get(f"{mirror}/api/models/{repo_id}")
    api.raise_for_status()
    siblings = [s["rfilename"] for s in api.json().get("siblings", [])]
    kept, dropped = 0, []
    for rel in siblings:
        rf = Path(rel)
        size = 0
        head = http_get(f"{mirror}/{repo_id}/resolve/main/{rel}")
        head.raise_for_status()
        data = head.content
        size = len(data)
        if _excluded(rf, size):
            dropped.append(rel)
            continue
        target = repo_dir / rf
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        kept += 1
    return {"channel": "huggingface", "commit": "unavailable", "files": kept,
            "dropped": dropped[:20], "mirror": mirror}


def _sdist(package: str, repo_dir: Path) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="sdist-"))
    try:
        subprocess.run(
            ["pip", "download", "--no-deps", "--no-binary", ":all:",
             "--no-build-isolation", "-d", str(tmp), package],
            check=True, capture_output=True, timeout=600)
        archive = next(tmp.glob("*.tar.gz")) if list(tmp.glob("*.tar.gz")) \
            else next(tmp.iterdir())
        extract = tmp / "x"
        shutil.unpack_archive(archive, extract)
        inner = next(p for p in extract.iterdir() if p.is_dir())
        kept, dropped = _copy_filtered(inner, repo_dir)
        return {"channel": "sdist", "commit": "unavailable", "files": kept,
                "dropped": dropped[:20], "package": package}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="acquire_repo: 仓库快照三通道")
    parser.add_argument("slug")
    parser.add_argument("repo_url", help="github / huggingface URL，或 pypi:<package>")
    parser.add_argument("--commit", default=None, help="指定 commit（可选）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    load_dotenv()

    try:
        load_candidate_raw(args.slug)
    except Exception as exc:
        print(f"needs_human: {exc}", file=sys.stderr)
        return 2
    repo_dir = candidate_dir(args.slug) / "repo"
    if repo_dir.exists():
        print("needs_human: repo/ 已存在，幂等保护——先人工清理再重跑", file=sys.stderr)
        return 2
    repo_dir.mkdir(parents=True)

    try:
        if args.repo_url.startswith("pypi:"):
            result = _sdist(args.repo_url[len("pypi:"):], repo_dir)
        elif "huggingface.co" in args.repo_url:
            result = _huggingface(args.repo_url, repo_dir)
        elif "github.com" in args.repo_url:
            result = _github(args.repo_url, repo_dir)
        else:
            print(f"fatal: 无法识别的仓库 URL: {args.repo_url!r}", file=sys.stderr)
            return 3
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(repo_dir, ignore_errors=True)
        print(f"needs_human: repo_needs_review（clone/子模块失败）: "
              f"{(exc.stderr or b'').decode()[:200]}", file=sys.stderr)
        return 2
    except Exception as exc:
        shutil.rmtree(repo_dir, ignore_errors=True)
        print(f"fatal: 获取失败: {exc}", file=sys.stderr)
        return 3

    if result.get("submodules_needs_review"):
        result["status"] = "repo_needs_review"
        _stdio_json(result, args.json)
        print("needs_human: 子模块递归实化失败，需人工确认子模块清单", file=sys.stderr)
        return 2
    result["status"] = "acquired"
    _stdio_json(result, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
