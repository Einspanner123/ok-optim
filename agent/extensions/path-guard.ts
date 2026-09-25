/**
 * Tool boundary: literal commands -> approved argv, canonical read paths,
 * declared scripts only, and no provider credentials in child processes.
 * This is a harness guard, not OS isolation for untrusted Python code.
 */
import { createBashToolDefinition, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { realpathSync } from "node:fs";
import { planCommand, readAllowed, scriptEnvironment, type Policy } from "./command-policy.ts";
import { executePlan } from "./safe-exec.ts";

export default function (pi: ExtensionAPI) {
  const root = realpathSync(process.env.AGENT_PROJECT_ROOT ?? process.cwd());
  const scripts: unknown = JSON.parse(process.env.AGENT_SCRIPTS_JSON ?? "[]");
  if (!Array.isArray(scripts) || scripts.some(s => typeof s !== "string"))
    throw new Error("path-guard: invalid script declaration");
  const policy: Policy = { root, task: process.env.AGENT_TASK ?? "", scripts };
  const bash = createBashToolDefinition(root, {
    exposeSessionEnvironment: false,
    operations: {
      exec: async (command, _cwd, options) => executePlan(
        planCommand(command, policy), root, scriptEnvironment(process.env), options,
      ),
    },
  });
  bash.description = "Run one literal command from the project root; no shell or cd. Allowed: declared uv run python task scripts; ls/cat/head/tail/wc on readable paths; pwd; literal echo; git status.";
  pi.registerTool(bash);
  pi.on("tool_call", async (event, ctx) => {
    const deny = (reason: string) => ({ block: true, reason: "path-guard: " + reason });
    if (event.toolName === "ask_user" && process.env.AGENT_INTERACTIVE === "1") return undefined;
    if (event.toolName === "read") {
      try {
        if (realpathSync(ctx.cwd) !== root) return deny("working directory differs from project root");
      } catch { return deny("invalid working directory"); }
      const path = (event.input as { path?: unknown }).path;
      return typeof path === "string" && readAllowed(root, path)
        ? undefined : deny("path outside readable roots");
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
