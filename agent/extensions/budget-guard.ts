/**
 * Count every model tool request before path-guard runs. Give actionable feedback
 * on repeated identical requests and on repeated *failing* requests, then block
 * all calls beyond budget.
 *
 * Two independent signals, because the runs data shows they catch different things:
 *   - identical repeats  : the model retrying the exact same call consecutively
 *   - failure repeats    : the same operation failing N times, no matter what
 *                          happens in between
 *
 * The second signal exists because the first one missed the dominant real pattern:
 * `scholar_lookup` hit S2 rate limiting 9 times in one run, interleaved with other
 * work, so the consecutive-identical counter reset to 1 on every single retry.
 *
 * The failure key is derived from the *call* (tool + normalized command shape), not
 * from the error text, because the check runs before the result exists. Failure
 * class is only used in the message, so the two sides of the comparison always match.
 */
import type { ExtensionAPI, ToolResultEvent } from "@earendil-works/pi-coding-agent";

const BUDGET = Number(process.env.AGENT_TOOL_BUDGET ?? 60) || 60;
const REPEAT_LIMIT = 2;
// Same operation failing this many times => refuse further attempts.
const FAILURE_LIMIT = Number(process.env.AGENT_FAILURE_LIMIT ?? 3) || 3;

/** Stable stringify so key order in the tool input cannot defeat the comparison. */
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

/**
 * Operation identity: the tool plus its command with volatile parts erased.
 * A retry against a different URL or a different repo is still the same operation
 * when the failure was environmental (rate limit, timeout, 5xx), which is exactly
 * the class of failure that made the model loop.
 */
function operationKey(toolName: string, input: Record<string, unknown>): string {
  const command = typeof input.command === "string" ? input.command : "";
  const shape = command
    .replace(/\bhttps?:\/\/\S+/g, "<url>")
    .replace(/\b[0-9a-f]{7,40}\b/g, "<id>")
    .replace(/\b\d{4,}\b/g, "<n>")
    .replace(/\s+/g, " ").trim().slice(0, 200);
  return JSON.stringify([toolName, shape]);
}

/** Short human phrasing of the last error, used only in the block message. */
function errorHint(content: unknown): string {
  const text = typeof content === "string" ? content : JSON.stringify(content ?? "");
  if (/\b429\b|rate.?limit|限流/i.test(text)) return "rate limited";
  if (/\b5\d\d\b/.test(text)) return "server error";
  if (/\b(403|406|404)\b/.test(text)) return "HTTP error";
  if (/timed? ?out|timeout|ETIMEDOUT/i.test(text)) return "timed out";
  return "failed";
}

export default function (pi: ExtensionAPI) {
  let count = 0;
  let previous = "";
  let consecutive = 0;
  // operationKey -> { count, hint } of observed failures this session
  const failures = new Map<string, { count: number; hint: string }>();

  pi.on("tool_call", async (event) => {
    count += 1;
    if (count > BUDGET) {
      return {
        block: true,
        reason: `Tool budget exhausted (maximum ${BUDGET} requests). Do not call more tools. `
          + "Report completed work, verified blockers, and remaining pending items, then stop.",
      };
    }

    const input = (event.input ?? {}) as Record<string, unknown>;
    const key = JSON.stringify([event.toolName, stable(input)]);
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

    const op = operationKey(event.toolName, input);
    const seen = failures.get(op);
    if (seen && seen.count >= FAILURE_LIMIT) {
      return {
        block: true,
        reason: `Blocked after ${seen.count} identical failures: this ${event.toolName} `
          + `operation has already ${seen.hint} ${seen.count} times. Retrying it again `
          + "will not produce a different result. Record the pending reason for this item "
          + "and move to another paper or channel, then report the blocker and stop.",
      };
    }
    return undefined;
  });

  pi.on("tool_result", async (event: ToolResultEvent) => {
    if (!event.isError) return undefined;
    const op = operationKey(event.toolName, (event.input ?? {}) as Record<string, unknown>);
    const seen = failures.get(op);
    failures.set(op, {
      count: (seen?.count ?? 0) + 1,
      hint: errorHint(event.content),
    });
    return undefined;
  });
}
