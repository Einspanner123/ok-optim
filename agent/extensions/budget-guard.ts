/**
 * Count every model tool request before path-guard runs. Give actionable feedback
 * on the third consecutive identical request, and block all calls beyond budget.
 * Tasks are stateless: there is no status entry to consult; the repeat-block
 * reason only tells the model to choose a different permitted action.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

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
          + "Use verified results to choose a different permitted action.",
      };
    }
    return undefined;
  });
}
