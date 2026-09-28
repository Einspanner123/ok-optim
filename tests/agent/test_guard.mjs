import assert from "node:assert/strict";
import test from "node:test";
import path from "node:path";
import { loadExtensions } from "../../agent/vendor/node_modules/@earendil-works/pi-coding-agent/dist/core/extensions/loader.js";

test("all attempts count and third identical call gets status feedback", async () => {
  const old = Object.fromEntries(["AGENT_TOOL_BUDGET", "AGENT_STATUS_SCRIPT",
    "AGENT_TASK", "AGENT_PROJECT_ROOT"].map(k => [k, process.env[k]]));
  try {
    Object.assign(process.env, {
      AGENT_TOOL_BUDGET: "5", AGENT_STATUS_SCRIPT: "candidate",
      AGENT_TASK: "ingest", AGENT_PROJECT_ROOT: process.cwd(),
    });
    const loaded = await loadExtensions(
      [path.resolve("agent/extensions/budget-guard.ts")], process.cwd());
    assert.deepEqual(loaded.errors, []);
    const handler = loaded.extensions[0].handlers.get("tool_call")[0];
    const call = (toolName, input) => handler({ toolName, input }, {});
    const input = { path: ".ingest/candidates/cellvq/candidate.json" };
    assert.equal(await call("read", input), undefined);
    assert.equal(await call("read", { path: input.path }), undefined);
    const repeated = await call("read", input);
    assert.equal(repeated.block, true);
    assert.match(repeated.reason, /Repeated tool call blocked/);
    assert.match(repeated.reason, /candidate_missing/);
    assert.equal(await call("read", { path: "AGENTS.md" }), undefined);
    assert.equal(await call("bash", { command: "pwd" }), undefined);
    const budget = await call("read", input);
    assert.equal(budget.block, true);
    assert.match(budget.reason, /Tool budget exhausted/);
  } finally {
    for (const [key, value] of Object.entries(old)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
});
