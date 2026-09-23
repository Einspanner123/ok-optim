/**
 * path-guard: harness 层沙盒（pi extension）。
 *
 * - write/edit: 一律拒绝（"LLM 不直接写盘"硬约束，产物写入只能经 task 脚本）
 * - bash: 白名单——当前任务的脚本（uv run python task/<task>/scripts/<x>.py）
 *   + 只读命令；拒绝命令连接符/重定向/替换
 * - read: 路径白名单内可读
 *
 * AGENT_TASK / AGENT_INTERACTIVE 由启动器 envguard 注入。
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

const TASK = process.env.AGENT_TASK ?? "";

// 只读命令白名单（整条命令前缀匹配）
const READONLY_CMDS: RegExp[] = [
  /^ls( |\n|$)/,
  /^cat( |\n|$)/,
  /^head( |\n|$)/,
  /^tail( |\n|$)/,
  /^wc( |\n|$)/,
  /^pwd\s*$/,
  /^echo( |\n|$)/,
  /^which( |\n|$)/,
  /^git (log|status|diff|show|ls-files)( |\n|$)/,
];

// read 工具可读路径前缀（相对项目根 resolve 后比较）
const READ_PATHS: string[] = [
  "task/",
  "runs/",
  "single-cell-hub/",
  "AGENTS.md",
  "README.md",
  "pyproject.toml",
  "main.py",
  ".env.example",
  ".python-version",
];

// 危险 shell 语法：命令连接/重定向/替换一律拒绝
const SHELL_META = /&&|\|\||;|\||>|<|`|\$\(|\n|\r/;

function underBase(cwd: string, rel: string): string {
  return resolve(cwd, rel);
}

function isReadAllowed(cwd: string, rawPath: string): boolean {
  const abs = resolve(cwd, rawPath);
  for (const prefix of READ_PATHS) {
    const base = underBase(cwd, prefix);
    if (abs === base || abs.startsWith(base + "/") || abs.startsWith(base)) {
      return true;
    }
  }
  return false;
}

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event, ctx) => {
    const tool = event.toolName;

    // ---- write/edit: 一律拒绝 ----
    if (tool === "write" || tool === "edit") {
      return {
        block: true,
        reason:
          `path-guard: ${tool} 工具被禁用（"LLM 不直接写盘"硬约束）。` +
          `产物写入只能通过运行当前任务的脚本来完成: ` +
          `uv run python task/${TASK || "<task>"}/scripts/<script>.py <args>`,
      };
    }

    // ---- read: 路径白名单 ----
    if (tool === "read") {
      const path = String((event.input as { path?: string }).path ?? "");
      if (!isReadAllowed(ctx.cwd, path)) {
        return {
          block: true,
          reason: `path-guard: 路径不在可读白名单内: ${path}`,
        };
      }
      return undefined;
    }

    // ---- bash: 命令白名单 ----
    if (tool === "bash") {
      const command = String(
        (event.input as { command?: string }).command ?? "",
      ).trim();

      if (SHELL_META.test(command)) {
        return {
          block: true,
          reason:
            "path-guard: 命令包含连接符/重定向/替换（&& | ; > < ` $() 换行），不允许。请执行单条命令。",
        };
      }

      // 形态: uv run python task/<task>/scripts/<script>.py [args...]
      const m = command.match(
        /^uv run python (task\/[A-Za-z0-9_-]+\/scripts\/[A-Za-z0-9_.-]+\.py)(\s.*)?$/,
      );
      if (m) {
        const scriptRel = m[1];
        // AGENT_TASK 未设定（手动调试）时放宽为任意 task 脚本
        if (TASK) {
          const expected = `task/${TASK}/scripts/`;
          if (!scriptRel.startsWith(expected)) {
            return {
              block: true,
              reason: `path-guard: 当前任务是 '${TASK}'，只能运行 ${expected} 下的脚本: ${scriptRel}`,
            };
          }
        }
        const scriptAbs = resolve(ctx.cwd, scriptRel);
        if (!existsSync(scriptAbs)) {
          return {
            block: true,
            reason: `path-guard: 脚本不存在: ${scriptRel}`,
          };
        }
        return undefined; // 参数由脚本自身 argparse 严格校验
      }

      // 只读命令
      for (const re of READONLY_CMDS) {
        if (re.test(command)) {
          return undefined;
        }
      }

      return {
        block: true,
        reason:
          `path-guard: 命令不在白名单内。允许: (1) uv run python task/${TASK || "<task>"}/scripts/<script>.py <args> ` +
          `(2) 只读命令 ls/cat/head/tail/wc/pwd/echo/which/git log|status|diff|show|ls-files。` +
          `收到: ${command.slice(0, 200)}`,
      };
    }

    return undefined;
  });
}
