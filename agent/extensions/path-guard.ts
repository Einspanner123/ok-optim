/**
 * Tool boundary: literal commands -> approved argv, canonical read paths,
 * declared scripts only, and no provider credentials in child processes.
 * This is a harness guard, not OS isolation for untrusted Python code.
 */
import { createBashToolDefinition, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { realpathSync } from "node:fs";
import { planCommand, readAllowed, resolveScope, scriptEnvironment, type Policy } from "./command-policy.ts";
import { executePlan } from "./safe-exec.ts";

/** 由 skill.yaml 声明、经 launcher 注入的字符串列表。 */
function envList(name: string): string[] {
  const raw = process.env[name];
  if (raw === undefined) throw new Error(name + " is not set");
  const value: unknown = JSON.parse(raw);
  if (!Array.isArray(value) || value.some(v => typeof v !== "string"))
    throw new Error("invalid " + name);
  return value as string[];
}

function buildPolicy(): Policy {
  const scripts: unknown = JSON.parse(process.env.AGENT_SCRIPTS_JSON ?? "[]");
  if (!Array.isArray(scripts) || scripts.some(s => typeof s !== "string"))
    throw new Error("invalid script declaration");
  const policy: Policy = {
    root: realpathSync(process.env.AGENT_PROJECT_ROOT ?? process.cwd()),
    task: process.env.AGENT_TASK ?? "",
    scripts,
    readDirs: envList("AGENT_READ_DIRS_JSON"),
    readFiles: envList("AGENT_READ_FILES_JSON"),
  };
  if (!/^[A-Za-z0-9_-]+$/.test(policy.task)) throw new Error("missing or invalid task");
  // 声明里不存在的可读路径在这一步就暴露，而不是等到每次读取时降级成"无权限"。
  resolveScope(policy);
  return policy;
}

export default function (pi: ExtensionAPI) {
  // 初始化失败不能 throw：pi 的扩展加载器会把工厂异常降级成一条诊断信息后继续启动
  // （loader.js 的 loadExtension 捕获错误、返回 null 扩展），守卫会整个消失，
  // 模型反而拿到未经替换的 pi 原生 bash——那才是真正的 fail-open。
  // 因此这里改为安装一个"拒绝一切工具"的守卫，任何失败都收敛成拒绝。
  let policy: Policy;
  try {
    policy = buildPolicy();
  } catch (error) {
    const reason = "path-guard is not configured, refusing every tool call: " + String(error);
    pi.on("tool_call", async () => ({ block: true, reason }));
    return;
  }

  const root = policy.root;
  const bash = createBashToolDefinition(root, {
    exposeSessionEnvironment: false,
    operations: {
      exec: async (command, _cwd, options) => executePlan(
        planCommand(command, policy), root, scriptEnvironment(process.env), options,
      ),
    },
  });
  bash.description = "Run one literal command from the project root; no shell or cd. Allowed: declared uv run python task scripts; ls/cat/head/tail/wc/grep/find on readable paths; pwd; literal echo; git status.";
  pi.registerTool(bash);
  pi.on("tool_call", async (event, ctx) => {
    const deny = (reason: string) => ({ block: true, reason: "path-guard: " + reason });
    if (event.toolName === "ask_user" && process.env.AGENT_INTERACTIVE === "1") return undefined;
    if (event.toolName === "read") {
      try {
        if (realpathSync(ctx.cwd) !== root) return deny("working directory differs from project root");
        const path = (event.input as { path?: unknown }).path;
        return typeof path === "string" && readAllowed(policy, path)
          ? undefined : deny("path outside readable roots");
      } catch (error) { return deny(String(error)); }
    }
    if (event.toolName === "bash") {
      try {
        const command = (event.input as { command?: unknown }).command;
        if (typeof command !== "string") throw new Error("command must be a string");
        planCommand(command, policy);
        return undefined;
      } catch (error) { return deny(String(error)); }
    }
    return deny("tool disabled; use only the tools enabled for this run");
  });
}
