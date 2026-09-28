"""tests/task 共享夹具：与 tests/hubkit/test_hubkit.py 中的 helper 等价。

独立成模块以便 tests/task 下的测试文件跨目录复用（避免依赖 tests/hubkit
的 sys.path 注入顺序）。
"""

from hubkit.schema import CSV_COLUMNS
from hubkit import render


def row(name="Seed"):
    return dict(zip(CSV_COLUMNS, [name, f"Single cell {name} paper", "2025", "arXiv",
        f"https://arxiv.org/abs/{name}", f"https://github.com/example/{name}", "1", "PyTorch", "MIT", "a" * 40]))


def readme(item, prefix=""):
    return ("# Hub\n![Models](https://img.shields.io/badge/Models-1-brightgreen)\n\n"
            + render.render_bullet(item, prefix)
            + "\n## 📊 Models\n\n| Model | Year | Venue | Framework | Repository |\n"
            + "| --- | --- | --- | --- | --- |\n" + render.render_table_row(item))
