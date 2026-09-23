---
name: ingest
description: 论文检索入库（discover 发现 / audit 复核），按 single-cell-hub 契约产出条目。检索定位论文与官方仓库、校验并落位 single-cell-hub，产出人工 git 步骤验收单。
---

# ingest

## 目标

- **discover**：给定种子（`INGEST_SEED_URL`：doi:/arxiv:/URL/标题）或在
  `INGEST_YEAR_FROM/TO` × `INGEST_MAX_NEW` 边界内自主发现单细胞论文，
  产出符合 hub 契约的完整条目并落位（apply）。
- **audit**：扫描检索记录表与 hub 的一致性，对异常条目（哈希不匹配 /
  unavailable / 空列）重查修复；`INGEST_SEED_URL` 可指定单点复核。
- 终态有限集：条目 applied / ledger 四态（finalized+applied、none、unavailable、
  pending）之一；不产生半成品。

## 流程步骤

### discover

1. **resolve**：种子解析走 `scholar_lookup`（五形态分流）；自主发现按年份窗口
   用 `search_arxiv` / `web_search`（仅线索级）——候选必须先过 ② 才能 acquire。
   每确定一个候选先建 `.ingest/candidates/<slug>/candidate.json`。
2. **verify**：`github_search --probe` 采集官方性证据 → 确定性判定
   （official / author_maintained / likely）。`likely` → needs_human（见「何时问人」）。
3. **acquire**：`download_pdf`（多源链）→ **`extract_repo_links`**（PDF 全文挖
   仓库链接；检索不出 = 确定性 none，直接 `ledger_update --verdict none` 收尾，
   证据 "全文无仓库链接"）→ `github_search --probe` 定性候选链接 →
   `acquire_repo`（三通道）。
4. **format**：`format_entry <slug>` 渲染 staged 全套产物。
5. **validate**：`validate_entry <slug>` 七规则预检；不过则按报告回修后重跑
   （≤2 轮），仍败 → needs_human。
6. **apply**：`apply_entry <slug>`（脚本内自动预检；落位前见「何时问人」）→
   `ledger_update --verdict <official|author_maintained> --evidence <关键证据>` →
   向用户汇报验收单（产物清单 + 证据链 + 脚本打印的人工 git 步骤）。

### audit

1. `audit_scan`（无 key 全量 / 有 key 单点）→ 异常清单。
2. 对每个异常条目按 discover ②③ 重查（复用同一管线）。
3. 修复成功 → format/validate/apply + ledger_update；仍异常 → needs_human。
4. 正常条目只做确定性校验，**不跑 LLM 复核**。

## 工具用法

- bash 仅限白名单：`uv run python task/ingest/scripts/<脚本>.py` + 只读命令；
  write/edit 已被拒绝，一切产物写入经脚本
- 裁决点用 `ask_user(question, options)`；非交互时收到 NOT_INTERACTIVE 按
  「何时问人」的降级列执行，**不得猜测用户意图**
- 网络类脚本自带限流与缓存；失败看 stderr 的 exit code 语义（0/2/3）

## 边界与禁止事项

- **LLM 不得判定 `none`**：只有 `extract_repo_links` 报"全文无仓库链接"或
  repo 快照 0 个 .py（probe 通过）两种确定性证据可登记 none
- `likely` 官方性不得直接 format/apply——必须人工确认或转 pending
- hub 契约七条规则不许绕过：任何产物先 validate 全绿再 apply
- 台账（`.ingest/ledger.jsonl`）只经 `ledger_update` 写入；append-only
- 不执行任何 git commit/push/PR；apply 后只转述脚本打印的人工步骤

## 何时问人

| 决策点 | 交互模式 | 非交互（默认） |
|---|---|---|
| 官方性 `likely` | ask_user 确认或否决 | pending：`official_needs_review`，候选冻结 |
| 多候选仓库 | ask_user 列选项+各自证据 | pending：清单留待人工 |
| PDF 拿不到 | ask_user 给本地路径 | pending：`pdf_needs_manual` |
| 壳仓库/子模块 | ask_user 确认清单 | pending：`repo_needs_review` |
| apply 落位前 | ask_user 最终确认 | **不落位**：staged + 验收单留待人工 |
| audit 异常复核 | ask_user 逐条确认处置 | 重查后仍异常 → pending |

## 完成标准（Done 的定义）

- validate_entry 全绿（或 needs_human 状态明确且产物完整）
- report.md 完整：证据链表（probe 证据 / PDF availability 原文）、产物清单、
  人工 git 步骤
- ledger 一致：本次触碰的每篇论文四态之一（finalized+applied / none /
  unavailable / pending）
- hub 侧无半成品：staged 完整 / 已 apply / 明确 pending 三选一
