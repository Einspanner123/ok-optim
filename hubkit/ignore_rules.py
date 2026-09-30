"""gitignore 安全模拟：判定 repo 快照内代码文件是否会被忽略规则吞掉。

消费者：
    validators.check_ignore_safety —— 校验（存量守护）
    ingest stage_entry —— 例外块自动建议（apply 前判定）
"""

from __future__ import annotations

from pathlib import Path

import pathspec

from hubkit.schema import CODE_EXTS, CODE_SIZE_LIMIT, ENTRIES_ROOT, GITIGNORE


def load_ignore_spec(hub: Path) -> pathspec.GitIgnoreSpec | None:
    gi = hub / GITIGNORE
    if not gi.is_file():
        return None
    return pathspec.GitIgnoreSpec.from_lines(
        gi.read_text(encoding="utf-8").splitlines(),
    )


def is_code_file(path: Path) -> bool:
    """代码相关且 <2MB 的文件（契约规定必须可被 git 追踪）。"""
    return path.suffix.lower() in CODE_EXTS and path.stat().st_size < CODE_SIZE_LIMIT


def find_ignored_code_files(hub: Path, name: str,
                            spec: pathspec.GitIgnoreSpec) -> list[Path]:
    """返回 <Name>/repo/ 内会被忽略规则吞掉的代码相关文件（hub 根相对路径）。"""
    repo = hub / ENTRIES_ROOT / name / "repo"
    if not repo.is_dir():
        return []
    ignored: list[Path] = []
    for f in repo.rglob("*"):
        if not f.is_file() or not is_code_file(f):
            continue
        rel = f.relative_to(hub).as_posix()
        if spec.match_file(rel):
            ignored.append(f)
    return ignored


def snapshot_exception(hub: Path, name: str, snapshot: Path) -> str:
    """Use the same code-file predicate and ignore semantics for candidate snapshots."""
    from hubkit.schema import entry_path
    spec = load_ignore_spec(hub)
    if spec is None:
        return ""
    paths = []
    for file in sorted(snapshot.rglob("*")):
        if file.is_file() and is_code_file(file):
            relative = f"{entry_path(name)}/repo/{file.relative_to(snapshot).as_posix()}"
            if spec.match_file(relative):
                paths.append(relative)
    if not paths:
        return ""
    # Unignore ancestors as well: Git cannot re-include files under an excluded directory.
    lines = [f"# 例外：{name} 源码快照内的代码相关文件"]
    for relative in paths:
        for parent in reversed(Path(relative).parents):
            if parent.as_posix() != ".":
                line = "!" + parent.as_posix() + "/"
                if line not in lines:
                    lines.append(line)
        line = "!" + relative
        if line not in lines:
            lines.append(line)
    return "\n".join(lines)
