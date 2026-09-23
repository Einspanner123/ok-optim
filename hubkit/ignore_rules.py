"""gitignore 安全模拟：判定 repo 快照内代码文件是否会被忽略规则吞掉。

消费者：
    validators.check_ignore_safety —— 校验（存量守护）
    ingest format_entry —— 例外块自动建议（apply 前判定）
"""

from __future__ import annotations

from pathlib import Path

import pathspec
import pathspec.patterns

from hubkit.schema import CODE_EXTS, CODE_SIZE_LIMIT, ENTRIES_ROOT, GITIGNORE


def load_ignore_spec(hub: Path) -> pathspec.GitIgnoreSpec | None:
    gi = hub / GITIGNORE
    if not gi.is_file():
        return None
    return pathspec.GitIgnoreSpec.from_lines(
        pathspec.patterns.GitWildMatchPattern,
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


def suggest_exception_block(name: str, ignored: list[Path]) -> str:
    """按契约「gitignore 例外模式」渲染例外块（一个模型一块）。"""
    lines = [
        f"# 例外：{name} 源码快照内的代码相关文件（gitignore 大类规则误伤，需可追踪）",
    ]
    for f in ignored:
        lines.append(f"!single_cell_models/{name}/repo/**/*{f.suffix}")
    return "\n".join(lines)
