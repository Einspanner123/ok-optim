# ok-optim-agent 设计文档

| 项 | 值 |
|---|---|
| 文档状态 | 生效（Living document） |
| 覆盖实现 | M0 / M0.5 / M1 / M2 已实现；M3 / M4 未实现（见 §14.3） |
| 代码分支 | `feat/pi-skill-arch` |
| 最后更新 | 2026-09-30 |

> **本文是项目唯一的设计文档**（`README.md` 与本文都以本文为唯一主线）。单一文档的目的：
> ingest 与 optimize 共用同一份 hub 契约，抽象集中定义才不会分叉。
>
> **规则**：改抽象必须同步下游实现；下游实现不得违背本文的契约层。
> **本文与代码不一致时，以实现即事实修正本文**——代码是最终事实源。
>
> **未实现的章节**一律用行内标注 `**状态：未实现（TBD）**` 明确标出，并列出"已定"
> 与"待定"两部分，不留模糊表述。全文不含未标注却尚未落地的设计。

---

## 修订记录

| 日期 | 变更 | 影响章节 |
|---|---|---|
| 2026-09-22 | 初版：四部分结构（系统架构 / 抽象层 / 下游实例化 / 运营）；pi 选型决策 | 全文 |
| 2026-09-23 | 补充已实现的权限边界、任务结果协议与退出码、重复调用反馈、增量入库 | §7 §8 |
| 2026-09-29 | 去重主键改为归一化仓库地址；入库改为无台账（候选工作区 → run 目录） | §11 §13.1 |
| 2026-09-30 | **重构为大厂规范设计文档结构**；补全守卫层实现细节、`read_dirs` 契约、测试与 CI 门禁；可读路径声明从公共常量下沉到 `skill.yaml`；修正 bash 命令规划器的选项与 `find` 语法缺陷；未实现项统一加 TBD 标注 | 全文，重点 §7 §15 §17 |

---

## 1. 摘要

围绕 `single-cell-hub`（20 个单细胞基础模型标准化快照）的两个 agent 任务：
**ingest**（论文检索入库）与 **optimize**（推理侧昇腾 NPU 适配）。

系统把 agent 框架层整体交给第三方 harness **pi**，项目自己只写两样东西：

- **启动器**（`agent/`）：环境装配、权限注入、运行归档。**不做任何 LLM 调用**。
- **任务能力**（`task/`）：`SKILL.md`（Agent Skills 标准的流程说明）+ 确定性 Python 脚本。

契约逻辑上收到顶层 **`hubkit/`**，由写入方（ingest）与读取方（optimize）共用同一份实现，
从机制上杜绝契约分叉。

| 关键数字（2026-09-30 实测） | 值 |
|---|---|
| 源码规模 | `agent/` 3971 行（含守卫与提示词）、`hubkit/` 918 行、`task/` 2532 行、`tests/` 3284 行 |
| 扩展（pi 侧 TS） | 6 个：`bootstrap-guard` / `budget-guard` / `command-policy` / `path-guard` / `safe-exec` / `ask-user` |
| 单测 | Node 60 通过、Python 272 通过（25 subtests）、pyright 0 error、ruff clean |
| 真实运行证据 | 8 个 `runs/*/events.jsonl`，共 199 次 bash 调用、179 条唯一命令 |

---

## 2. 背景与动机

`single-cell-hub` 是 20 个单细胞基础模型的标准化快照：每个模型都要「目录三件套 + CSV 一行 +
双 README 一条」，格式约束严格（10 列固定、双 README 逐字节镜像、徽章色值固定）。
人工维护的成本集中在两件事上：**找齐证据**（论文 ↔ 官方仓库 ↔ license ↔ commit）与
**按契约把文件写对**。这两件事都适合交给 agent，但都必须**可审计**——出错要能回放，越界要能拦住。

因此动机可以归纳为三条：

1. **确定性优先于智能**：能在脚本里写死的判断（格式校验、README 渲染、路径边界）不交给模型。
2. **审计优先于便利**：每次运行留下完整轨迹（pi session + `events.jsonl` + `task_result.json` + `issues`），
   人工执行 git 提交，agent 只准备产物。
3. **可替换 runtime**：task 层用 Agent Skills 开放标准描述，harness 若被替换（如 dsh 成熟），task 层可平移。

---

## 3. 目标与非目标

### 目标

| # | 目标 | 验收方式 |
|---|---|---|
| G1 | agent 能按 hub 契约产出完整入库产物 | `validate_hub.py --hub` 对现有 20 模型全绿 |
| G2 | 端到端跑通单篇论文入库（不 commit） | 真实 arXiv ID 产出 staged 产物 + 验收单 |
| G3 | agent 无法越界写盘、无法直连 provider 凭据 | 三层约束 + 用例覆盖（见 §15） |
| G4 | 每次运行可回放、结论可机器读 | journal + `task_result.json` + 退出码协议 |
| G5 | NPU 适配走同一套契约与分级验证 | 见 §12、§13.2（**未实现**） |

### 非目标

| # | 非目标 | 理由 |
|---|---|---|
| N1 | 不做 OS 级代码沙盒 | 路径防护是 harness 权限约束，不是容器。见 §7.1 的诚实声明 |
| N2 | agent 不执行 git commit / push | 提交由人工执行，产物 + 验收单就够 |
| N3 | 不自建 agent runtime | 用 pi，并把版本 pin 死（§6.1） |
| N4 | 不做跨 run 业务台账 | 任务无状态；身份与去重一律以 hub 本身为准（§11） |

---

## 4. 需求

### 4.1 功能需求

| 编号 | 需求 | 状态 |
|---|---|---|
| FR-1 | `ok setup` 一步装好 pi runtime（含 vendor Node），不碰全局、不改 PATH | 已实现 |
| FR-2 | `ok run --task <t>` 拉起一次受控会话，可注入任务参数 | 已实现 |
| FR-3 | 运行方案（`--config` / `--profile`）把常用参数组合固化为 YAML | 已实现 |
| FR-4 | ingest 两模式：`discover` 发现入库 / `audit` 复核存量异常 | 已实现（M2） |
| FR-5 | 入库产物按 hub 契约渲染与校验，人工提交 | 已实现（M2） |
| FR-6 | 无人值守时把裁决点降级为 needs_human 记录，不阻塞 | 已实现 |
| FR-7 | optimize 遍历/指定仓库做 NPU 适配与分级验证 | **未实现（M3）** |
| FR-8 | `ok batch` 批量驱动 + `ok pending` 待人工项闭环 | **未实现（M4）** |

### 4.2 非功能需求

| 编号 | 需求 | 落地方式 |
|---|---|---|
| NFR-1 | 确定性：同一输入的判定不随模型抖动 | 契约判断固化在 `hubkit/`；网络证据用结构化 API；`extract_repo_links` 的 `none` 是终态，LLM 不得推翻 |
| NFR-2 | 可审计：LLM 调用必然经 launcher | 一次性令牌 `AGENT_INVOKED_BY_LAUNCHER` + `bootstrap-guard` 禁直跑（§6.1） |
| NFR-3 | 最小权限：模型只有列出的能力 | bash 白名单 + `write`/`edit` 默认拒绝 + `read_dirs` 声明（§7） |
| NFR-4 | 失败可见：配置或契约错误不得静默降级 | fail-closed（§7.5）；`task_result.json` 兜底（§8.2） |
| NFR-5 | 成本可控：预算可数、超限强制收尾 | `budget-guard` 双信号 + 墙钟超时（§8.1） |
| NFR-6 | 可移植：本地 aarch64 与 CI x86_64 行为一致 | 一律经 `bootstrap._arch()`，禁止写死架构名（§15.3） |
| NFR-7 | 可替换：task 层不绑 harness | SKILL.md 走 Agent Skills 标准 |

---

## 5. 总体设计

### 5.1 系统上下文

```
                    ┌──────────────────────────────────────────────┐
   人工（唯一提交方） │  uv run ok run --task ingest --set ...        │
                    └───────────────────┬──────────────────────────┘
                                        │ 1. 装配：读 skill.yaml / .env / --config
                                        ▼
                    ┌──────────────────────────────────────────────┐
                    │  agent/ 启动器（无 LLM 调用）                  │
                    │  launcher · envguard · assembly · journal     │
                    └───────────────────┬──────────────────────────┘
                                        │ 2. 白名单 env 快照 + -e 显式加载扩展
                                        ▼
                    ┌──────────────────────────────────────────────┐
                    │  pi runtime（vendored，pin 0.87.0）           │
                    │  ┌────────────────┐  ┌─────────────────────┐ │
                    │  │ 守卫层（扩展）  │  │ LLM（OpenAI 兼容）  │ │
                    │  │ bootstrap-guard│  └──────────┬──────────┘ │
                    │  │ budget-guard   │             │ tool calls │
                    │  │ path-guard     │◀────────────┘            │
                    │  │ ask-user       │                          │
                    │  └───────┬────────┘                          │
                    └──────────┼───────────────────────────────────┘
                               │ 3. 唯一执行通道：声明的 task 脚本 / 只读命令
                               ▼
        ┌──────────────────────────┬──────────────────────────┐
        │ task/<t>/scripts/*.py    │ 只读：ls cat head tail wc │
        │ （确定性、可单测）         │       grep find pwd echo  │
        └────────────┬─────────────┴───────────┬──────────────┘
                     │ 4. 共用契约实现           │ 5. 只读
                     ▼                          ▼
        ┌────────────────────────────────────────────────────┐
        │ hubkit/  schema · readers · validators · ignore_rules│
        └────────────┬───────────────────────────────────────┘
                     ▼
        ┌────────────────────────────────────────────────────┐
        │ single-cell-hub/（子模块，只读；唯一写入口是 apply_entry）│
        └────────────────────────────────────────────────────┘
```

### 5.2 分层与目录结构

```text
ok-optim-agent/
├── main.py                        # 入口：from agent.launcher import main
├── pyproject.toml                 # deps: httpx/pathspec；不依赖 openai——LLM 调用全在 pi 内
├── .env                           # 外层配置（gitignore；模板 .env.example）
├── AGENTS.md / AGENTS_CN.md       # pi 项目指令：全局边界（hub 只读、脚本工具优先、禁止事项）
├── agent/                         # ══ 启动器层：无业务逻辑，不发起任何 LLM 调用 ══
│   ├── launcher.py                # ok CLI：run / batch / pending / status / setup
│   ├── bootstrap.py               # ok setup 实现：vendor node 下载（SHA256 锁定）+ npm ci + 校验
│   ├── envguard.py                # 读 .env → 构造白名单 env 快照 → 传给 pi 子进程
│   ├── assembly.py                # 装配：preflight、运行方案、models.json 渲染、guard 复制
│   ├── journal.py                 # 归档：session 链接、usage 汇总、status/exit_code、pending
│   ├── extensions/                # pi 扩展源文件（launcher 用 -e 显式加载）
│   │   ├── command-policy.ts      # 命令规划器：字面 argv 语法 + 可读路径边界（纯函数，可单测）
│   │   ├── path-guard.ts          # 工具调用拦截：注册 bash 工具、read 路径校验、fail-closed 外壳
│   │   ├── safe-exec.ts           # 执行已批准的 argv（shell=false、独立进程组、超时/中止）
│   │   ├── budget-guard.ts        # 工具预算 + 相同调用重复 + 同操作失败重复
│   │   ├── bootstrap-guard.ts     # 禁直跑守卫：缺 launcher 令牌的会话立即终止
│   │   └── ask-user.ts            # 注册 ask_user 工具（ctx.ui select/confirm/input）
│   ├── prompts/system.md(+_CN)    # 追加进 pi 系统提示词（不覆盖 pi 自带规则）
│   ├── vendor/                    # pi runtime 本体（gitignore，`ok setup` 生成）
│   │   ├── package.json(+lock)    #   ← 例外：这两个入库，pin pi 0.87.0
│   │   ├── node-v22.23.2-*/       #   vendor Node（tarball 解压，SHA256 校验）
│   │   └── node_modules/          #   pi 及依赖
│   └── runtime/                   # PI_CODING_AGENT_DIR（gitignore）：
│       ├── models.json            #   assembly 每次启动渲染
│       └── extensions/bootstrap-guard.ts  # 全局扩展位，必加载
├── hubkit/                        # ══ 可执行契约库：hub 契约的代码化身 ══
│   ├── schema.py                  # CSV 列定义、键约束、措辞规则、venue 映射、徽章体系
│   ├── readers.py                 # 读接口：models.csv / 模型 README / 双 README 名单
│   ├── validators.py              # 七条契约规则 + 孤儿检查（ingest/optimize 共用）
│   ├── render.py                  # 渲染纯函数：条目文件、双 README、CSV 行
│   └── ignore_rules.py            # gitignore 安全模拟、例外块建议
├── task/                          # ══ 任务层：按 §10/§11 抽象实例化 ══
│   ├── _template/                 # skill 标准结构模板（含 README 新建清单）
│   ├── hello/                     # M0 验收任务
│   ├── ingest/                    # 任务①（hub 唯一写入方）→ §13.1
│   └── optimize/                  # 任务②（hub 纯读方）→ §13.2（未实现）
├── configs/                       # 运行方案示例（ingest.profiles.example.yml）
├── docs/architecture.md           # 本文档（单一主线）
├── tests/                         # agent / hubkit / task 三层测试
├── runs/<ts>/<task>/<slug>/       # 运行工作区：journal、产物、events.jsonl（gitignore）
└── single-cell-hub/               # 子模块数据集：只读；唯一写入口是 task 脚本的 apply 类操作
```

`task/<name>/` 标准结构（§10 定义；模板与新建清单见 `task/_template/README.md`）：

```text
task/<name>/
├── skill.yaml         # 元数据：name / description / read_dirs / read_files / required_env / scripts
├── SKILL.md           # skill 设计（Agent Skills 标准）：流程、工具用法、预算与停止规则、完成标准
├── SKILL_CN.md        # SKILL.md 的中文直译版（逐段对应，改英文版后须同步，见 §18.2）
├── references/        # 任务专属知识（可选）
└── scripts/           # 确定性 py 脚本，可独立执行与单测
```

### 5.3 ok CLI（唯一入口）

```bash
uv sync && uv run ok setup    # 全新 clone：两步完成环境准备
uv run ok setup               # 安装/修复 pi runtime（幂等）
uv run ok run --task <t> [--set K=V ...] [--config F [--profile P]] [--interactive]
                              # 单次任务（run 可省略；默认非交互）
uv run ok run --task <t> --tool-budget N --failure-limit N --timeout S
                              # 护栏旋钮（也可写进 --config 的 flags）
uv run ok status              # 历史运行记录（journal 汇总）
uv run ok batch ...           # 批量（M4，未实现）
uv run ok pending             # 待人工项（M4，未实现）
```

- 入口 `ok`（`pyproject.toml` 的 `[project.scripts]`）；简写规则：首参数不是子命令时按 `run` 处理。
- `setup` 三步：vendor 无 Node 则从 npmmirror 下载 pinned tarball（**SHA256 锁定**，双源核对）
  → `npm ci`（`package-lock` 全树锁定）→ `pi --version` 校验；不写全局、不碰 PATH。
- 禁直跑：PATH 上无 pi；vendor 直跑缺 `AGENT_INVOKED_BY_LAUNCHER` 令牌会被
  `bootstrap-guard` 终止——**LLM 调用必然经 launcher 审计**。

### 5.4 关键决策记录（ADR）

| 决策点 | 选择 | 理由 |
|---|---|---|
| Agent runtime | **pi**（`@earendil-works/pi-coding-agent`） | 极简可控；OpenAI 兼容 endpoint 一等支持；扩展用 `-e` 显式装配（不用 `.pi/` 项目目录）；`--mode json` 便于启动器接管；session 单文件可回放 |
| 任务参数注入 | **混合分工** | 启动参数经 env/CLI 注入（确定性、可批处理）；运行中例外升级用 `ask_user`；非交互降级为 needs_human |
| task 可移植性 | **SKILL.md 开放标准**（agentskills.io） | `task/` 设计框架无关；harness 可替换，task 层平移 |
| git 提交 | **人工执行** | agent 只准备产物 + 校验 + 验收单 |
| 沙盒 | 启动器 env 白名单 + pi 扩展路径防护 + 脚本层校验 三层 | §7.2 |
| 框架层不手改 | **pi 代码只读**，只用公开注册机制与生命周期 hook | pi 是 0.x 单一作者依赖，vendored 且被 `.gitignore`；手改会在升级时丢失 |
| 文档形态 | **单文档主线**（本文） | ingest/optimize 共享 hub 契约，抽象集中定义避免偏移 |
| 命令安全边界（2026-09-30） | **边界是路径，不是选项** | `ls -R` 与 `ls -la` 同为只读；唯一真实的边界是"读哪个路径"。见 §7.3 |
| 可读路径声明位置（2026-09-30） | 从 `command-policy.ts` 公共常量 **下沉到 `skill.yaml`** | 加新 task 时公共代码零改动；缺失即拒绝启动 |
| 去重主键（2026-09-29） | **归一化仓库地址** `owner/repo` | 目标物是仓库；地址变了即视为另一篇论文；预印本↔正式版共享仓库自然合并。见 §11 |

### 5.5 备选框架对比结论（为什么不是它们）

- **claude code**：闭源 binary、绑 Anthropic 生态，接第三方 OpenAI 兼容 endpoint 属灰色地带，轨迹格式不可控。
- **hermes**（Nous Research）：常驻型自主 agent（持久记忆、自动技能创建、消息平台 gateway），
  与本项目"确定性、可审计、任务型"诉求相悖。
- **dsh**（DeepSeek Harness）：Trajectory 审计最强、Docker 沙盒内置，但官方声明开发者预览期有
  破坏性变更，插件开发用 TS 门槛高——**作为备选持续关注**，SKILL.md 标准保证平移可行。

---

## 6. 运行时设计（pi 集成）

### 6.1 安装与版本锁定

- pi 精确 pin 在 `agent/vendor/package.json` + `package-lock.json`（入库），本体装在
  `agent/vendor/`（gitignore，`ok setup` 生成）；版本升级 = 改版本号 + 重跑 `ok setup` + 全量回归。
- **两套 Node，用途不同**（易混，务必分清）：

| Node | 来源 | 版本 | 用途 |
|---|---|---|---|
| 系统 Node | `actions/setup-node`（CI）/ 宿主环境 | 22.x | 跑 `uvx pyright`（pyright 是 JS 实现）与 `node --test tests/agent/*.mjs` |
| vendor Node | `uv run python -m agent.bootstrap` 下载 | pin `v22.23.2` | **pi runtime 运行时**用，落在 `agent/vendor/node-v22.23.2-linux-<arch>/` |

- **禁直跑**：launcher 每次拉起注入一次性令牌 `AGENT_INVOKED_BY_LAUNCHER`；
  `bootstrap-guard.ts`（assembly 复制到 `runtime/extensions/`，必加载）发现缺令牌即 `process.exit(1)`。
  **防误用不防对抗**（能手注令牌的人拦不住；硬隔离走容器/专用 OS 用户）。

### 6.2 Provider 与 LLM 参数

- `models.json` 声明自定义 provider（`api: "openai-completions"`，`baseUrl`/`apiKey` 从 env 读取）。
- `.env`：`OPENAI_BASE_URL` / `OPENAI_API_KEY` / `AGENT_LLM_MODEL`。
- 采样参数演进（2026-09-24 二次修订）：**temperature 0.4 / top_p 0.95 / frequency_penalty 0.3**。

| 阶段 | 设置 | 结果 |
|---|---|---|
| 初版 | temperature 0（贪心，求一致） | 长会话实测触发重复退化（vLLM 部署） |
| 一次修订 | temperature 0.2 | 仍不足——三次 discover run 均以重复文本循环失控告终 |
| 二次修订（现行） | temperature 0.4 + frequency_penalty 0.3 | `samplingParams` 原样直传 vLLM，专惩重复 token；对执行链参数精度的代价小于继续升温 |

引擎侧 `repetition_penalty` 1.1 由 `generation_config` 提供。
扩展采样参数（`top_k` / `min_p` / `presence_penalty` / `seed`）**仅在 env 显式设置时**进
`samplingParams`——未设置即交给端点默认，避免"发送默认值反而改变端点行为"。

### 6.3 Skills 装配

- 不用 `.pi/` 项目目录（pi 相关文件全部收在 `agent/` 下）：launcher 以 `--skill task/<name>`
  显式加载任务目录（SKILL.md 就在任务目录内）。
- SKILL.md 遵循 Agent Skills 标准（YAML front-matter `name`/`description` + 正文）。
- pi 按需渐进加载 skill（progressive disclosure），不破坏 prompt cache——批处理成本友好。

### 6.4 运行模式

| 场景 | 模式 | 说明 |
|---|---|---|
| 人工值守调试 | launcher 交互模式（TUI） | `--interactive`；pi 由 launcher 显式装配（`--skill` + `-e`），`ask_user` 可用 |
| 启动器单次任务 | `pi -p --mode json` | launcher 拉起，按 `--output` 渲染到 stdout：`human`（默认，流式轨迹+横幅）/ `quiet`（一行 JSON 摘要）/ `raw`（事件透传）；`events.jsonl` 恒存全量原始事件 |
| 批处理 | launcher 循环逐模型拉起 | **未实现（M4）** |

默认值：`run` → 非交互，显式 `--interactive` 且 stdout 为 TTY 才进 TUI；`batch` → 恒为非交互。

### 6.5 会话与审计

- pi session 由 launcher 以 `--session-dir` 定向到 run 目录（append-only，含 reasoning / tool calls 全轨迹）；
  **禁用 `--no-session`**。
- `journal.py` 运行结束把 session 路径、usage 汇总、产物清单、`runtime_status` / `task_status` /
  `validation_status` / 最终 `status` 与 `exit_code` 归档进 `runs/<ts>/`。
- 同次运行只消费**一种**消息来源（非交互解析 `events.jsonl`，交互解析真实 session），
  避免双重计数。
- launcher 用**独立定时器**监控非交互会话墙钟（`--timeout`，默认 1800s；0 关闭），
  避免"无日志输出时无法触发超时"。

---

## 7. 安全模型

### 7.1 威胁模型与信任边界

| 角色 | 是否可信 | 说明 |
|---|---|---|
| 人工 | 可信 | 唯一执行 git 提交与契约变更的一方 |
| launcher / 守卫扩展 | 可信 | 我们自己写的代码，模型无法修改（`agent/` 只读） |
| **LLM 的工具调用** | **不可信** | 可能幻觉参数、绕路、重复循环。全部经守卫 |
| **task 脚本** | **可信代码** | 我们自己写的确定性脚本；**但不是 OS 级隔离对象** |
| optimize 将执行的模型代码 | **不可信** | 见下方声明 |

> **诚实声明（对应非目标 N1）**：现行机制是 **harness 权限约束**，不是 OS 沙盒。
> 路径防护拦得住"模型让 bash 去读 `.env`"，拦不住"已声明的 Python 脚本自己胡来"——
> 脚本是我们写的可信代码，尚无 OS 级第三方代码隔离。
> **optimize 将来执行模型代码时，必须另行实现隔离策略**（容器 / 专用用户），
> 不能把路径防护当作代码沙盒。容器隔离（pi 支持 Gondolin / Docker / OpenShell）可选：
> ingest 可容器跑；**optimize 不容器化**——NPU 设备透传复杂，host 跑 + 路径防护。

### 7.2 三层约束

机制澄清：agent 在 bash 里 `export` 只影响它自己的子进程，**天然污染不到启动器注入的配置**。
真正要防的是持久化篡改与越权写盘：

| 层 | 机制 | 实现 |
|---|---|---|
| 1. 进程层（envguard） | launcher 从 `.env` 构造**白名单 env 快照**传给 pi 子进程；provider 凭据只进 pi 自己的进程，不进任务脚本 | `envguard.py` + `assembly.preflight` 校验 `required_env`，缺失即拒绝启动并打印注入清单 |
| 2. harness 层（path-guard） | 拦截工具调用：`write`/`edit` 默认拒绝；`read` 限可读路径；`bash` 只放行声明的脚本与只读命令 | `path-guard.ts` + `command-policy.ts` + `safe-exec.ts`（§7.3） |
| 3. 脚本层（最后防线） | 任务脚本内部对写入目标做路径前缀校验；argparse 严格模式；幂等设计 | `task/*/scripts/` |

补充：**任务脚本只收到必要变量**（`scriptEnvironment()` 白名单 + skill 声明的任务参数），
`OPENAI_` / `PI_` / `PYTHON` / `LD_` / `NODE_` / `BASH` / `PATH` / `HOME` / `GIT_` 前缀一律**不透传**。

#### LLM 可见的工具面

pi 默认给模型四个工具（`read` / `write` / `edit` / `bash`）+ 我们的扩展。经 `path-guard`
拦截收紧后，等效于"**声明的 task 脚本是唯一执行通道**"的语义：

| 工具 | 策略 |
|---|---|
| `bash` | **命令白名单**（§7.3）：仅允许 `uv run python task/<当前task>/scripts/<已声明脚本>.py <args>`，以及 `ls` / `cat` / `head` / `tail` / `wc` / `grep` / `find` / `pwd` / `echo` / `git status` 只读命令；**安全边界是路径**，选项不构成边界 |
| `write` / `edit` | **默认拒绝**——"LLM 不直接写盘"硬约束：一切产物写入只能经 task 脚本（脚本内部有自己的路径白名单） |
| `read` | 可读路径内可读（§7.4） |
| `ask_user(question, options)` | 仅交互模式加载 `ask-user.ts` 并注册；非交互**不暴露** |
| slash `/skill:<name>` | pi 原生技能入口，SKILL.md 即任务说明书 |

launcher **关闭扩展自动发现**，仅显式加载 `bootstrap-guard` / `budget-guard` / `path-guard`；
仅交互模式额外加载 `ask-user`。因此非交互运行下模型可见工具为 **`read` + `bash`**。

### 7.3 bash 命令规划器（`command-policy.ts`）

#### 原则：边界是路径，不是选项

只读命令的选项**不构成安全边界**——`ls -R` 与 `ls -la` 同为只读，`head -c 5` 与 `head -n 5`
同样只读。选项只做**形态检查**（`OPTION_RE`，拦 `-x;rm` 这类拼接），真正的边界是可读路径。
这条原则同时解决了另一个问题：早先把选项当成边界去"规范化"，反而静默改变了命令语义。

#### 输入语法：字面 argv，无 shell

`tokenize()` 实现一个**刻意比 shell 小得多**的字面语法：

- 引号内容视为**字面参数**（如 `--payload '<json>'`），引号内允许任意字符——shell 从来看不到它。
- 引号**外**用**字符集白名单** `SAFE_CHAR = /[A-Za-z0-9._/@:%=,+~-]/`，未列出的字符直接拒绝
  （黑名单永远可能漏一个）。因此 `& | ; < > \` $ * ? ~ { } ( ) [ ] # !` 等一律不可用。
- 控制字符（`\0` `\n` `\r`）禁用；空命令、未闭合引号拒绝。

最终执行由 `safe-exec.ts` 用 `spawn(executable, args, {shell: false})` 完成，
**接受后的命令永远不经过 shell**。

#### 命令白名单

| 类别 | 允许 | 说明 |
|---|---|---|
| 任务脚本 | `uv run python task/<当前task>/scripts/<已声明脚本>.py <args>` | 仅限本 task `skill.yaml` 的 `scripts` 键；实际执行 `.venv/bin/python -B -E -s <脚本>`，**不调用 uv 同步或安装** |
| 只读命令 | `ls` `cat` `head` `tail` `wc` `grep` `find` | 目标必须是可读路径 |
| 其它 | `pwd`、字面 `echo`（转成 `printf '%s\n'`）、`git status` + 展示选项 | git 关闭 fsmonitor 与 hook，禁 `--output` / 版本参数 / 外部 diff；**提交类操作一律由人工在工具面外执行** |

#### 选项处理规则（2026-09-30 重写，数据驱动）

选项一律**原样透传**，只对"值型选项"做 token 分类，避免值被误判为路径。规则来自
`runs/*/events.jsonl` 里 **199 次 bash 调用**的实际选项分布：

| 实际出现 | 次数 | 归类 |
|---|---|---|
| `-la` | 53 | ls 开关 |
| `-n` | 7 | head/tail 取值、grep 开关（**同名不同义，按下达命令区分**） |
| `-<数字>` | 5 | head/tail 的行数简写，规范化为 `-n <数字>` |
| `-i` `-o` `-R` `-l` `-u` | 各 1–4 | 开关 |
| `-c` | 3 | **head 取值（`head -c 2000`）、grep 开关** |
| `-type` | 1 | find 谓词（取值） |

据此定义两张小表（**只影响 token 分类，与安全无关**）：

- `NUMERIC_OPTIONS`：`head/tail` 的 `-c -n`、`grep` 的 `-m -A -B -C`、`ls` 的 `-w`——紧随其后必须是数字。
- `REJECTED_OPTIONS`：本环境不可用的选项，拒绝并给出原因
  - `tail -f / -F / --follow*`：**永不返回**，会把会话挂到墙钟超时。
  - `grep -e / -f`、`wc --files0-from`：引入第二个输入源；模式一律走位置参数。

未登记的"值型"选项会让它的值落进路径校验并报 `no such path`——**响亮失败**，
不会静默改变命令语义（这正是本次要修的缺陷类型）。

> **历史缺陷（已修，记录以免回退）**：旧实现把任何非 `-<数字>` 的选项都改写成 `-n 10`，
> 于是 `head --bytes=5 f` 实跑成 `head -n 10 f`（要 5 字节给 10 行，`exit 0` 无声），
> `head -c 5 f` 则报误导性的 `ENOENT lstat '<ROOT>/5'`。

#### `grep` 的 argv 组装

语法是 `grep [OPTIONS] PATTERN [FILE...]`。规划器把**模式串写进 argv**并置于路径之前：

```
grep -n MARKER_ALPHA README.md   ->   grep -n -- MARKER_ALPHA /abs/README.md
```

模式必须存在（`grep` 单独调用报 `requires a pattern`），且**必须给出待搜文件**——
bash 子进程的 stdin 是 `/dev/null`，无文件的 grep 只可能返回"无匹配"，属于静默假阴性。

> **历史缺陷（已修）**：旧实现的 `resolved` 只含路径，模式串只存在于 `positional`
> 却从没被写进 argv。于是 grep 把**文件路径当成模式**、转去读 stdin，永远无匹配：
> `grep -c <存在的串> <文件>` 会理直气壮地报告 `0`。这比它取代的
> `command is not allowed` **更糟**——错误信息从"明确拒绝"退化成"看起来成功了"。

#### `find` 的表达式是"程序"而不是"选项"

GNU find 语法是 `find [起始目录...] [表达式...]`：**起始目录必须在表达式之前**。
规划器据此切分，并对表达式做**允许清单**——因为 `-delete` 会改盘、`-exec` 会执行外部命令：

| 允许 | 内容 |
|---|---|
| 筛选谓词（取值） | `-name -iname -path -ipath -regex -iregex -type -maxdepth -mindepth -size -mtime -mmin -newer -newermt` |
| 开关 | `-print -print0 -prune -not -a -o` |
| 其余 | 一律拒绝（`find predicate is not allowed: -delete`） |

```
find task -name hello.py   ->   find /abs/task -name hello.py
```

> **历史缺陷（已修）**：旧实现生成 `<谓词> -- <路径>`，两个问题同时存在——
> (a) 违反 GNU find 语法，**带谓词的 find 全废**（`unknown predicate '--'`），
> 于是 `/ -name` 之外的 find 用法实际从未工作过；(b) 修好顺序后 `-delete` 会变得**真的可执行**，
> 所以顺序修复必须与表达式允许清单同时落地。
> 另注：旧行为下 `find task -delete` "安全"是**碰巧安全**（被 `--` 语法错误挡住），不可依赖。

#### 路径校验

- 所有位置参数经 `realpathSync` 解析为**绝对路径**后再执行；因此 argv 中**不需要 `--` 终止符**
  （绝对路径不可能以 `-` 开头）。`grep` 仍用 `--` 把模式与选项隔开。
- `readAllowed()` 做**边界比较**：既判前缀也判分隔符，`runs-secret/` 不会被 `runs/` 放行；
  符号链接按 realpath 后的**真实目标**判定，越界即拒绝。
- 报错按原因分开，避免诊断误导：`no such path: X`（路径不存在）与
  `path outside readable roots: X`（越界）是两条不同的信息。

### 7.4 `read_dirs` / `read_files` 契约

可读路径由**各 task 的 `skill.yaml` 声明**，经 launcher 注入
（`AGENT_READ_DIRS_JSON` / `AGENT_READ_FILES_JSON`），`path-guard` 在会话启动时解析成
规范绝对路径（`resolveScope()`）。

| 规则 | 说明 |
|---|---|
| `read_dirs` 必须显式声明 | **缺失即拒绝启动**（`assembly._read_list` 抛 `PreflightError`） |
| `read_files` 可省略 | 省略等价于空列表 |
| 声明了**不存在**的路径 | **配置错误**：`resolveScope()` 抛出 `declared readable path does not exist: X (fix read_dirs/read_files in task/<task>/skill.yaml)` |
| 根目录特例 | `ls` 允许列项目根（便于定位），但**读取文件仍限可读路径** |

现行声明（均为真实存在的路径）：

| task | `read_dirs` | `read_files` |
|---|---|---|
| `ingest` | `task` `runs` `single-cell-hub` `docs` | `AGENTS.md` `README.md` `pyproject.toml` `main.py` `.env.example` `.python-version` |
| `hello` | `task` `runs` | `README.md` |
| `_template` | `task` `runs`（示例） | `README.md`（示例） |

> **设计意图**：把可读路径下沉到 `skill.yaml` 后，**加新 task 时公共代码零改动**——
> 这是本层最值得保留的可扩展性设计。
>
> **历史缺陷（已修）**：`roots()` 曾对每个 entry 做 `realpathSync`，ENOENT 被
> `readAllowed()` 的 `catch` 吞掉。结果是"声明里多一条不存在的路径"会**静默拒绝全部读取**——
> 连 `read_files` 里存在的文件都读不到，报错文案还误导向 `path outside readable roots`。
> 现在配置错误会大声抛出。

### 7.5 fail-closed 的两种形态

"失败即拒绝"在这个系统里有两个层次，实现方式**不同**，必须分清：

| 形态 | 场景 | 实现 | 为什么不用另一种 |
|---|---|---|---|
| **启动即拒绝** | `skill.yaml` 缺 `read_dirs` / `required_env` 缺失 / 项目 `.venv` 缺失 | `assembly.PreflightError` → launcher 打印原因并返回 `EXIT_FATAL`（3），**pi 根本不启动** | 最便宜、最清晰——错误发生在装配期 |
| **阻塞一切工具调用** | 守卫扩展（`path-guard`）初始化失败：env 缺失、声明路径不存在、`task` 名称非法 | 扩展**不抛异常**，而是注册一个"拒绝一切 `tool_call`"的处理器后返回 | **pi 的扩展加载器会 catch 工厂异常** |

> **关键实测结论（决定了 §7.5 第二种形态的必要性）**：pi 的
> `loadExtension` 捕获扩展工厂抛出的异常，把它降级成一条 **`type: "error"` 诊断信息**
> （`main.js` 的 diagnostics 数组）后**继续启动会话**。
> 也就是说：**在扩展里 `throw` 并不能阻止会话启动，只会让这个扩展消失。**
> 对 `path-guard` 而言这等于守卫整个失效——`bash` 工具不会被替换，模型拿到的将是
> **pi 原生 bash**（完整 shell）。这才是真正的 fail-open。
>
> 因此 `path-guard.ts` 的初始化错误**不抛**，改为安装"拒绝一切工具"的守卫，
> 把任何配置失败都收敛成拒绝。（`bootstrap-guard` 走的是另一条路：`process.exit(1)` 终止会话。）

### 7.6 任务权限矩阵（共享工具的边界）

ingest 与 optimize 面向同一 hub，但能力面截然不同：**optimize 是零网络任务**
（seed 是本地模型名，repo 快照已在 hub，缺权重的验证直接 skip 而非下载）；
ingest 是重网络任务 + hub 唯一写入方。四层落地：**声明**（`skill.yaml`）→ **装配**
（preflight）→ **拦截**（`path-guard` 按 `AGENT_TASK` 限定各自 scripts）→ **脚本层**
（hub 写函数只存在于 ingest 侧 `apply_entry`）。

| 资源 / 能力 | ingest | optimize |
|---|---|---|
| hub 读（模型目录/CSV/双 README） | ✅ | ✅ |
| hub 写 | ✅ 仅 `apply_entry`（校验全绿 + confirm 前置） | ❌ 产物留 `runs/`，人工审后取用 |
| 检索（Semantic Scholar / arXiv / DuckDuckGo / 期刊页） | ✅ | ❌ 无需求 |
| `gh api` / `git clone` | ✅ 克隆到候选区 | ❌ 源码已在 hub，禁重复克隆 |
| 大文件下载 | PDF / repo → 候选工作区 | ❌（L2 缺权重 → skip） |
| NPU 子进程（`NPU_PYTHON`） | ❌ | ✅ 仅 `validate_cpu` / `validate_npu` |
| `runs/` 写 | ✅ 自己 slug 目录 | ✅ 自己 slug 目录 |
| 环境变量命名空间 | `INGEST_*` | `OPTIMIZE_*` + `NPU_PYTHON` |

共享面收敛为 §9 的 hub 契约：写入方（ingest）按它实现并自证合规（校验器归 ingest 侧），
纯读方（optimize）按它读取、读错即报，不需要也不持有校验代码。

### 7.7 交互模型（混合分工）

- **启动时注入（确定性）**
  - `uv run ok run --task ingest --set INGEST_SEED_URL=arxiv:xxxx`（默认非交互）
  - `uv run ok run --task optimize --set OPTIMIZE_MODEL_NAME=UCE`
  - `--set K=V` 进 env 快照；launcher 组装首条任务指令（任务名 + 参数说明 + 指向 SKILL.md）注入 pi。
  - `--config <yml> [--profile <name>]`：YAML 固化 env + flags（模板 `configs/ingest.profiles.example.yml`），
    优先级 `--set` > 方案 > `.env`；env 键限 `skill.yaml` 白名单、未知键拒绝（fail-closed）；
    `INGEST_APPLY_AUTHORIZED=1` 由 `apply_entry` 机械校验。
- **运行中升级（`ask_user`）**：agent 无法从 env 得知的裁决点——官方性存疑确认、
  多候选仓库二选一、PDF / repo 人工兜底、apply 前确认。
- **无人值守降级**：默认非交互或 batch 模式**不加载提问扩展、不暴露提问工具**；
  启动指令直接说明无人应答，agent 按各任务 SKILL.md 约定改走 needs_human 分支
  （写 pending + 安全收尾），事后 `uv run ok pending` 列出待人工项，处理后重跑。

---

## 8. 编排与护栏

### 8.1 工具预算与失败重复（`budget-guard.ts`）

护栏在 `path-guard` **之前**计数——**包括被拒绝的调用**（被拒也是一次消耗）。

| 信号 | 触发条件 | 理由 |
|---|---|---|
| 总预算 | 请求数 > `AGENT_TOOL_BUDGET`（默认 60，`--tool-budget`） | 超限即要求模型汇报并停止 |
| 相同调用重复 | 工具名 + 参数（stable stringify）**连续**相同 > 2 次 | 抓"原地打转" |
| **同操作失败重复** | 同一操作**失败**次数 ≥ `AGENT_FAILURE_LIMIT`（默认 3，`--failure-limit`） | 抓"交错重试" |

**为什么需要第二个失败信号**（真实数据驱动）：旧实现只比"上一次调用"（`consecutive === previous`），
而主导失败模式是**交错的**——`scholar_lookup` 在一次 run 里撞 S2 限流 **9 次**，
每次重试之间夹着别的调用，于是计数每次都被重置回 1，**永远达不到阈值**。
最贵的失败模式恰好最抓不到。

失败键由**调用形态**导出（tool + 归一化命令：URL → `<url>`、长 hex → `<id>`、4 位数字 → `<n>`），
**不含错误文本**——因为检查发生在 `tool_call`（错误尚不存在），学习发生在 `tool_result`（错误才出现），
只有让键与错误无关，两侧才永远对得上。错误文本只用于生成**人类可读的拦截理由**。

### 8.2 结束协议：`task_result.json`

原则：**"模型说完成了"不算完成**。每次运行用唯一 `run_id` 与独立目录，
最终确定性业务脚本**原子写入** `$AGENT_RUN_DIR/task_result.json`：

```json
{
  "version": 1,
  "run_id": "<AGENT_RUN_ID>",
  "task": "hello",
  "slug": "hello",
  "status": "done",
  "validation_status": "passed",
  "checks": [{"name": "hello_script", "status": "passed"}],
  "reason": "hello script completed and produced its environment snapshot"
}
```

| 字段 | 取值 |
|---|---|
| `status` | `done` / `done_with_warnings` / `needs_human` / `failed` / `skipped_incomplete` |
| `validation_status` | `passed` / `warnings` / `failed` / `skipped` |
| `checks[]` | `name` + `passed` / `failed` / `skipped` |
| 约束 | `done` 要求 `checks` 非空且全部 `passed`；`done_with_warnings` 不得含 `failed` |

**journal 侧校验**：校验 `version` 与 `task` / `slug` / `run_id`；缺结果视为 `incomplete`；
身份不符或格式损坏视为 `invalid_result`。

**兜底（2026-09-30 新增）**：`task_result.json` 只由 `_state.py` 的 `progress()` 在
**脚本跑完时**写入。若模型在任何脚本执行前就被预算杀掉，文件永不产出，run 只会留下
`status: unknown` → `last_error: "task script did not publish task_result.json"`。
现在 `journal.finalize()` 在该分支后合成一份 `status: "skipped_incomplete"` 的结果
（`checks: [{"name": "task_result_published", "status": "skipped"}]` + 中文 `reason`），
保证**每个 run 目录都有一份可机器读的结论**，且该兜底产物必须过自己的校验器。

### 8.3 退出码

| status | CLI 退出码 |
|---|---|
| `done` / `done_with_warnings` | 0 |
| `needs_human` / `incomplete` / `skipped_incomplete` | 2 |
| `failed`、运行/审计/结果错误 | 3 |

**pi 退出 0 不再自动代表成功**：pi 非零退出、LLM error/aborted、审计不完整**优先于**业务成功；
会话必须正常收尾。`hello` 已接入协议；其他任务在实现终态脚本时必须接入。
**历史 journal 不会自动重写。**

### 8.4 运行内提示词与反馈

- `agent/prompts/system.md` 经 launcher 的 `--append-system-prompt` 加入 pi 系统提示词，
  **不覆盖** pi 自带工具规则。模型用英文规划；非交互最终答复默认简体中文；交互答复按
  最近一条真实用户请求的主要叙述语言调整。
- 项目撰写的模型指令、工具描述、守卫反馈一律用**英文**；`AGENTS.md` 维护全局规则，
  各任务 `SKILL.md` 维护业务流程。外部论文与代码保持原文。
- 各 `SKILL.md` 顶部有 **Budget and stopping rules** 段落，把"何时停止"从散文变成**可数约束**
  （频道 discovery 调用上限、重试上限、工具预算的停线）。

---

## 9. 契约层：hub 契约（格式事实源）

> 本节是 `single-cell-hub` 的**唯一格式事实源**：ingest（写入方）按它生成产物并自证合规，
> optimize（纯读方）按它读取。**可执行化身是顶层 `hubkit/` 包**——两侧共用同一实现，
> 从机制上杜绝契约分叉。
> **契约变更流程**：人工编辑本节 → 同步 `hubkit/` → 两侧实现各自跟进 →
> `validate_hub.py --hub` 全量回归。

### 9.1 仓库顶层布局

```text
single-cell-hub/
├── README.md                    # 对外门面（外层 README）
├── .gitignore                   # 忽略规则 + 例外块（见 §9.8）
├── dl.py                        # 历史下载脚本（遗留，契约不约束）
└── single_cell_models/          # 模型条目根目录
    ├── README.md                # 内层 README（与外层镜像，见 §9.7）
    ├── models.csv               # 元数据表（见 §9.3）
    └── <Name>/                  # 每模型一个目录（见 §9.4）
```

### 9.2 Entity：模型条目

一个模型条目 = **四位一体**，缺一不可：

1. 目录 `single_cell_models/<Name>/`（三件套）
2. `models.csv` 中恰好一行
3. 外层 README 一条 bullet
4. 对比表一行

`<Name>` 规则：与 CSV `model_name` 完全一致；**大小写敏感**（`AIDO.Cell`、`Cell2Sentence`）；
允许 `.`、`-`，不允许空格与路径分隔符。

### 9.3 表格接口（`models.csv`）

UTF-8、带表头、`QUOTE_MINIMAL`、逗号分隔、`\n` 换行。**10 列，列名与顺序固定**：

| # | 列名 | 类型 | 约束 |
|---|---|---|---|
| 1 | `model_name` | str | 非空；全表唯一；与目录名一致 |
| 2 | `paper_title` | str | 非空；论文官方标题 |
| 3 | `year` | int | 2019–2030；四位数 |
| 4 | `venue` | str | 非空；与 README 徽章 venue 词一致（§9.7 映射表） |
| 5 | `paper_url` | url | 非空；http(s) |
| 6 | `repo_url` | url | 非空；http(s)；GitHub 或 HF URL |
| 7 | `github_stars` | int | **可空**（HF 条目或未标注时留空）；GitHub 条目为抓取时数值 |
| 8 | `framework` | str | 非空；`PyTorch` / `MindSpore` / `PyTorch/Hugging Face` 形态 |
| 9 | `license` | str | 非空；以 repo 快照内 LICENSE 实际文本为准（PyPI 兜底通道：以包内 LICENSE 为准） |
| 10 | `commit_hash` | str | **裸值**：恰好 40 位 hex，或字面 `unavailable`；无括号注解、无前缀 |

**键约束**：`model_name`、`repo_url`、`commit_hash` 三键全表无重复。

### 9.4 目录格式

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
  `author_maintained` → "Author-maintained repository"（HF 条目强制后者，Geneformer 先例）。
  允许变体 "Official code repository"（AIDO.Cell 实例）。
- **`Framework/license` 行允许两形态**：
  - 形态 a：``Framework/license/commit: <fw> / <lic> / `<40hex>` ``
  - 形态 b：`Framework/license: <fw> / <lic>.` —— commit 以 40hex 形式融入
    `Status` 叙述（scPRINT / Geneformer 实例）；CSV `commit_hash=unavailable` 时无此要求。
- `framework` / `license` 在 README 中是**自由文本描述**（如 "Python/PyTorch utilities"、
  "not declared in repository root"），与 CSV 规范值做**语义比对**（忽略大小写的包含关系），
  不要求逐字一致。
- `Status` 必须含字面 **"PDF downloaded"**；repo 内无 `.py` 时必须写明原因（如 "no runnable code"）。
- `Verification` 须为含主谓的**完整句子**，说明官方性判定依据与 license 结论。

**repo 快照排除规则**（acquire 时执行）：排除模型权重与大数据（按扩展名
`*.pt/*.pth/*.ckpt/*.bin/*.onnx/*.safetensors/*.h5ad/*.csv…` + 目录名
`data/weights/checkpoints/`）；代码外单文件 >2MB；`notebooks/figures/data` 目录默认排除
但**必须列入报告**。被排除项中若含 gitignore 会吞掉的代码相关文件，走例外块捞回（§9.8）。

### 9.5 双 README 同步

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
（顺序：📄 → code/🤗 → Stars → Local；📄 → `paper_url`，code → `repo_url`，Local → `./<Name>/`）。

**对比表行模板**（两 README 各含一张，列 `| Model | Year | Venue | Framework | Repository Link |`）：

```
| **<Name>** | <year> | <venue 短名> | <framework 短名> | [<owner/repo>](<repo_url>) |
```

venue 用短名（Nature Commun.、Nature Mach Intell.、NeurIPS W）；framework 短名（`PyTorch / HF`）；
GitHub 条目链接文本 `owner/repo`，HF 条目用域名显示。

**两 README 的差异（仅两处，其余逐字节一致）**：

| 项 | 外层 `README.md` | 内层 `single_cell_models/README.md` |
|---|---|---|
| Local 徽章前缀 | `./single_cell_models/<Name>/` | `./<Name>/` |
| footer CSV 链接 | `` [`models.csv`](./single_cell_models/models.csv) `` | `` [`models.csv`](./models.csv) `` |

> 已知偏差：现存外层 footer 链接为 `./single_cell_models/models.csv/`（末尾多 `/`）。
> 契约规定为无尾斜杠形式；**存量修正走人工 commit**。

### 9.6 七条契约校验规则

可执行实现：`hubkit/validators.py`；CLI 入口：`task/ingest/scripts/validate_hub.py`。

| 规则名 | 内容 | 失败处置 |
|---|---|---|
| 目录三件套（`structure`） | 目录三件套齐全；PDF 以 `%PDF` 开头且 ≥50KB | error |
| README 一致性（`readme_csv`） | 模型 README 六行与 CSV 行字段一致（含措辞规则） | error |
| 表格合法性（`csv_schema`） | CSV 10 列名序正确；三键唯一；`commit_hash` 裸值合法 | error |
| 双 README 镜像（`readme_mirror`） | 双 README 镜像一致（两处差异之外逐字节相同）；条目数徽章 = CSV 行数；bullet/对比表行与 CSV 一一对应 | error |
| 代码存在性（`code_presence`） | `repo/` 内 `.py` ≥1；为 0 时模型 README `Status` 必须说明原因 | error / 条件通过 |
| gitignore 安全（`ignore_safety`） | repo 内代码相关 <2MB 文件不被 gitignore 忽略（含例外块核对） | error |
| 关键行措辞（`wording`） | `Verification` 为完整句子；`Status` 含 "PDF downloaded" | error |

`--hub` 全量体检 = 七条规则 × 全部条目 + **孤儿检查**（目录无 CSV 行、CSV 行无目录、双 README 有条目无目录）。

### 9.7 gitignore 例外模式

hub 根 `.gitignore` 按扩展名大类忽略（csv/h5ad/权重/压缩包/notebook 等）。repo 快照中
**代码相关且 <2MB** 的文件若被规则吞掉，必须追加例外块：

```gitignore
# 例外：<Name> 源码快照内的<说明>（<原因>）
!single_cell_models/<Name>/repo/**/*.<ext>
```

- **判定**：apply 前用 `pathspec` 按忽略规则模拟快照全部文件；代码相关扩展
  （`.py .pyi .r .sh .yaml .yml .toml .json .pkl .ipynb .txt .csv` 等）被命中即需例外
  （`.csv` 是吸取 GenePT `gene_info_table.csv` 被吞的教训）。
- 例外块追加在 `.gitignore` 末尾"例外"区，一个模型一块，注释注明模型名与原因。
- 存量先例：Geneformer `*.pkl/*.ipynb`、scPRINT `*.ipynb`。
  （**GenePT 事故**：代码文件被吞且无人察觉——本规则为直接防线。）

### 9.8 读者与写者

| 角色 | 权限 | 实现 |
|---|---|---|
| ingest | **唯一写入方**（`apply_entry`：新模型目录 + CSV 行 + 双 README + gitignore 例外） | `task/ingest/scripts/`（薄壳 CLI + 落位），校验/渲染共用 `hubkit/` |
| optimize | **纯读方** | `task/optimize/scripts/`，经 `hubkit/readers` 读取；读失败报错，不校验不回写 |
| 人工 | 契约变更 + git commit | 修改本节 → 同步 `hubkit/` → 通知两侧跟进 → 全量回归 |

---

## 10. 任务通用契约

所有任务共用（标准结构模板：`task/_template/`）：

- **`skill.yaml`**

  | 键 | 必填 | 说明 |
  |---|---|---|
  | `name` / `description` | ✅ | 任务标识与一句话目标 |
  | `read_dirs` / `read_files` | `read_dirs` ✅ | bash 与 read 的可读路径；`read_dirs` **必须显式声明，缺失即拒绝启动**（§7.4） |
  | `required_env` / `optional_env` | ✅ | preflight 校验 required；两者都进 `AGENT_SCRIPT_ENV_JSON` |
  | `scripts` | ✅ | 脚本名 → 参数 JSON Schema，供 `path-guard` 校验 bash 调用并与 argparse 对齐 |

- **`SKILL.md`**（Agent Skills 标准）：目标 → **预算与停止规则** → 流程步骤 → 工具用法 →
  边界与禁止事项 → 何时问人 → 完成标准（Done 的定义）。配套 `SKILL_CN.md`（§18.2）。
- **`scripts` 规范**
  - argparse CLI；`--json` 输出机器可读结果。
  - exit code 语义：`0` 成功 / `2` needs_human（附原因）/ `3` fatal。
  - 不 import `agent/` 内核、任务之间互不 import；**契约级逻辑一律上收顶层 `hubkit/`**
    （schema 常量 / hub 读接口 / 校验器 / 渲染纯函数），任务脚本退化为薄壳
    （参数解析、exit code、`runs/` 落盘、网络限流与缓存）。
  - 网络类脚本内建限流与缓存（额度见 §11）。
  - stdout 预算：超长输出截断（摘要 ≤4k chars），全文落 `runs/` 供 `read` 追查。
- **工作区**：`runs/<ts>/<task>/<slug>/`（`AGENT_RUN_DIR`，产物与逐篇结果）。
  **任务无状态**：跨 run 不保存业务状态——没有 candidate/ledger 类持久工作区，
  去重等业务判断一律以 hub 本身为准（§11）。HTTP 缓存 `runs/.cache/ingest/`
  纯内容寻址、无业务语义，跨 run 复用安全。

---

## 11. 抽象层：检索接口（网络型任务共用）

### 线索与证据两级分离

防检索噪声污染结论的核心机制：

```
第一级：线索（leads）——广、可以脏
    web_search 返回 URL+摘要，agent 挑候选
    ↓ 候选进入取证
第二级：证据（evidence）——窄、必须权威
    官方性判定只认结构化数据（Semantic Scholar / arXiv / 论文页 / gh api）
    web_search 结果永不作为判定依据
```

### 网络脚本模板（所有网络类脚本遵守）

| 通道 | 限流 | 韧性 |
|---|---|---|
| Semantic Scholar Graph API | 1 req/s（匿名档） | 缓存；不够加免费 key |
| arXiv Atom API | ≥3s（官方政策） | 缓存 |
| DuckDuckGo（ddgs） | 3s 间隔 + 退避 | 失败返回 `web_search_unavailable`，降级 gh api；**仅线索级** |
| `gh api`（已认证） | 滑动窗口 30 req/min | 探针化（readme 互认 / tree 计数） |
| 任意网页（httpx + Mozilla UA） | 1 req/2s/域名 | 每 slug 缓存，agent 读缓存解析 |

### 种子分流接口

`scholar_lookup` 接受任意种子形态——`arxiv:ID` / `doi:...` / `pmid:...` / URL
（S2 匹配失败落 `fetch_page`）/ 纯标题（S2 title search）。

### 仓库发现公理（ingest 领域事实）

若论文有开源仓库，**链接必出现在论文全文中**。`extract_repo_links` 对已下载 PDF 做
确定性提取——github / gitlab / huggingface / zenodo / gitee 等仓库域 URL 清单
+ Code Availability 段落原文 + 全文缓存；**检索不出 → 确定性 `none`（终态，证据即"全文无仓库链接"），
LLM 不得推翻**。检索出的链接清单交 agent 挑选官方候选，定性仍走下方 probe 判定。

### 官方性判定规则（固化在 `github_search --probe`，非 LLM 判定）

| 证据 | 采集方式 | 权重 |
|---|---|---|
| 论文 Code Availability 声明 | `fetch_page(paper_url, want="code_availability")` | 强 |
| repo README 互认（引用论文标题 / arXiv id） | `github_search` readme 通道 | 强 |
| 作者匹配（论文作者 ↔ owner login/用户名） | `scholar_lookup` 作者 + `gh api users` | 强 |
| 元数据旁证（description / 创建日期 / 非 fork） | `gh api` | 弱 |

```
CodeAvailability ∧ README互认 或 README互认 ∧ 作者匹配 → official
仅作者匹配                                            → author_maintained
单项强                                                → likely（强制 ask_user / pending，不得静默入库）
```

### 去重主键：仓库地址（2026-09-29 决策）

入库去重以**归一化仓库地址 `owner/repo`（小写）**为主键——目标物是仓库，仓库地址变了
即视为另一篇论文，不用 DOI/标题模糊匹配。GitHub 改名后旧地址会重定向：probe 经 `gh api`
`meta.full_name` 取 **canonical 全名**为键，并输出 `renamed_from` 标注；
预印本与正式版共享同一仓库 → 自然合并。`model_name` 撞已入库条目 → **needs_human**
（唯一身份类人工闸，绝不自动合并）。

接口：`hub_query --repo <url|owner/repo>`（是否已入库）/ `--model <name>`（撞名检查）/
`--list --json`（紧凑名单）。归一化实现在 `hubkit/readers.py`（纯文本，零网络），
CLI 薄壳 `task/ingest/scripts/hub_query.py`。

### 特殊通道内嵌验证（历史教训固化进脚本）

- HF URL → HF model card 验证。
- `.py == 0 && .gitmodules 存在` → `shell_repo`，列子模块要求人工确认（AIDO.Cell 教训）。
- README 指向 PyPI 而 GitHub 空壳 → sdist 兜底、license 以包内 LICENSE 为准（scPRINT 教训）。

### PDF 获取多源链模板

```
S2 openAccessPdf → arXiv /pdf/ → citation_pdf_url → Unpaywall → pdf_needs_manual
```

落地校验 `%PDF` magic + ≥50KB。

---

## 12. 抽象层：验证分级（NPU 适配类任务共用）

**状态：未实现（M3）** —— 本节是已确定的设计，实现待 M3。

### 双 Python 环境分离（硬约束）

| 环境 | 用途 | 说明 |
|---|---|---|
| 项目 `.venv`（3.12） | agent 主进程（pi + task 脚本） | **不 import torch** |
| `NPU_PYTHON=/work/nvidia_ascend/.venv/bin/python`（3.10 + `torch_npu`） | 验证子进程 | 由验证脚本 `subprocess` 拉起 |

生成代码须 **≤3.10 语法**（SKILL.md 指令 + 静态校验按 3.10 执行）。

### 分级验证（彼此正交，缺基础设施自动 skip 带原因）

| 级别 | 内容 | 执行者 |
|---|---|---|
| L0 静态 | `py_compile` 全部 + AST import 白名单 + diff 哨兵（**永远跑**） | `validate_static`（主进程） |
| L1 CPU 导入 | 仅 import `entry_points` + `model_def`；hard deps 用 `sys.modules` stub 注入 | `validate_cpu`（`NPU_PYTHON` 子进程） |
| L2 NPU smoke | 能力探针通过才跑：小配置实例化 → 单次 forward → `.npu()`；需预训练权重的 → skipped 不算失败 | `validate_npu`（`NPU_PYTHON` 子进程） |
| L3 KernelGYM | 算子级评测（默认关，显式启用） | `kernel_gym`（HTTP） |

状态判定：L0/L1 fail → `failed`；L2 skip → `done_with_warnings`。

### 状态机模板

```
pending → scanned → analyzed → planned → transformed → validated → done
```

`skipped_incomplete` 为 preflight 终态；阶段重试耗尽 → `failed`（记 `failing_stage`）。

### 防幻觉护栏模板（LLM 改写强制项，静态校验执行）

1. `ast.parse` 强制通过（改写产物必须是合法 Python）。
2. import 集合 ⊆ 原文件 ∪ 白名单（`torch_npu` 等）。
3. diff 规模哨兵：非目标文件 >50% 行变更 → 拒绝，回修 1 次仍败 → 保留确定性结果标 `degraded`。

### 上下文预算模板（agent 读码/生成预算，写入 SKILL.md）

| 环节 | 预算 |
|---|---|
| 选文件 | ≤4k |
| 逐文件读 | ≤12k / 文件 |
| 合成 model_card | ≤16k in / ≤4k out |
| 计划生成 | ≤24k in / ≤6k out |
| 单文件改写 | ≤14k in / ≤8k out |

不整仓喂入；AST 按 class/function 边界切块，相关性预筛。

---

## 13. 下游任务实例化

### 13.1 task/ingest（hub 唯一写入方）——**已实现（M2）**

目标：**discover**——给定种子（论文 URL / arXiv ID / DOI / 标题）或在边界内自主发现
单细胞论文，检索定位论文与官方仓库，产出符合 hub 契约的完整入库产物；
**audit**——复核 hub 异常条目。两条流程在 stage/apply 汇合，校验全绿后给出
**人工 git 步骤清单**。agent 不执行 git 提交。
**任务无状态**：无 candidate / ledger，条目材料与产物都在 run 目录下，身份与去重判断以 hub 为准。

#### `skill.yaml`（12 个 agent 入口）

```yaml
name: ingest
description: 论文检索入库（discover 发现 / audit 复核），按 single-cell-hub 契约产出条目
read_dirs: [task, runs, single-cell-hub, docs]
read_files: [AGENTS.md, README.md, pyproject.toml, main.py, .env.example, .python-version]
required_env: [INGEST_MODE]            # discover | audit
optional_env:
  - INGEST_SEED_URL                    # 种子：doi:/arxiv:/URL/标题；audit 下为指定单点复核
  - INGEST_MAX_NEW                     # discover：单 run 新条目数量上限
  - INGEST_YEAR_FROM / INGEST_YEAR_TO  # discover：年份窗口（如 2024 / 2025）
  - INGEST_MODEL_NAME / INGEST_VENUE / INGEST_YEAR
  - INGEST_APPLY_AUTHORIZED            # =1 时 apply_entry 才允许落位
scripts:
  hub_query:          {args: {repo: str?, model: str?, list: bool?}}   # 仓库主键去重 + model_name 撞名检查
  scholar_lookup:     {args: {seed: str, limit: int?}}
  search_arxiv:       {args: {query: str, limit: int?}}
  web_search:         {args: {query: str, limit: int?}}
  github_search:      {args: {query: str, probe: bool?, title: str?, arxiv_id: str?, authors: str?, code_availability: str?}}
  fetch_page:         {args: {url: str, want: str?}}   # want: code_availability | metadata | full
  download_pdf:       {args: {paper_url: str, arxiv_id: str?, doi: str?, s2_pdf: str?, citation_pdf_url: str?}}
  extract_repo_links: {args: {}}                       # PDF 全文挖仓库链接（确定性 none 证据）
  acquire_repo:       {args: {repo_url: str}}
  stage_entry:        {args: {payload: str}}           # 条目字段 JSON；材料取 run 目录 paper/ repo/
  apply_entry:        {args: {confirm: bool?}}
  audit_scan:         {args: {model: str?}}            # 只读 hub 契约扫描
```

#### 两模式流程（SKILL.md 编排）

```text
discover 入口（六步，逐篇）              audit 入口
① dedup     hub_query --repo 查重；      ⓪ audit_scan [model] 只读扫描 hub 契约异常
            staged 前 --model 撞名检查  ① 对异常条目按 discover ②③ 重查；
            （撞名 → needs_human）              INGEST_MODEL_NAME 指定时单点复核；
② resolve   scholar_lookup 解析种子/自主         正常条目仅确定性校验，不跑 LLM
            发现（数量、年份窗口护栏），      ②③ 同 discover
            定位论文与条目字段
③ acquire   download_pdf + extract_repo_links
            + github_search --probe（官方性，含 renamed_from 检测）
            + acquire_repo（三通道）
④ stage     stage_entry --payload：按 hubkit 契约渲染完整待写文件并校验
⑤ validate  按校验报告回修，至多 2 轮
⑥ apply     apply_entry --confirm（校验全绿 + 授权前置）落位；
            产出验收单 report.md：产物清单 + 人工 git 步骤
```

#### 去重与身份（取代台账）

无 ledger：过程状态不落盘，**hub 本身就是唯一事实源**。去重主键 = 归一化仓库地址
`owner/repo`（§11）：地址变了即另一篇论文；预印本↔正式版共享仓库自然合并；
`model_name` 撞名 → needs_human。确定性 `none` 证据（全文无仓库链接）由
`extract_repo_links` 产出，仅约束当次流程，不持久登记——同仓库再次出现时 `hub_query` 命中即止。

#### 工作区

```text
runs/<ts>/ingest/<slug>/         # AGENT_RUN_DIR，launcher 创建
├── paper/                       # 下载的 PDF（落地校验 %PDF + ≥50KB）
├── repo/                        # 剥 .git 的源码快照
├── cache/                       # 全文抽取缓存
├── staged/                      # 完整待写文件 + summary.json（含校验结果）
├── ingest.json                  # 逐条目结果（脚本按真实结果写入）
├── task_result.json             # 任务结果协议（applied 达标才 done）
└── report.md                    # 验收单
runs/.cache/ingest/              # HTTP 缓存（纯内容寻址，跨 run 复用）
```

`staged/` 布局：`staged/README.md`、`staged/.gitignore`、
`staged/single_cell_models/{README.md,models.csv,<Name>/README.md}` 和 `summary.json`。
PDF / repo 引用 run 目录材料，不额外保存长期副本。**旧版 staged 必须重新生成。**

#### 脚本职责要点

- `hub_query.py`：仓库主键去重查询（`--repo` / `--model` / `--list`），逻辑在 `hubkit/readers.py`。
- `scholar_lookup` / `search_arxiv` / `web_search` / `github_search` / `fetch_page` /
  `download_pdf` / `extract_repo_links` / `acquire_repo`：检索、取证、判定和采集；
  证据上下文经 CLI 参数传入（无状态），PDF 落 `paper/`，快照落 `repo/`。
- `stage_entry.py --payload '<json>'`：校验条目字段（九个必需字段；仅 `official` /
  `author_maintained` 可入库），一次渲染完整待写文件，构建临时 hub 视图并调用统一校验器，
  产物落 `staged/`。
- `apply_entry.py --confirm`：确认 hub 基线、材料及 staged 均未改变；校验并应用同一份内容，
  失败恢复索引和条目。
- `audit_scan.py`：复用 `hubkit` 读取/校验的只读扫描。
- `validate_hub.py`：维护用校验入口，**不列入 agent 的 ingest 脚本白名单**。
- 内部模块 `_state.py`（run 工作区/结果落盘）、`_net.py`（网络/限流/缓存）、
  `_entry.py`（暂存/落位/回滚）**不提供 CLI、不进入白名单**。
- `hubkit` 只接收条目字段、现有索引和材料路径，不依赖 run 目录布局；**不写正式 hub**。
- 脚本按真实结果更新 run 内 `ingest.json` / `task_result.json` / `report.md`；
  只有实际应用达到 `INGEST_MAX_NEW` 才标记 `done`。

#### 人工决策点实例化

| 决策点 | 交互模式（`--interactive`） | 非交互（默认） |
|---|---|---|
| `model_name` 撞已入库条目 | `ask_user` 定夺身份 | needs_human：pending，不得入库 |
| 官方性 `likely` | `ask_user` 确认或否决 | pending：`official_needs_review` |
| 多候选仓库 | `ask_user` 列选项 | pending：列出候选 + 各自证据 |
| PDF 拿不到 | `ask_user` 给本地路径 | pending：`pdf_needs_manual` |
| 壳仓库/子模块 | `ask_user` 确认子模块清单 | pending：`repo_needs_review` |
| apply 落位前 | 已明确授权则执行，否则 `ask_user` | 无明确授权则保持 staged + 验收单 |
| audit 异常条目复核 | `ask_user` 逐条确认处置 | 重查后仍异常 → pending |

#### 完成标准

`stage_entry` 全绿（或 needs_human 状态明确且产物完整）；`report.md` 完整
（产物清单、人工 git 步骤）；触碰过的每篇论文要么 `applied`、要么有明确 pending 原因；
hub 侧无半成品状态（staged 完整 / 已 apply / 明确 pending 三选一）。

#### 增量入库与存量异常

`stage_entry` 与 `apply_entry` 仍校验全局 CSV schema 与双 README 镜像，并**完整校验本次目标条目**；
与目标无关的存量 PDF/源码缺陷由 `audit_scan` 报告，**不阻断新条目入库**——
避免既有坏条目使所有后续合法入库不可执行。

---

### 13.2 task/optimize（hub 纯读方，零网络）

**状态：未实现（M3）**

目标（已定）：对 hub 内模型快照做**推理侧**昇腾适配：读码分析 → 适配计划 →
确定性改写 + LLM 增强 → 分级验证 → 报告。支持指定单模型或批量遍历。

#### 已定的设计

**`skill.yaml`（8 脚本）**

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

**语料事实（实测，写入 SKILL.md）**

- 19/20 PyTorch；**CellFM 是 MindSpore**（只出分析 + 人工迁移指引，不自动转换）。
- **GenePT / AIDO.Cell 无代码**（0 个 `.py`）→ preflight 跳过，终态 `skipped_incomplete`。
- 痛点在依赖栈：flash-attn（scGPT/scPRINT）、RAPIDS（CellPLM）、faiss-gpu（SATURN）、
  bitsandbytes（Geneformer）；Performer/cosformer/flowformer 线性注意力可用标准 matmul 重写。
- **scMulan 自带 `scMulan_npu.py`**（torch_npu 写法）——语料内现成正确迁移参考。
- MVP 首选 **UCE**：10 个 py 文件，`model.py` 仅 115 行，纯标准 PyTorch。
- NPU 环境：`/work/nvidia_ascend/.venv`（Python 3.10 + torch 2.7.1 + torch_npu 2.7.1.post2）。
- KernelGYM：`/work/AscendKernelBench` 起 `127.0.0.1:8082`，`POST /evaluate` 返回
  compiled / correctness / speedup（L3 可选）。

**七步流程（SKILL.md 编排）**

```text
⓪ preflight  scan_repo 顺带检查：0 个 .py → skipped_incomplete 终态（不进 LLM 分析）
① scan        scan_repo：AST 静态扫描（无 LLM）→ 文件树/imports/classes/role/hard_deps/相关性预评分
② analyze     agent 分级读码（read 工具读 scan 标注的关键文件）→ model_card + 适配计划 JSON
③ plan_check  计划后校验：action.path 必须存在于 snapshot（防幻觉）；drop/stub 仅限非入口文件；
              CellFM → 只允许 analysis 类 action
④ transform   transform_rules 确定性打底（整树拷贝 + .cuda()→.npu() + import torch_npu 注入
              try/except + 依赖降级映射 cuml→sklearn、faiss→sklearn.neighbors）
              → agent 对 needs_llm 文件整文件重写（≤12 个/模型），护栏强制（§12 模板）
⑤ validate    分级验证（§12 L0–L3），失败只记录不改代码
⑥ report      render_report 模板化报告（无 LLM）
```

**产物（`runs/<ts>/optimize/<Model>/`，批量模式共用 run 目录）**

```text
scan/            # file_index.json
analysis/        # model_card.json + plan.json
transformed/     # repo/ + changes.diff + patch_manifest.json（逐文件标注来源：rules | llm）
validation/      # L0-L3 各级结果 JSON
report.md
state.json       # 状态机 + 断点续跑
```

**人工决策点实例化**

| 决策点 | 交互模式（`--interactive`） | 非交互（默认） |
|---|---|---|
| 计划含高风险 drop/stub | `ask_user` 确认 | pending：降级为保守计划（不 drop，仅标注） |
| L1 stub 注入仍失败 | `ask_user` 选择继续/终止 | 记 `failed`，报告说明 |
| apply 类操作 | 不适用本任务——`transformed` 产物留 `runs/`，人工审后自行取用 | — |

**完成标准**：终态明确（`done` / `done_with_warnings` / `failed` / `skipped_incomplete` 之一）；
`report.md` 完整（状态、依赖矩阵、diff 摘要、验证各级结果与跳过原因）；批量模式
`summary.md` 聚合 20 模型状态矩阵 + token 消耗。

#### 待定（TBD）

| # | 待定项 | 说明 |
|---|---|---|
| T13.2-1 | 落地顺序与首个 MVP 模型确认 | 设计首选 UCE，尚未实跑 |
| T13.2-2 | `task/optimize/` 目录与 `skill.yaml` 实体 | 尚未创建 |
| T13.2-3 | 模型代码执行的隔离策略 | §7.1 已声明"必须另行实现"，方案未定 |
| T13.2-4 | `state.json` 断点续跑的校验粒度 | 仅设计，未实现 |
| T13.2-5 | L3 KernelGYM 的启用条件与配额 | 仅设计，未实现 |

---

## 14. 运营

### 14.1 批处理

**状态：未实现（M4）**。已定的接口形态：

```
uv run ok batch --task optimize [--models UCE,scGPT ...] [--fresh]
```

1. 读 `models.csv` 得模型清单（可 `--models` 过滤）。
2. 逐模型拉起独立 pi session（`--mode json`，`--name` 标记），**串行**。
3. 每模型开始前查该 slug 的 `state.json`——终态即跳过（断点续跑）。
4. 失败不中断批次；结束输出状态矩阵 + pending 清单汇总。

### 14.2 pending

**状态：未实现（M4）**。`uv run ok pending` 列出跨 run 的待人工项（needs_human 记录），
处理后重跑对应 slug 形成闭环。

### 14.3 里程碑与实现状态

| 里程碑 | 内容 | 验收 | 状态 |
|---|---|---|---|
| M0 runtime 打通 | pi 安装（pin 版本）；`models.json` + envguard；显式装配（`--skill` + `-e`）；`path-guard` / `ask-user` 两个扩展；launcher `run` + 一个 hello task | `run --task hello` 输出思考与工具轨迹；journal 落盘；env 白名单生效 | ✅ 已完成 |
| M0.5 pi 自包含 | pi 入 vendor（`package-lock` pin 0.87.0）；`ok setup` 一键装；`bootstrap-guard` 禁直跑；不依赖全局安装与 PATH | 全新 clone 后 `uv sync && uv run ok setup && uv run ok run --task hello` 走通；裸调 vendor pi 无令牌被拒 | ✅ 已完成 |
| M1 ingest 脚本层 | `task/ingest` 全部脚本 + hub 契约（§9） | `validate_hub.py --hub` 对现有 20 模型全绿；脚本可独立单测 | ✅ 已完成（报 5 处存量缺陷，见 §17） |
| M2 ingest 端到端 | SKILL.md 编排 + LLM 搜索循环 + 降级路径 | 真实论文（arXiv ID）产出入库产物 + 验收单（不 commit） | ✅ 已完成 |
| M3 optimize MVP | `task/optimize` 脚本 + SKILL.md，跑 UCE | `transformed` 树通过 L0/L1，report 完整 | ⬜ **未实现** |
| M4 批量与无人值守 | batch driver + pending 流程 | 20 模型批量状态矩阵；needs_human 项事后补跑闭环 | ⬜ **未实现** |
| M5 硬化（新增，未排期） | §17 开放问题收敛；优化循环治理 | 见 §17 | ⬜ 未排期 |

### 14.4 端到端验证方式

1. **M0/M0.5**：`uv sync && uv run ok setup`，然后 `uv run ok run --task hello --set FOO=bar`
   —— 轨迹输出、journal 落盘、env 注入与冻结生效；尝试让 agent 改 `.env` 被 `path-guard` 拒绝；
   裸调 vendor pi 无令牌被 `bootstrap-guard` 拒绝。
2. **M1**：`uv run python task/ingest/scripts/validate_hub.py --hub` 现有 20 模型全绿。
3. **M2**：`run --task ingest --set INGEST_SEED_URL=<arXiv 链接>` 端到端产物检查
   （目录三件套 / CSV 行 / 双 README diff / 验收单）。
4. **M3**：`run --task optimize --set OPTIMIZE_MODEL_NAME=UCE` L0/L1 通过、报告完整。（未实现）
5. **M4**：batch 中断重跑，断点续跑正常；pending 补跑闭环。（未实现）

---

## 15. 测试与质量门禁

### 15.1 四层门禁

| 层 | 触发 | 内容 |
|---|---|---|
| ① 本地 pre-commit | `git commit`（`.pre-commit-config.yaml`） | `ruff check` / `ruff format` / `pyright` / `pytest` |
| ② CI（GitHub Actions） | push + PR（`.github/workflows/ci.yml`） | `uv sync --frozen` → `ruff check` → `ruff format --check` → `setup-node(22)` → `pyright` → `pytest` → `agent.bootstrap` → `node --test tests/agent/*.mjs` |
| ③ ruff 规则集 | `pyproject.toml` | `E W F I B C4 SIM RUF S FURB TRY DTZ EXE`；ignore `E501 RUF001-003 TRY003` + `tests/`、`task/` 的 per-file-ignores |
| ④ pyright | 同 ② | `pyright --pythonpath .venv/bin/python agent hubkit task`，要求 0 error |

**CI 步骤顺序的两个硬约束**：

1. `setup-node` 必须在 pyright 之前——pyright 本身是 JS 实现。
2. `agent.bootstrap` 必须在 node 测试之前——`tests/agent/*.mjs` 从
   `agent/vendor/node_modules` 导入 pi runtime，而该目录是 gitignore 的（由 bootstrap 生成），
   干净检出下不先 bootstrap 会直接 import 失败。

### 15.2 测试清单与现状

| 文件 | 覆盖 | 用例数 |
|---|---|---|
| `tests/agent/test_command_exec.mjs` | **执行级**：真跑 `planCommand` + `executePlan`，断言输出（grep 命中 / `-c` 计数 / find 列文件 / `head -c` 字节数 / 配置错误抛错） | 18 |
| `tests/agent/test_policy.mjs` | 命令规划：注入与元字符拒绝、脚本白名单、路径边界、符号链接、可读声明、执行器环境 | 36 |
| `tests/agent/test_budget.mjs` | 预算与重复信号：交错重试被拦、换命令放行、成功不计数、预算兜底 | 4 |
| `tests/agent/test_guard.mjs` | bootstrap-guard 令牌校验 | 1 |
| `tests/agent/test_interaction.mjs` | `ask_user` 仅在交互模式注册 | 1 |
| `tests/agent/*.py` | launcher / assembly / envguard / journal / results / bootstrap / cmd_run / interaction | 见 pytest 汇总 |
| `tests/hubkit/` `tests/task/` | hub 契约校验与渲染、ingest 脚本解析 | 见 pytest 汇总 |

**当前结果（2026-09-30）**：Node **60 通过 / 0 失败**；Python **272 通过（25 subtests）**；
pyright **0 error**；`ruff check` **clean**、`ruff format --check` **clean**。

### 15.3 可移植性铁律

**本地 dev = aarch64，GitHub runner = x86_64。** 任何由 `platform.machine()` 派生的
路径或校验值，测试必须走 `bootstrap._arch()`，**禁止写死 `arm64` / `x86_64`**。
（此坑真实挂了 2 轮 CI。）

### 15.4 测试方法论教训（本次新增，值得长期保留）

**只断言 argv 形状的测试，等于没有测试。**

`test_policy.mjs` 早期只断言 `args.at(-1)` 与 `args.includes("--")`。于是两个真实缺陷
在 **42 个全绿测试**下全部溜过：

| 缺陷 | 形状断言为何看不出 | 后果 |
|---|---|---|
| `grep` 丢失模式串 | 最后一个参数确实是那个路径，`--` 也确实存在 | **静默假阴性**：有匹配却报 0 |
| `find` 表达式排在路径前 | 语法错误只在运行时报 | 带谓词的 find 全废 |

因此新增 `tests/agent/test_command_exec.mjs`：**真实执行**并断言输出内容。
规则：**凡是"生成的命令"，都要有一条用例证明它真的能跑出预期结果。**

### 15.5 本机验证命令

```bash
# 服务器（/work/ok-optim-agent）
uvx ruff check . && uvx ruff format --check .
uvx pyright --pythonpath .venv/bin/python agent hubkit task
.venv/bin/python -m pytest tests/ -q
uv run python -m agent.bootstrap            # 生成 gitignore 的 agent/vendor/node_modules
node --test tests/agent/*.mjs

# 若 node 不在非交互 PATH（本环境实测）：
NB=agent/vendor/node-v22.23.2-linux-arm64/bin
PATH=$NB:$PATH node --test tests/agent/*.mjs
PATH=$NB:$PATH uvx pyright --pythonpath .venv/bin/python agent hubkit task
```

> 注意：`| tail -N` 会吞掉退出码，校验必须显式取真实退出码（曾因此把"没装 pytest"
> 误报成 267 passed）。

---

## 16. 失败模式与风险

### 16.1 设计层风险与兜底

| 风险 | 兜底 |
|---|---|
| pi 版本演进破坏扩展 API | 精确 pin + `package-lock`；扩展面尽量小（4 个生产扩展 + 2 个守卫小文件），逻辑尽量留在 `agent/` py 层；仅用 pi 的公开注册机制与 hook，不手改 vendor；SKILL.md 标准保证 task 层可平移（dsh 备选） |
| 扩展初始化失败导致守卫消失 | §7.5：初始化错误不抛，改为"拒绝一切工具调用"的守卫 |
| npm 安装网络问题 | npmmirror 镜像 + SHA256 锁定；或离线分发 `node_modules` |
| LLM 幻觉脚本参数 | `path-guard` 校验命令结构与 `skill.yaml` 脚本声明；参数语义由脚本 argparse 严格校验 |
| agent 越界改文件 | `write`/`edit` 默认拒绝；bash 白名单；三层约束（§7.2） |
| 抽象与实现偏移 | 单文档主线 + `hubkit/` 可执行契约库（写方/读方共用同一实现）；`validate_hub --hub` 全量回归 |
| 脚本输出撑爆上下文 | 分发层截断 + 全文落 `runs/` 供 `read` 追查 |
| 端点不返回 `reasoning_content` | json 模式事件流天然兼容：有则显示思考块，无则只显示 content + tool calls |
| token 成本失控 | skill 渐进披露不破坏 prompt cache；串行执行 + usage 记账 + `budget-guard` 强制收尾 |
| 模型重复循环 | temperature 0.4 + frequency_penalty（§6.2）+ `budget-guard` 双信号（§8.1） |
| 模型在任何脚本前被预算杀掉 | `journal` 合成 `skipped_incomplete` 兜底（§8.2） |

### 16.2 已观测到的真实失败分布

来自 8 个 `runs/*/events.jsonl`、199 次 bash 调用：

| 现象 | 量级 | 本质 |
|---|---|---|
| `uv run python task/ingest/scripts/*.py` 业务失败 | 多为 arXiv 406 / S2 429 / PDF 403 / 超时 | **脚本业务失败**，不是守卫误伤 |
| `cat .ingest/...` 被守卫拒绝 | `.ingest` 出现在 **67 次**调用中 | **提示词/契约不一致**（见 §17.1） |
| `scholar_lookup` 撞 S2 限流 | 单 run 内 9 次，交错重试 | 护栏漏抓（已由 §8.1 第二信号覆盖） |
| 一次 run 被预算杀掉 | 63 次工具调用后 `Tool budget exhausted (maximum 60 requests)` | 收尾缺失（已由 §8.2 兜底） |
| 命令拼接攻击 | **0 次** | 说明"字符集白名单"防的是事故而非对抗 |

### 16.3 明确记录的两类"看起来像 bug 其实不是"

- **`cat .env` 被拒绝是正确的**——不是守卫误伤，而是它本来就该被拒。
- **`find task -delete` 曾经"安全"是碰巧的**——被 `--` 语法错误挡住，不可依赖（§7.3）。

---

## 17. 开放问题与已知缺口

### 17.1 `.ingest/` 与 `AGENT_RUN_DIR` 的提示词不一致（优先级高）

- **事实**：真实 runs 里有 **67 次调用**在读写 `.ingest/`（`cat .ingest/candidates/...`、
  `ls -la .ingest/candidates/...`）；而 `.ingest/` 既被 `.gitignore` 忽略、也不在任何 task 的
  `read_dirs` 里，`task/ingest/SKILL.md` 规定产物应写在 `AGENT_RUN_DIR`（`runs/<ts>/...`）。
- **定性**：这是**提示词引导错误**，不是路径白名单缺陷。
- **修法**：修 `SKILL.md`（及 `SKILL_CN.md`）的产物路径表述。**优先级高于继续放宽白名单**。
- **状态**：未修。

### 17.2 其他已知缺口

| # | 缺口 | 影响 | 备注 |
|---|---|---|---|
| Q1 | `docs/architecture.md` 无中文版 | 中文读者需读英文/对照 | 与 `AGENTS*` / `SKILL*` / `system*` 的双版本约定不一致（§18.2） |
| Q2 | CI action 版本仍用弃用 Node 20 的 `checkout@v4` / `setup-node@v4` / `setup-uv@v5` | CI 有弃用告警 | 未排期 |
| Q3 | `read_dirs` 存在性只在 **harness 侧**（`resolveScope`）校验 | 配置错误在 pi 启动后才暴露，浪费一次会话 | 可加在 `assembly.preflight` 提前拦截 |
| Q4 | `validate_hub.py --hub` 对存量 20 模型报 5 处缺陷 | M1 验收口径需澄清"全绿"含义 | Geneformer PDF 缺失、GenePT/AIDO.Cell 无 `.py`、SCimilarity orphan |
| Q5 | §7.1 声明的"模型代码 OS 级隔离"方案未定 | 阻塞 M3 的 `validate_npu` 之外的能力 | 见 T13.2-3 |
| Q6 | 未启用的 `for` 循环类命令 | 唯一 1 次 `for` 调用被字符集白名单拒绝 | 当前设计视为正确行为（无 shell） |

---

## 18. 附录

### 18.1 术语表

| 术语 | 含义 |
|---|---|
| harness | agent 运行时（本项目的 harness = pi） |
| 守卫 / guard | 拦截工具调用、施加权限约束的 pi 扩展 |
| fail-closed | 配置或校验失败时**拒绝执行**，而不是放行或静默降级 |
| literal argv | 把命令行解析成参数数组后直接 `exec`，不经 shell 的输入语法 |
| 可读路径（read roots） | `skill.yaml` 声明的 `read_dirs` + `read_files`，`read`/`bash` 可访问的范围 |
| staged | 已渲染、已自校验、尚未落位 hub 的完整待写文件集合 |
| 验收单 | `report.md`：产物清单 + 人工 git 步骤 |
| run 目录 | `runs/<ts>/<task>/<slug>/`，一次运行的完整证据 |
| 通道（channel） | 一个网络数据源（S2 / arXiv / DDG / gh api / 网页） |
| 终态 | 该条目或该模型不再需要继续处理的状态（`done` / `failed` / `pending` 等） |

### 18.2 中英双语版本约定

项目对**面向模型与人工的说明性文档**维护中英双版本，命名后缀 `_CN`：

| 英文版 | 中文版 | 同步要求 |
|---|---|---|
| `AGENTS.md` | `AGENTS_CN.md` | 逐段对应 |
| `agent/prompts/system.md` | `agent/prompts/system_CN.md` | 逐段对应 |
| `task/<t>/SKILL.md` | `task/<t>/SKILL_CN.md` | 逐段对应，**改英文版后须同步**（`task/_template/README.md` 明确要求） |
| `README.md` | — | 仅中文 |
| `docs/architecture.md` | — | 仅中文（Q1） |

**收尾自检**：`git diff --name-only` 里每个 `.md`，问一句"它有没有 `_CN` 兄弟"。
`SKILL.md` 与 `SKILL_CN.md` 的章节标题应保持**行号级对齐**，便于人工对照。

### 18.3 参考

- pi：https://pi.dev （`@earendil-works/pi-coding-agent`，pin 0.87.0）
- Agent Skills 标准：agentskills.io
- hub 数据集：`single-cell-hub`（git submodule）
- 项目内相关文档：`README.md`（面向使用者）、`AGENTS.md`（面向 agent 的全局边界）、
  `task/_template/README.md`（新建任务清单）、`configs/ingest.profiles.example.yml`（运行方案模板）
