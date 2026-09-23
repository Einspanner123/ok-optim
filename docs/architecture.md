# ok-optim-agent 架构设计（单一主线文档）

> 本文是项目**唯一**设计文档，分三部分：**Part I 系统架构**、**Part II 抽象层**
> （任务通用契约 / hub 契约 / 检索接口 / 验证分级——架构定义的接口、契约与模板）、
> **Part III 下游任务实例化**（ingest / optimize 只写按抽象落地的差异）。
> 规则：改抽象必须同步下游实现；下游实现不得违背抽象。本文与代码不一致时，
> 以实现即事实修正本文。

# Part I 系统架构

## Context（背景）

项目目标：围绕 `single-cell-hub`（20 个单细胞基础模型标准化快照）做两件事——

1. **ingest**：检索论文与官方仓库，按 hub 契约下载入库（人工 commit/PR）
2. **optimize**：遍历或指定仓库做推理侧昇腾 NPU 适配优化与分级验证

架构把 agent 框架层交给 **pi**（agent harness），项目自己只写两样东西：
**启动器**（`agent/`）与**任务能力**（`task/` = SKILL.md + 确定性 py 脚本）。

### 决策记录（2026-09-22）

| 决策点 | 选择 | 理由 |
|---|---|---|
| Agent runtime | **pi**（pi.dev，`@earendil-works/pi-coding-agent`） | 极简可控；OpenAI 兼容 endpoint 一等支持（models.json）；扩展用 `--skill`/`-e` 显式装配（不用 `.pi/` 项目目录）；`--mode json`/RPC 便于启动器接管；session 单文件可回放 |
| 任务参数注入 | **混合分工** | 启动参数经 env/CLI 注入（确定性、可批处理）；运行中例外升级用 `ask_user` 交互工具；`--no-interactive` / batch 模式降级为 needs_human 记录 |
| task 可移植性 | **SKILL.md 开放标准**（agentskills.io） | `task/` 设计框架无关；pi 若未来不满足（或 dsh 成熟），harness 可替换，task 层平移 |
| git 提交 | **人工执行**（延续） | agent 只准备产物 + 校验 + 验收单 |
| 沙盒 | 启动器 env 白名单 + pi extension 路径防护 + 脚本层校验 三层 | 见「沙盒」节 |
| 文档形态 | **单文档主线**（本文） | ingest/optimize 共享 hub 契约，抽象集中定义避免偏移；下游只写实例化差异 |

### 候选框架对比结论（为什么不是它们）

- **claude code**：闭源 binary、绑 Anthropic 生态，接第三方 OpenAI 兼容 endpoint 属灰色地带，轨迹格式不可控
- **hermes**（Nous Research）：常驻型自主 agent（持久记忆、自动技能创建、消息平台 gateway），与本项目"确定性、可审计、任务型"诉求相悖
- **dsh**（DeepSeek Harness）：Trajectory 审计最强、Docker 沙盒内置，但官方声明开发者预览期有破坏性变更，插件开发用 TS 门槛高——**作为备选持续关注**，SKILL.md 标准保证平移可行

## 目录结构

```text
ok-optim-agent/
├── main.py                        # 入口：from agent.launcher import main
├── pyproject.toml                 # deps: httpx/pathspec；不依赖 openai——LLM 调用全在 pi 内
├── .env                           # 外层配置（gitignore；模板 .env.example）
├── AGENTS.md                      # pi 项目指令：全局边界（hub 只读、脚本工具优先、禁止事项）
├── agent/                         # ══ 启动器层：无业务逻辑，不发起任何 LLM 调用 ══
│   ├── launcher.py                # ok CLI：run / batch / pending / status / setup
│   ├── bootstrap.py               # ok setup 实现：vendor node 下载（SHA256 锁定）+ npm ci + 校验
│   ├── envguard.py                # 读 .env → 构造白名单 env 快照 → 传给 pi 子进程
│   ├── assembly.py                # 装配：preflight、models.json 渲染、bootstrap-guard 复制
│   ├── journal.py                 # 归档：pi session 链接、llm_usage 汇总、pending 登记
│   ├── extensions/                # pi 扩展源文件（launcher 用 -e 显式加载；guard 另复制进 runtime）
│   │   ├── path-guard.ts          # 工具调用拦截：写路径白名单、bash 命令白名单（见「工具面」）
│   │   ├── ask-user.ts            # 注册 ask_user 工具（ctx.ui select/confirm/input）
│   │   └── bootstrap-guard.ts     # 禁直跑守卫：缺 launcher 令牌的会话立即终止
│   ├── vendor/                    # pi runtime 本体（gitignore，`ok setup` 生成）；
│   │   ├── package.json(+lock)    #   ← 例外：这两个入库，pin pi 0.87.0
│   │   ├── node-v22.23.2-*/       #   vendor Node（tarball 解压，SHA256 校验）
│   │   └── node_modules/          #   pi 及依赖
│   └── runtime/                   # PI_CODING_AGENT_DIR（gitignore）：
│       ├── models.json            #   assembly 每次启动渲染
│       └── extensions/bootstrap-guard.ts  # 全局扩展位，必加载
├── task/                          # ══ 任务层：按 Part II 抽象实例化 ══
│   ├── ingest/                    # 任务①（hub 唯一写入方）→ 实例化见 Part III
│   └── optimize/                  # 任务②（hub 纯读方）→ 实例化见 Part III
├── docs/
│   └── architecture.md            # 本文档（单一主线）
├── runs/<ts>/<task>/<slug>/       # 每次运行工作区：journal、产物、state.json（gitignore）
└── single-cell-hub/               # 子模块数据集：只读；唯一写入口是 task 脚本的 apply 类操作
```

`task/<name>/` 标准结构（Part II「任务通用契约」定义）：

```text
task/<name>/
├── skill.yaml         # 元数据：name / description / required_env / optional_env / scripts 清单（含参数 schema）
├── SKILL.md           # skill 设计（Agent Skills 标准格式）：流程、工具用法、边界、何时问人、完成标准
├── docs/              # 任务专属知识（语料笔记等）
└── scripts/           # 确定性 py 脚本，可独立执行与单测
```

## ok CLI（唯一入口）

```bash
uv sync && uv run ok setup    # 全新 clone：两步完成环境准备
uv run ok setup               # 安装/修复 pi runtime（幂等）
uv run ok run --task <t> [--set K=V ...] [--no-interactive]   # 单次任务（run 可省略）
uv run ok status              # 历史运行记录（journal 汇总）
uv run ok batch ...           # 批量（M4）
uv run ok pending             # 待人工项（M4）
```

- 入口 `ok`（pyproject `[project.scripts]`）；简写规则：首参数不是子命令时按 `run` 处理
- `setup` 三步：vendor 无 Node 则从 npmmirror 下载 pinned tarball（**SHA256 锁定**，
  双源核对）→ `npm ci`（package-lock 全树锁定）→ `pi --version` 校验；不写全局、不碰 PATH
- 禁直跑：PATH 上无 pi；vendor 直跑缺 `AGENT_INVOKED_BY_LAUNCHER` 令牌会被
  bootstrap-guard 终止——LLM 调用必然经 launcher 审计

## 运行时集成（pi）

### 安装与版本

- pi 精确 pin 在 `agent/vendor/package.json` + `package-lock.json`（入库），本体装在
  `agent/vendor/`（gitignore，`ok setup` 生成）；版本升级 = 改版本号 + 重跑 `ok setup`
- **禁直跑**：launcher 每次拉起注入一次性令牌 `AGENT_INVOKED_BY_LAUNCHER`；
  `bootstrap-guard.ts`（assembly 复制到 runtime/extensions/，必加载）发现缺令牌即
  `process.exit(1)`。防误用不防对抗（能手注令牌的人拦不住；硬隔离走容器/专用 OS 用户）

### Provider 与 LLM 参数

- `models.json` 声明自定义 provider（`api: "openai-completions"`，`baseUrl`/`apiKey` 从 env 读取）
- `.env`：`OPENAI_BASE_URL` / `OPENAI_API_KEY` / `AGENT_LLM_MODEL`
- 解码参数冻结：temperature 0 / top_p 1（seed 42 若 provider 透传则配置）——同事流水线既有口径

### Skills 装配

- 不用 `.pi/` 项目目录（pi 相关文件全部收在 `agent/` 下）：launcher 以
  `--skill task/<name>` 显式加载任务目录（SKILL.md 就在任务目录内）
- SKILL.md 遵循 Agent Skills 标准（YAML front-matter `name`/`description` + 正文）
- pi 按需渐进加载 skill（progressive disclosure），不破坏 prompt cache——批处理成本友好

### 运行模式

| 场景 | 模式 | 说明 |
|---|---|---|
| 人工值守调试 | launcher 交互模式（TUI） | `uv run ok run --task <t>`（TTY 自动进交互）；pi 由 launcher 显式装配（`--skill` + `-e`），ask_user 可用 |
| 启动器单次任务 | `pi -p --mode json` | launcher 拉起，逐事件流式转发渲染到 stdout（思考 + 工具调用 + 结果） |
| 批处理 | launcher 循环逐模型拉起 | 每模型独立 session（`--name <task>/<slug>`），读 state 跳过已完成 |

审计：pi session 由 launcher 以 `--session-dir` 定向到 run 目录（append-only，含
reasoning/tool calls 全轨迹）；`journal.py` 运行结束把 session 路径、usage 汇总、
产物清单归档进 `runs/<ts>/`。**禁用 `--no-session`**。

## 工具面（LLM 可见，经 path-guard 收紧）

pi 默认给模型四个工具（read/write/edit/bash）+ 我们的扩展。全部经 `path-guard.ts`
拦截收紧后，等效于"run_skill_script 唯一执行通道"的语义：

| 工具 | 策略 |
|---|---|
| `bash` | **命令白名单**：仅允许 `uv run python task/<当前task>/scripts/<已声明脚本>.py <args>` 形态 + `ls`/`cat`/`git log`/`git status`/`git diff` 等只读命令；其余拒绝 |
| `write` / `edit` | **默认拒绝**——"LLM 不直接写盘"硬约束：一切产物写入只能经 task 脚本（脚本内部有自己的路径白名单） |
| `read` | 路径白名单内可读：`task/`、`runs/`、`AGENTS.md`、`single-cell-hub/`（只读语义） |
| `ask_user(question, options)` | `ask-user.ts` 注册；仅交互模式可用 |
| slash `/skill:<name>` | pi 原生技能入口，SKILL.md 即任务说明书 |

## 沙盒：三层防护

机制澄清：agent 在 bash 里 `export` 只影响它自己的子进程，**天然污染不到启动器注入的配置**。
真正要防的是持久化篡改与越权写盘：

1. **envguard（进程层）**：launcher 从 `.env` 构造白名单 env 快照传给 pi 子进程；
   `assembly.py` preflight 校验 `skill.yaml` 的 `required_env`，缺失即拒绝启动并打印注入清单。
   脚本以只读方式消费 env。
2. **path-guard（harness 层）**：pi extension 拦截工具调用，`.env`、`.env.example`、
   `agent/`、`task/`、`AGENTS.md`、`main.py` 全部只读；`single-cell-hub/` 仅允许
   经 apply 类脚本写入（bash 白名单天然保证——直接 write/edit 已被拒）。
3. **脚本层（最后防线）**：task 脚本内部对写入目标做路径前缀校验；argparse 严格模式；
   幂等设计（同参数重跑安全）。

容器隔离（可选，pi 支持 Gondolin/Docker/OpenShell）：ingest 任务可容器跑；
**optimize 不容器化**——NPU 设备透传复杂，host 跑 + 上述路径防护。

## 任务权限矩阵（共享工具的边界）

ingest 与 optimize 面向同一 hub，但能力面截然不同：**optimize 是零网络任务**
（seed 是本地模型名，repo 快照已在 hub，缺权重的验证直接 skip 而非下载）；
ingest 是重网络任务 + hub 唯一写入方。四层落地：声明（skill.yaml）→ 装配
（preflight）→ 拦截（path-guard 按 `AGENT_TASK` 限定各自 scripts）→ 脚本层
（hub 写函数只存在于 ingest 侧 apply_entry）。

| 资源 / 能力 | ingest | optimize |
|---|---|---|
| hub 读（模型目录/CSV/双 README） | ✅ | ✅ |
| hub 写 | ✅ 仅 `apply_entry`（校验全绿 + confirm 前置） | ❌ 产物留 runs/，人工审后取用 |
| 检索（Semantic Scholar/arXiv/DuckDuckGo/期刊页） | ✅ | ❌ 无需求 |
| gh api / git clone | ✅ 克隆到候选区 | ❌ 源码已在 hub，禁重复克隆 |
| 大文件下载 | PDF/repo → 候选工作区 | ❌（L2 缺权重 → skip） |
| NPU 子进程（NPU_PYTHON） | ❌ | ✅ 仅 validate_cpu/npu |
| runs/ 写 | ✅ 自己 slug 目录 | ✅ 自己 slug 目录 |
| 环境变量命名空间 | `INGEST_*` | `OPTIMIZE_*` + `NPU_PYTHON` |

共享面收敛为本文「hub 契约」一节：写入方（ingest）按它实现并自证合规（校验器归
ingest 侧），纯读方（optimize）按它读取、读错即报，不需要也不持有校验代码。

## 交互模型（混合分工）

- **启动时注入（确定性）**：
  - `uv run ok run --task ingest --set INGEST_SEED_URL=arxiv:xxxx [--no-interactive]`
  - `uv run ok run --task optimize --set OPTIMIZE_MODEL_NAME=UCE`
  - `--set K=V` 进 env 快照；launcher 组装首条任务指令（任务名 + 参数说明 + 指向 SKILL.md）注入 pi
- **运行中升级（ask_user）**：agent 无法从 env 得知的裁决点——官方性存疑确认、
  多候选仓库二选一、PDF/repo 人工兜底、apply 前确认
- **无人值守降级**：`--no-interactive` 或 batch 模式下 ask_user 返回 `NOT_INTERACTIVE`，
  agent 按各任务 SKILL.md 约定改走 needs_human 分支（写 `pending.json` + 安全收尾），
  事后 `uv run ok pending` 列出待人工项，处理后重跑
- 默认值：`run` 且 stdout 为 TTY → 交互模式；`batch` → 恒为 no-interactive

---

# Part II 抽象层（架构定义的契约、接口与模板）

## 任务通用契约

所有任务（含未来 task/discover）共用：

- **skill.yaml**：`name` / `description` / `required_env` / `optional_env` /
  `scripts`（脚本名 → 参数 JSON Schema，供 path-guard 校验 bash 调用与 argparse 对齐）
- **SKILL.md**（Agent Skills 标准）：目标 → 流程步骤 → 工具用法 → 边界与禁止事项 →
  何时问人 → 完成标准（Done 的定义）
- **scripts 规范**：
  - argparse CLI；`--json` 输出机器可读结果
  - exit code 语义：`0` 成功 / `2` needs_human（附原因）/ `3` fatal
  - 不 import agent/ 内核、不跨任务 import——可独立执行与单测
  - 网络类脚本内建限流与缓存（各通道额度见「检索接口抽象」）
  - stdout 预算：超长输出截断（摘要 ≤4k chars），全文落 `runs/` 供 read 追查
- **工作区**：`runs/<ts>/<task>/<slug>/`（state.json 断点续跑 + 产物目录）；
  任务自定义工作区（如 ingest 候选区 `.ingest/candidates/<slug>/`）必须 gitignore

## hub 契约（格式事实源）

> 本节是 `single-cell-hub` 的**唯一格式事实源**：ingest（写入方）按它生成产物并
> 自证合规，optimize（纯读方）按它读取。契约变更：人工编辑本节 → 两侧实现各自
> 跟进 → `validate_hub.py --hub` 全量回归。

### 仓库顶层布局

```text
single-cell-hub/
├── README.md                    # 对外门面（外层 README）
├── .gitignore                   # 忽略规则 + 例外块（见下）
├── dl.py                        # 历史下载脚本（遗留，契约不约束）
└── single_cell_models/          # 模型条目根目录
    ├── README.md                # 内层 README（与外层镜像，见「双 README 同步」）
    ├── models.csv               # 元数据表（见「表格接口」）
    └── <Name>/                  # 每模型一个目录（见「目录格式」）
```

### Entity：模型条目

一个模型条目 = **四位一体**，缺一不可：

1. 目录 `single_cell_models/<Name>/`（三件套）
2. `models.csv` 中恰好一行
3. 外层 README 一条 bullet
4. 对比表一行

`<Name>` 规则：与 CSV `model_name` 完全一致；大小写敏感（`AIDO.Cell`、
`Cell2Sentence`）；允许 `.`、`-`，不允许空格与路径分隔符。

### 表格接口（models.csv）

UTF-8、带表头、QUOTE_MINIMAL、逗号分隔、`\n` 换行。**10 列，列名与顺序固定**：

| # | 列名 | 类型 | 约束 |
|---|---|---|---|
| 1 | `model_name` | str | 非空；全表唯一；与目录名一致 |
| 2 | `paper_title` | str | 非空；论文官方标题 |
| 3 | `year` | int | 2019–2030；四位数 |
| 4 | `venue` | str | 非空；与 README 徽章 venue 词一致（见徽章映射表） |
| 5 | `paper_url` | url | 非空；http(s) |
| 6 | `repo_url` | url | 非空；http(s)；GitHub 或 HF URL |
| 7 | `github_stars` | int | **可空**（HF 条目或未标注时留空）；GitHub 条目为抓取时数值 |
| 8 | `framework` | str | 非空；`PyTorch` / `MindSpore` / `PyTorch/Hugging Face` 形态 |
| 9 | `license` | str | 非空；以 repo 快照内 LICENSE 实际文本为准（PyPI 兜底通道：以包内 LICENSE 为准） |
| 10 | `commit_hash` | str | **裸值**：恰好 40 位 hex，或字面 `unavailable`；无括号注解、无前缀 |

**键约束**：`model_name`、`repo_url`、`commit_hash` 三键全表无重复。

### 目录格式

```text
single_cell_models/<Name>/
├── README.md                    # 模型 README：标题 + 固定六行 bullet（见下）
├── paper/
│   └── <Name>.pdf               # 论文 PDF：%PDF magic 开头且 ≥50KB
└── repo/                        # 官方仓库快照：无 .git；至少 1 个 .py（无码须 Status 说明）
```

模型 README 六行模板（以 UCE 实例为准，逐字格式）：

```markdown
# <Name>

- Paper: [<paper_title>](<paper_url>) (<venue>, <year>)
- Paper PDF: `paper/<Name>.pdf`
- Official repository: <repo_url>
- Verification: <成句说明；必须为完整句子，含官方性依据与 license 结论>
- Framework/license/commit: <framework> / <license> / `<commit_hash>`
- Status: PDF downloaded; <仓库获取方式说明>
```

措辞与形态规则（经 20 模型存量校准）：

- **repo 行措辞由官方性结论决定**：`official` → "Official repository"；
  `author_maintained` → "Author-maintained repository"（HF 条目强制后者，
  Geneformer 先例）。允许变体 "Official code repository"（AIDO.Cell 实例）
- **Framework/license 行允许两形态**：
  - 形态 a：`Framework/license/commit: <fw> / <lic> / \`<40hex>\``
  - 形态 b：`Framework/license: <fw> / <lic>.` —— commit 以 40hex 形式融入
    Status 叙述（scPRINT/Geneformer 实例）；CSV `commit_hash=unavailable` 时无此要求
- framework/license 在 README 中是**自由文本描述**（如 "Python/PyTorch utilities"、
  "not declared in repository root"），与 CSV 规范值做语义比对（忽略大小写的包含
  关系），不要求逐字一致
- `Status` 必须含字面 **"PDF downloaded"**；repo 内无 `.py` 时必须写明原因
  （如 "no runnable code"）
- `Verification` 须为含主谓的完整句子，说明官方性判定依据与 license 结论

repo 快照排除规则（acquire 时执行）：排除模型权重与大数据（按扩展名
`*.pt/*.pth/*.ckpt/*.bin/*.onnx/*.safetensors/*.h5ad/*.csv…` + 目录名
`data/weights/checkpoints/`）；代码外单文件 >2MB；`notebooks/figures/data` 目录默认
排除但**必须列入报告**。被排除项中若含 gitignore 会吞掉的代码相关文件，走例外块捞回。

### 双 README 同步

**徽章体系**（shields.io，固定色值映射）：

| 徽章 | 图式 | 色值 |
|---|---|---|
| 条目数 | `badge/Models-<N>-brightgreen` | brightgreen；**N = CSV 行数** |
| 📄 venue | `badge/📄-<venue短名>%20<year>-<色>` | 见映射表 |
| code（GitHub 条目） | `badge/code-2ea44f?logo=github` | 2ea44f；**HF 条目无此徽章** |
| 🤗 Model（HF 条目） | `badge/🤗-Model-FFD21E` | FFD21E；链接 HF repo |
| Stars（GitHub 条目且 stars 非空） | `github/stars/<owner>/<repo>?style=flat&label=Stars` | 动态徽章；stars 为空则整枚省略 |
| Local | `badge/folder-📁-f0f0f0` | 链接 `./<前缀><Name>/` |

venue → 短名与色值映射（**新 venue 必须先在此登记**，色值人工指定）：

| venue | 短名 | 色值 |
|---|---|---|
| Nature | Nature | D32F2F |
| Nature Methods / Nature Communications / Nature Machine Intelligence | Nature Methods / Nat Commun / Nature Mach Intell | 2E86AB |
| Cell Research | Cell Research | 007791 |
| ICLR | ICLR | 4A154B |
| ICML | ICML | 6B4FBB |
| NeurIPS / NeurIPS Workshop | NeurIPS / NeurIPS W | 3F51B5 |
| bioRxiv | bioRxiv | BD4089 |
| arXiv | arXiv | B31B1B |

**bullet 条目模板**（外层 README）：`* **(<Name>) <paper_title>**` + 徽章行
（顺序：📄 → code/🤗 → Stars → Local；📄 → paper_url，code → repo_url，Local → `./<Name>/`）。

**对比表行模板**（两 README 各含一张，列 `| Model | Year | Venue | Framework | Repository Link |`）：
`| **<Name>** | <year> | <venue 短名> | <framework 短名> | [<owner/repo>](<repo_url>) |`
—— venue 用短名（Nature Commun.、Nature Mach Intell.、NeurIPS W）；framework 短名
（`PyTorch / HF`）；GitHub 条目链接文本 `owner/repo`，HF 条目用域名显示。

**两 README 的差异（仅两处，其余逐字节一致）**：

| 项 | 外层 `README.md` | 内层 `single_cell_models/README.md` |
|---|---|---|
| Local 徽章前缀 | `./single_cell_models/<Name>/` | `./<Name>/` |
| footer CSV 链接 | `` [`models.csv`](./single_cell_models/models.csv) `` | `` [`models.csv`](./models.csv) `` |

> 已知偏差：现存外层 footer 链接为 `./single_cell_models/models.csv/`（末尾多 `/`）。
> 契约规定为无尾斜杠形式；存量修正走人工 commit。

### gitignore 例外模式

hub 根 `.gitignore` 按扩展名大类忽略（csv/h5ad/权重/压缩包/notebook 等）。repo 快照中
**代码相关且 <2MB** 的文件若被规则吞掉，必须追加例外块：

```gitignore
# 例外：<Name> 源码快照内的<说明>（<原因>）
!single_cell_models/<Name>/repo/**/*.<ext>
```

- 判定：apply 前用 pathspec 按忽略规则模拟快照全部文件；代码相关扩展
  （`.py .pyi .r .sh .yaml .yml .toml .json .pkl .ipynb` 等）被命中即需例外
- 例外块追加在 `.gitignore` 末尾"例外"区，一个模型一块，注释注明模型名与原因
- 存量先例：Geneformer `*.pkl/*.ipynb`、scPRINT `*.ipynb`（GenePT 事故：代码文件
  被吞且无人察觉——本规则为直接防线）

### 七条契约校验规则（实现：task/ingest/scripts/validate_hub.py）

| 规则名 | 内容 | 失败处置 |
|---|---|---|
| 目录三件套（structure） | 目录三件套齐全；PDF 以 `%PDF` 开头且 ≥50KB | error |
| README 一致性（readme_csv） | 模型 README 六行与 CSV 行字段一致（含措辞规则） | error |
| 表格合法性（csv_schema） | CSV 10 列名序正确；三键唯一；`commit_hash` 裸值合法 | error |
| 双 README 镜像（readme_mirror） | 双 README 镜像一致（两处差异之外逐字节相同）；条目数徽章 = CSV 行数；bullet/对比表行与 CSV 一一对应 | error |
| 代码存在性（code_presence） | `repo/` 内 `.py` ≥1；为 0 时模型 README `Status` 必须说明原因 | error / 条件通过 |
| gitignore 安全（ignore_safety） | repo 内代码相关 <2MB 文件不被 gitignore 忽略（含例外块核对） | error |
| 关键行措辞（wording） | `Verification` 为完整句子；`Status` 含 "PDF downloaded" | error |

`--hub` 全量体检 = 七条规则 × 全部条目 + 孤儿检查（目录无 CSV 行、CSV 行无目录、
双 README 有条目无目录）。

### 读者与写者

| 角色 | 权限 | 实现 |
|---|---|---|
| ingest | 唯一写入方（apply_entry：新模型目录 + CSV 行 + 双 README + gitignore 例外） | `task/ingest/scripts/`，按本节实现并自证合规 |
| optimize | 纯读方 | `task/optimize/scripts/`，按本节读取；读失败报错，不校验不回写 |
| 人工 | 契约变更 + git commit | 修改本节 → 通知两侧跟进 → 全量回归 |

## 检索接口抽象（网络型任务共用）

**线索与证据两级分离**（防检索噪声污染结论的核心机制）：

```
第一级：线索（leads）——广、可以脏
    web_search 返回 URL+摘要，agent 挑候选
    ↓ 候选进入取证
第二级：证据（evidence）——窄、必须权威
    官方性判定只认结构化数据（Semantic Scholar / arXiv / 论文页 / gh api）
    web_search 结果永不作为判定依据
```

**网络脚本模板**（所有网络类脚本遵守）：

| 通道 | 限流 | 韧性 |
|---|---|---|
| Semantic Scholar Graph API | 1req/s（匿名档） | 缓存；不够加免费 key |
| arXiv Atom API | ≥3s（官方政策） | 缓存 |
| DuckDuckGo（ddgs） | 3s 间隔 + 退避 | 失败返回 `web_search_unavailable`，降级 gh api；**仅线索级** |
| gh api（已认证） | 滑动窗口 30req/min | 探针化（readme 互认 / tree 计数） |
| 任意网页（httpx + Mozilla UA） | 1req/2s/域名 | 每 slug 缓存，agent 读缓存解析 |

**种子分流接口**：`scholar_lookup` 接受任意种子形态——`arxiv:ID` / `doi:...` /
`pmid:...` / URL（S2 匹配失败落 fetch_page）/ 纯标题（S2 title search）。

**官方性判定规则**（固化在 `github_search --probe`，非 LLM 判定）：

| 证据 | 采集方式 | 权重 |
|---|---|---|
| 论文 Code Availability 声明 | `fetch_page(paper_url, want="code_availability")` | 强 |
| repo README 互认（引用论文标题/arXiv id） | `github_search` readme 通道 | 强 |
| 作者匹配（论文作者 ↔ owner login/用户名） | scholar_lookup 作者 + gh api users | 强 |
| 元数据旁证（description/创建日期/非 fork） | gh api | 弱 |

```
CodeAvailability ∧ README互认 或 README互认 ∧ 作者匹配 → official
仅作者匹配 → author_maintained
单项强 → likely（强制 ask_user / pending，不得静默入库）
```

**特殊通道内嵌验证**（历史教训固化进脚本）：HF URL → HF model card 验证；
`.py==0 && .gitmodules 存在` → `shell_repo` 列子模块要求人工确认（AIDO.Cell 教训）；
README 指向 PyPI 而 GitHub 空壳 → sdist 兜底、license 以包内 LICENSE 为准
（scPRINT 教训）。

**PDF 获取多源链模板**：S2 openAccessPdf → arXiv /pdf/ → citation_pdf_url →
Unpaywall → `pdf_needs_manual`；落地校验 %PDF magic + ≥50KB。

## 验证分级抽象（NPU 适配类任务共用）

**双 Python 环境分离（硬约束）**：

- agent 主进程（pi + task 脚本）：项目 .venv（3.12），不 import torch
- 验证子进程：`NPU_PYTHON=/work/nvidia_ascend/.venv/bin/python`（3.10 + torch_npu），
  由验证脚本 subprocess 拉起
- 生成代码 ≤3.10 语法（SKILL.md 指令 + 静态校验按 3.10 执行）

**分级验证**（彼此正交，缺基础设施自动 skip 带原因）：

| 级别 | 内容 | 执行者 |
|---|---|---|
| L0 静态 | py_compile 全部 + AST import 白名单 + diff 哨兵（永远跑） | validate_static（主进程） |
| L1 CPU 导入 | 仅 import entry_points + model_def；hard deps 用 sys.modules stub 注入 | validate_cpu（NPU_PYTHON 子进程） |
| L2 NPU smoke | 能力探针通过才跑：小配置实例化 → 单次 forward → `.npu()`；需预训练权重的 → skipped 不算失败 | validate_npu（NPU_PYTHON 子进程） |
| L3 KernelGYM | 算子级评测（默认关，显式启用） | kernel_gym（HTTP） |

状态判定：L0/L1 fail → `failed`；L2 skip → `done_with_warnings`。

**状态机模板**：`pending → scanned → analyzed → planned → transformed → validated → done`；
`skipped_incomplete` 为 preflight 终态；阶段重试耗尽 → `failed`（记 failing_stage）。

**防幻觉护栏模板**（LLM 改写强制项，静态校验执行）：

1. `ast.parse` 强制通过（改写产物必须是合法 Python）
2. import 集合 ⊆ 原文件 ∪ 白名单（torch_npu 等）
3. diff 规模哨兵：非目标文件 >50% 行变更 → 拒绝，回修 1 次仍败 → 保留确定性结果标 `degraded`

**上下文预算模板**（agent 读码/生成预算，写入 SKILL.md）：选文件 ≤4k；逐文件读
≤12k/文件；合成 model_card ≤16k in / ≤4k out；计划生成 ≤24k in / ≤6k out；单文件
改写 ≤14k in / ≤8k out。不整仓喂入；AST 按 class/function 边界切块，相关性预筛。

---

# Part III 下游任务实例化

## task/ingest（hub 唯一写入方）

目标：给定种子（论文 URL / arXiv ID / DOI / 仓库 URL），产出符合 hub 契约的完整
入库产物，校验全绿后给出**人工 commit/PR 步骤清单**。agent 不执行 git 提交。

### skill.yaml（10 脚本）

```yaml
name: ingest
description: 检索论文与官方仓库，按 single-cell-hub 契约下载入库
required_env: [INGEST_SEED_URL]
optional_env: [INGEST_MODEL_NAME, INGEST_VENUE, INGEST_YEAR, INGEST_COMMIT]
scripts:
  scholar_lookup:  {args: {seed: str, limit: int?}}
  search_arxiv:    {args: {query: str, limit: int?}}
  web_search:      {args: {query: str, limit: int?}}
  github_search:   {args: {query: str, probe: bool?}}
  fetch_page:      {args: {url: str, want: str?}}   # want: code_availability | metadata | full
  download_pdf:    {args: {slug: str, paper_url: str}}
  acquire_repo:    {args: {slug: str, repo_url: str, commit: str?}}
  format_entry:    {args: {slug: str}}
  validate_entry:  {args: {slug: str}}
  apply_entry:     {args: {slug: str, confirm: bool}}
```

### 六步流程（SKILL.md 编排，M2）

```text
① resolve   解析种子 → scholar_lookup / search_arxiv / web_search / github_search
            定位准确的论文链接与官方仓库链接
② verify    github_search --probe 采集官方性证据 → 确定性判定（抽象层规则）
③ acquire   download_pdf（多源链）+ acquire_repo（三通道）
④ format    format_entry：按 hub 契约渲染六行 bullet + CSV 行 + 双 README + gitignore 例外
⑤ validate  validate_entry（七条规则）全绿；不过则带报告回修（≤2 轮），仍败 → needs_human
⑥ report    产出验收单 report.md：产物清单 + 证据链 + 人工 git add/commit/PR 步骤
```

### 工作区

```text
.ingest/candidates/<slug>/       # gitignore
├── candidate.json               # 种子 + 解析结果 + 证据链 + 官方性判定（含 needs_review 原因）
├── paper/  repo/  cache/        # PDF、剥 .git 快照、页面缓存
├── staged/                      # README / CSV 行 / 例外块（apply 前的待落位产物）
└── validation.json  report.md
```

### 脚本职责要点

| 脚本 | 关键行为 |
|---|---|
| `scholar_lookup.py` | S2 Graph API；种子分流；元数据/openAccessPdf/作者信息 |
| `search_arxiv.py` / `web_search.py` / `fetch_page.py` / `download_pdf.py` | 按抽象层模板实现（通道/限流/多源链） |
| `github_search.py` | search / readme 互认 / tree 探针；`--probe` 内嵌官方性判定 |
| `acquire_repo.py` | 三通道：① GitHub shallow clone + `git rev-parse HEAD`（40 位）+ 剥 .git + 子模块递归实化（失败置 `repo_needs_review` 不静默跳过）② HF 经 hf-mirror.com 排除权重 ③ sdist 兜底 `pip download --no-deps --no-binary :all:`（hash 记 `unavailable`）；env 注入代理 |
| `format_entry.py` | 六行 bullet（verdict 决定措辞；无 commit 省略 commit 行）；CSV 三键查重 + QUOTE_MINIMAL；双 README 一次渲染两份（badge 计数=CSV 行数、venue 色值映射）；gitignore 例外自动建议（pathspec 模拟） |
| `validate_entry.py` | 七条契约规则（与 validate_hub.py 共用实现），`--json` 报告 |
| `apply_entry.py` | 前置：validate 全绿 + `confirm=true`；staged 落位到 `single-cell-hub/single_cell_models/<Name>/` + 更新 models.csv + 双 README + gitignore 例外；打印人工 git 步骤 |

### 人工决策点实例化

| 决策点 | 交互模式 | no-interactive |
|---|---|---|
| 官方性 `likely` | ask_user 确认或否决 | pending：`official_needs_review`，候选冻结 |
| 多候选仓库 | ask_user 列选项 | pending：列出候选 + 各自证据 |
| PDF 拿不到 | ask_user 给本地路径 | pending：`pdf_needs_manual` |
| 壳仓库/子模块 | ask_user 确认子模块清单 | pending：`repo_needs_review` |
| apply 落位前 | ask_user 确认 | **不落位**：staged 产物 + 验收单留待人工 |

**完成标准**：validate_entry 全绿（或 needs_human 状态明确且产物完整）；
report.md 完整（证据链表、产物清单、人工 git 步骤）；hub 侧无半成品状态
（staged 完整 / 已 apply / 明确 pending 三选一）。

## task/optimize（hub 纯读方，零网络）

目标：对 hub 内模型快照做**推理侧**昇腾适配：读码分析 → 适配计划 → 确定性改写 +
LLM 增强 → 分级验证 → 报告。支持指定单模型或批量遍历。

### skill.yaml（8 脚本）

```yaml
name: optimize
description: 单细胞模型仓库的昇腾 NPU 推理适配优化与分级验证
required_env: []                    # OPTIMIZE_MODEL_NAME 与 OPTIMIZE_REPO_PATH 二选一
optional_env: [OPTIMIZE_MODEL_NAME, OPTIMIZE_REPO_PATH, OPTIMIZE_STAGES]
scripts:
  scan_repo:        {args: {model: str}}
  plan_check:       {args: {model: str}}          # 计划后校验（防幻觉）
  transform_rules:  {args: {model: str, plan: str}}
  validate_static:  {args: {model: str}}
  validate_cpu:     {args: {model: str}}
  validate_npu:     {args: {model: str}}
  kernel_gym:       {args: {model: str, op: str}} # 可选 L3
  render_report:    {args: {model: str}}
```

### 语料事实（实测，写入 SKILL.md）

- 19/20 PyTorch；**CellFM 是 MindSpore**（只出分析 + 人工迁移指引，不自动转换）
- **GenePT / AIDO.Cell 无代码**（0 个 .py）→ preflight 跳过，终态 `skipped_incomplete`
- 痛点在依赖栈：flash-attn（scGPT/scPRINT）、RAPIDS（CellPLM）、faiss-gpu（SATURN）、
  bitsandbytes（Geneformer）；Performer/cosformer/flowformer 线性注意力可用标准 matmul 重写
- **scMulan 自带 `scMulan_npu.py`**（torch_npu 写法）——语料内现成正确迁移参考
- MVP 首选 **UCE**：10 个 py 文件，model.py 仅 115 行，纯标准 PyTorch
- NPU 环境：`/work/nvidia_ascend/.venv`（Python 3.10 + torch 2.7.1 + torch_npu 2.7.1.post2）
- KernelGYM：`/work/AscendKernelBench` 起 127.0.0.1:8082，`POST /evaluate` 返回
  compiled/correctness/speedup（L3 可选）

### 七步流程（SKILL.md 编排，M3）

```text
⓪ preflight  scan_repo 顺带检查：0 个 .py → skipped_incomplete 终态（不进 LLM 分析）
① scan        scan_repo：AST 静态扫描（无 LLM）→ 文件树/imports/classes/role/hard_deps/相关性预评分
② analyze     agent 分级读码（read 工具读 scan 标注的关键文件）→ model_card + 适配计划 JSON
③ plan_check  计划后校验：action.path 必须存在于 snapshot（防幻觉）；drop/stub 仅限非入口文件；
              CellFM → 只允许 analysis 类 action
④ transform   transform_rules 确定性打底（整树拷贝 + .cuda()→.npu() + import torch_npu 注入
              try/except + 依赖降级映射 cuml→sklearn、faiss→sklearn.neighbors）
              → agent 对 needs_llm 文件整文件重写（≤12 个/模型），护栏强制（抽象层模板）
⑤ validate    分级验证（抽象层 L0–L3），失败只记录不改代码
⑥ report      render_report 模板化报告（无 LLM）
```

### 产物（`runs/<ts>/optimize/<Model>/`，批量模式共用 run 目录）

```text
scan/            # file_index.json
analysis/        # model_card.json + plan.json
transformed/     # repo/ + changes.diff + patch_manifest.json（逐文件标注来源：rules | llm）
validation/      # L0-L3 各级结果 JSON
report.md
state.json       # 状态机 + 断点续跑
```

### 人工决策点实例化

| 决策点 | 交互模式 | no-interactive |
|---|---|---|
| 计划含高风险 drop/stub | ask_user 确认 | pending：降级为保守计划（不 drop，仅标注） |
| L1 stub 注入仍失败 | ask_user 选择继续/终止 | 记 `failed`，报告说明 |
| apply 类操作 | 不适用本任务——transformed 产物留 runs/，人工审后自行取用 | — |

**完成标准**：终态明确（done / done_with_warnings / failed / skipped_incomplete 之一）；
report.md 完整（状态、依赖矩阵、diff 摘要、验证各级结果与跳过原因）；批量模式
summary.md 聚合 20 模型状态矩阵 + token 消耗。

---

# Part IV 运营

## 批处理

`uv run ok batch --task optimize [--models UCE,scGPT ...] [--fresh]`：

1. 读 `models.csv` 得模型清单（可 `--models` 过滤）
2. 逐模型拉起独立 pi session（`--mode json`，`--name` 标记），串行
3. 每模型开始前查该 slug 的 state.json——终态即跳过（断点续跑）
4. 失败不中断批次；结束输出状态矩阵 + pending 清单汇总

## 里程碑

| 里程碑 | 内容 | 验收 |
|---|---|---|
| M0 runtime 打通 | pi 安装（pin 版本）；models.json + envguard；显式装配（--skill + -e）；path-guard / ask-user 两个扩展；launcher run + 一个 hello task | `run --task hello` 输出思考与工具轨迹；journal 落盘；env 白名单生效（脚本读得到注入值，agent 无任何工具能改 .env） |
| M0.5 pi 自包含 | pi 入 vendor（package-lock pin 0.87.0）；`ok setup` 一键装；bootstrap-guard 禁直跑；不依赖全局安装与 PATH | 全新 clone 后 `uv sync && uv run ok setup && uv run ok run --task hello` 走通；裸调 vendor pi 无令牌被拒 |
| M1 ingest 脚本层 | task/ingest 全部脚本 + hub 契约（本文 Part II） | `validate_hub.py --hub` 对现有 20 模型全绿；脚本可独立单测 |
| M2 ingest 端到端 | SKILL.md 编排 + LLM 搜索循环 + 降级路径 | 真实论文（arXiv ID）产出入库产物 + 验收单（不 commit） |
| M3 optimize MVP | task/optimize 脚本 + SKILL.md，跑 UCE | transformed 树通过 L0/L1，report 完整 |
| M4 批量与无人值守 | batch driver + pending 流程 | 20 模型批量状态矩阵；needs_human 项事后补跑闭环 |
| M5 预留 | 全自动论文发现（task/discover） | 复用 M2 工具层 |

## 关键风险与兜底

| 风险 | 兜底 |
|---|---|
| pi 版本演进破坏扩展 API | 精确 pin 版本；扩展仅三个小文件，逻辑尽量留在 agent/ py 层；SKILL.md 标准保证 task 层可平移（dsh 备选） |
| npm 安装网络问题 | npmmirror 镜像 + SHA256 锁定；或离线分发 node_modules |
| LLM 幻觉脚本参数 | path-guard 按 skill.yaml 的参数 schema 校验 bash 命令 + 脚本自身 argparse 严格模式 |
| agent 越界改文件 | write/edit 默认拒绝；bash 白名单；三层沙盒 |
| 抽象与实现偏移 | 本文单文档主线；下游实现不得违背 Part II；validate_hub 全量回归守护 hub 契约 |
| 脚本输出撑爆上下文 | 分发层截断 + 全文落 runs/ 供 read 追查 |
| 端点不返回 reasoning_content | json 模式事件流天然兼容：有则显示思考块，无则只显示 content + tool calls |
| token 成本失控 | skill 渐进披露不破坏 prompt cache；batch 串行 + usage 记账 + 每任务预算上限（超限终止记 needs_human） |

## 验证方式（端到端）

1. M0/M0.5：`uv sync && uv run ok setup`，然后 `uv run ok run --task hello --set FOO=bar`
   ——轨迹输出、journal 落盘、env 注入与冻结生效；尝试让 agent 改 `.env` 被 path-guard
   拒绝；裸调 vendor pi 无令牌被 bootstrap-guard 拒绝
2. M1：`uv run python task/ingest/scripts/validate_hub.py --hub` 现有 20 模型全绿
3. M2：`run --task ingest --set INGEST_SEED_URL=<arXiv 链接>` 端到端产物检查
   （目录三件套 / CSV 行 / 双 README diff / 验收单）
4. M3：`run --task optimize --set OPTIMIZE_MODEL_NAME=UCE` L0/L1 通过、报告完整
5. M4：batch 中断重跑，断点续跑正常；pending 补跑闭环
