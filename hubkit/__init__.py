"""hubkit —— single-cell-hub 契约的可执行化身。

契约事实源: docs/architecture.md Part II「hub 契约」节。本包是其代码层实现：
schema（常量与模板）/ readers（读接口）/ validators（七条规则 + 孤儿检查）/
ignore_rules（gitignore 安全模拟）。

使用方：
    ingest（唯一写入方）  —— 校验自证 + format 渲染共用本包
    optimize（纯读方）    —— 经 readers 读取；读失败报错，不校验不回写

边界（architecture.md 任务权限矩阵）：
    本包只含 schema / 读 / 校验等只读能力；hub 落位写动作（apply_entry）
    仅存在于 task/ingest/scripts/。本包不 import agent/ 与 task/。
"""

from hubkit.readers import load_models_csv, parse_entries, parse_model_readme
from hubkit.validators import Report, validate
from hubkit.render import entry_files

__all__ = [
    "Report",
    "load_models_csv",
    "parse_entries",
    "parse_model_readme",
    "validate",
    "entry_files",
]
