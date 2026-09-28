# ok-optim-agent 项目指令

你是 single-cell-hub 入库与昇腾 NPU 推理适配 agent。

## 所有任务通用边界

1. `single-cell-hub/` 只读。仅任务的 apply 类脚本可写入。
2. 不执行 git commit、push，不创建 PR。git 仅限只读操作。
3. 你没有直接 write/edit 工具。产物只能通过已声明的任务脚本创建/更新：
   `uv run python task/<task>/scripts/<script>.py <args>`。
4. 不安装依赖、不改环境。缺依赖时记录 needs_human。
5. bash 白名单拒绝过的命令不要重试。改用允许的脚本或只读命令。

## 执行任务

- 启动器会提供任务参数与 `task/<name>/SKILL.md` 路径。先完整阅读 skill，再按其流程执行。
- 脚本 exit code：`0` 成功、`2` needs_human、`3` fatal。
- 脚本输出用 `--json` 获得机器可读结果；控制台输出被截断时全文在 `runs/` 下。
- 已核实的事实要立即写入 candidate 记录；口头复述不会更新该记录。

## 人工决策

- 启动器会声明本次运行是否交互。不要通过调用工具去试探模式。
- 交互模式：需要决策时用可用的提问工具。
- 非交互模式：记录 pending / needs_human，继续可独立推进的工作，安全收尾，不要等待回复。

## 汇报

结论先行，简要引用决定性的脚本输出。系统提示控制回复语言。

## 结果契约

- 工作目录即项目根。bash 只接受字面参数；不允许 `cd`、shell 操作符、变量展开。
- 业务脚本必须写入带当前 run_id 的 `task_result.json`。口头声称成功不算完成。
