"""hub 契约常量与模板（事实源: docs/architecture.md Part II「hub 契约」节）。

本模块只放契约数据；读取与校验逻辑见 readers / validators。
契约变更流程：改 architecture.md → 同步本模块 → validate_hub 全量回归。
"""

from __future__ import annotations

import re

# ---- models.csv 表格接口 ----

CSV_COLUMNS = [
    "model_name", "paper_title", "year", "venue", "paper_url",
    "repo_url", "github_stars", "framework", "license", "commit_hash",
]
KEY_COLUMNS = ["model_name", "repo_url", "commit_hash"]
YEAR_RE = re.compile(r"(19|20)\d{2}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
COMMIT_HASH_PLACEHOLDER = "unavailable"

# PDF 落地校验（目录格式节）
PDF_MAGIC = b"%PDF"
PDF_MIN_BYTES = 50 * 1024

# ---- repo 快照 / gitignore 安全 ----

# 代码相关扩展（<2MB 被忽略规则命中即需例外块）
# .txt: requirements.txt 等运行所需；.csv: GenePT 事故——input_data/gene_info_table.csv 被吞
CODE_EXTS = {
    ".py", ".pyi", ".r", ".sh", ".yaml", ".yml", ".toml", ".json",
    ".pkl", ".ipynb", ".cfg", ".ini", ".txt", ".csv",
}
CODE_SIZE_LIMIT = 2 * 1024 * 1024

# ---- 模型 README 措辞规则 ----

# repo 行措辞由官方性结论决定；HF 条目强制 Author-maintained
REPO_KIND_OFFICIAL = "Official"
REPO_KIND_AUTHOR = "Author-maintained"
STATUS_PDF_MARK = "PDF downloaded"
# repo 内无 .py 时 Status 必须说明原因（命中任一即视为已说明）
STATUS_NO_CODE_RE = re.compile(r"no (runnable )?code|no python|0 \.py|incomplete", re.I)

# ---- 双 README 徽章体系 ----

COUNT_BADGE = "badge/Models-{n}-brightgreen"

# venue → 短名与色值映射（新 venue 必须先在此登记，色值人工指定）
VENUE_STYLES: dict[str, dict[str, str]] = {
    "Nature": {"short": "Nature", "color": "D32F2F"},
    "Nature Methods": {"short": "Nature Methods", "color": "2E86AB"},
    "Nature Communications": {"short": "Nat Commun", "color": "2E86AB"},
    "Nature Machine Intelligence": {"short": "Nature Mach Intell", "color": "2E86AB"},
    "Cell Research": {"short": "Cell Research", "color": "007791"},
    "ICLR": {"short": "ICLR", "color": "4A154B"},
    "ICML": {"short": "ICML", "color": "6B4FBB"},
    "NeurIPS": {"short": "NeurIPS", "color": "3F51B5"},
    "NeurIPS Workshop": {"short": "NeurIPS W", "color": "3F51B5"},
    "bioRxiv": {"short": "bioRxiv", "color": "BD4089"},
    "arXiv": {"short": "arXiv", "color": "B31B1B"},
}

# ---- hub 顶层布局（相对 hub 根） ----

OUTER_README = "README.md"
INNER_README = "single_cell_models/README.md"
MODELS_CSV = "single_cell_models/models.csv"
ENTRIES_ROOT = "single_cell_models"
GITIGNORE = ".gitignore"

INDEX_FILES = (OUTER_README, INNER_README, MODELS_CSV, GITIGNORE)


def entry_name(name: str) -> str:
    """Names are a single directory component, never a caller-supplied path."""
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", name):
        raise ValueError(f"非法 model_name: {name!r}")
    return name


def entry_path(name: str) -> str:
    return f"{ENTRIES_ROOT}/{entry_name(name)}"
