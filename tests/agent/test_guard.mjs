import assert from "node:assert/strict";
import test from "node:test";
import path from "node:path";
import { loadExtensions } from "../../agent/vendor/node_modules/@earendil-works/pi-coding-agent/dist/core/extensions/loader.js";

test("all attempts count and third identical call is blocked", async () => {
  const old = process.env.AGENT_TOOL_BUDGET;
  try {
    process.env.AGENT_TOOL_BUDGET = "5";
    const loaded = await loadExtensions(
      [path.resolve("agent/extensions/budget-guard.ts")], process.cwd());
    assert.deepEqual(loaded.errors, []);
    const handler = loaded.extensions[0].handlers.get("tool_call")[0];
    const call = (toolName, input) => handler({ toolName, input }, {});
    const input = { path: "task/ingest/SKILL.md" };
    assert.equal(await call("read", input), undefined);
    assert.equal(await call("read", { path: input.path }), undefined);
    const repeated = await call("read", input);
    assert.equal(repeated.block, true);
    assert.match(repeated.reason, /Repeated tool call blocked/);
    assert.equal(await call("read", { path: "AGENTS.md" }), undefined);
    assert.equal(await call("bash", { command: "pwd" }), undefined);
    const budget = await call("read", input);
    assert.equal(budget.block, true);
    assert.match(budget.reason, /Tool budget exhausted/);
  } finally {
    if (old === undefined) delete process.env.AGENT_TOOL_BUDGET;
    else process.env.AGENT_TOOL_BUDGET = old;
  }
});
