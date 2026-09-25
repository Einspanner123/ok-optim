/**
 * Count every model tool request before path-guard runs. Give actionable feedback
 * on the third consecutive identical request, and block all calls beyond budget.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { execFile } from "node:child_process";
import { resolve } from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const BUDGET = Number(process.env.AGENT_TOOL_BUDGET ?? 60) || 60;
const REPEAT_LIMIT = 2;

function stable(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).sort(([a], [b]) => a.localeCompare(b))
        .map(([key, item]) => [key, stable(item)]),
    );
  }
  return value;
}

function candidateSlug(input: unknown): string | undefined {
  const match = JSON.stringify(input ?? "").match(
    /\.ingest\/candidates\/([A-Za-z0-9][A-Za-z0-9._-]*)/,
  );
  return match?.[1];
}

async function statusFeedback(input: unknown): Promise<string> {
  const script = process.env.AGENT_STATUS_SCRIPT;
  const task = process.env.AGENT_TASK;
  const root = process.env.AGENT_PROJECT_ROOT;
  if (!script || !task || !root || !/^[A-Za-z0-9_-]+$/.test(script)
      || !/^[A-Za-z0-9_-]+$/.test(task)) return "";
  const file = resolve(root, "task", task, "scripts", script + ".py");
  const args = ["-B", "-E", "-s", file, "status"];
  const slug = candidateSlug(input);
  if (slug) args.push(slug);
  args.push("--json");
  try {
    const { stdout } = await execFileAsync(resolve(root, ".venv/bin/python"), args, {
      cwd: root, timeout: 5000, maxBuffer: 16384,
      env: { PATH: "/usr/bin:/bin", PYTHONDONTWRITEBYTECODE: "1" },
    });
    const state = JSON.parse(stdout);
    return " Task status: " + JSON.stringify(state);
  } catch {
    return " Task status could not be verified; do not infer the current phase.";
  }
}

export default function (pi: ExtensionAPI) {
  let count = 0;
  let previous = "";
  let consecutive = 0;
  pi.on("tool_call", async (event) => {
    count += 1;
    if (count > BUDGET) {
      return {
        block: true,
        reason: `Tool budget exhausted (maximum ${BUDGET} requests). Do not call more tools. `
          + "Report completed work, verified blockers, and remaining pending items, then stop.",
      };
    }
    const key = JSON.stringify([event.toolName, stable(event.input)]);
    consecutive = key === previous ? consecutive + 1 : 1;
    previous = key;
    if (consecutive > REPEAT_LIMIT) {
      return {
        block: true,
        reason: `Repeated tool call blocked: ${event.toolName} with identical arguments `
          + `was requested ${consecutive} times consecutively. `
          + "Use verified results to choose a different permitted action."
          + await statusFeedback(event.input),
      };
    }
    return undefined;
  });
}
