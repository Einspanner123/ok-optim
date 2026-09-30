/**
 * budget-guard 的同类失败拦截测试。
 * 复现真实 runs 里的模式：scholar_lookup 被限流 9 次、中间夹着别的调用，
 * 旧的「连续相同调用」计数永远不会触发。
 */
import assert from "node:assert/strict";
import test from "node:test";
import path from "node:path";
import { loadExtensions } from "../../agent/vendor/node_modules/@earendil-works/pi-coding-agent/dist/core/extensions/loader.js";

async function loadGuard(budget, failureLimit) {
  const old = { b: process.env.AGENT_TOOL_BUDGET, f: process.env.AGENT_FAILURE_LIMIT };
  process.env.AGENT_TOOL_BUDGET = String(budget);
  process.env.AGENT_FAILURE_LIMIT = String(failureLimit);
  const loaded = await loadExtensions(
    [path.resolve("agent/extensions/budget-guard.ts")], process.cwd());
  assert.deepEqual(loaded.errors, []);
  const ext = loaded.extensions[0];
  const call = ext.handlers.get("tool_call")[0];
  const result = ext.handlers.get("tool_result")[0];
  return { call, result, restore: () => {
    if (old.b === undefined) delete process.env.AGENT_TOOL_BUDGET; else process.env.AGENT_TOOL_BUDGET = old.b;
    if (old.f === undefined) delete process.env.AGENT_FAILURE_LIMIT; else process.env.AGENT_FAILURE_LIMIT = old.f;
  } };
}

test("interleaved retries of the same failing operation are eventually blocked", async () => {
  const { call, result, restore } = await loadGuard(500, 3);
  try {
    const cmd = { command: "uv run python task/ingest/scripts/scholar_lookup.py --json x" };
    const other = { command: "ls -la task" };
    // 三次失败，中间夹着别的调用（旧机制在这里永远不会触发）
    for (let i = 0; i < 3; i++) {
      assert.equal(await call({ toolName: "bash", toolCallId: "c" + i, input: cmd }), undefined);
      await result({ toolName: "bash", toolCallId: "c" + i, input: cmd, isError: true,
        content: [{ type: "text", text: "needs_human: S2 搜索持续限流（429）" }] });
      assert.equal(await call({ toolName: "bash", toolCallId: "o" + i, input: other }), undefined);
      await result({ toolName: "bash", toolCallId: "o" + i, input: other, isError: false,
        content: [{ type: "text", text: "total 12" }] });
    }
    // 第四次同样的操作必须被拦
    const blocked = await call({ toolName: "bash", toolCallId: "c9", input: cmd });
    assert.equal(blocked?.block, true);
    assert.match(blocked.reason, /identical failures/);
    assert.match(blocked.reason, /rate limited/);
    assert.match(blocked.reason, /Record the pending reason/);
  } finally { restore(); }
});

test("a different command is still allowed after failures", async () => {
  const { call, result, restore } = await loadGuard(500, 3);
  try {
    const cmd = { command: "uv run python task/ingest/scripts/fetch_page.py --json x" };
    for (let i = 0; i < 3; i++) {
      await call({ toolName: "bash", toolCallId: "c" + i, input: cmd });
      await result({ toolName: "bash", toolCallId: "c" + i, input: cmd, isError: true,
        content: [{ type: "text", text: "fatal: fetch 失败: 403 Forbidden" }] });
    }
    const other = { command: "uv run python task/ingest/scripts/hub_query.py --json" };
    assert.equal(await call({ toolName: "bash", toolCallId: "x", input: other }), undefined);
  } finally { restore(); }
});

test("successful calls never count toward the failure limit", async () => {
  const { call, result, restore } = await loadGuard(500, 3);
  try {
    // 每次用不同路径，避免触发「连续相同调用」拦截
    for (let i = 0; i < 5; i++) {
      const cmd = { command: "cat README" + i + ".md" };
      assert.equal(await call({ toolName: "bash", toolCallId: "s" + i, input: cmd }), undefined);
      await result({ toolName: "bash", toolCallId: "s" + i, input: cmd, isError: false,
        content: [{ type: "text", text: "ok" }] });
    }
  } finally { restore(); }
});

test("budget still blocks beyond the limit", async () => {
  const { call, restore } = await loadGuard(3, 3);
  try {
    // 不同命令，隔离出「预算」这一条信号
    assert.equal(await call({ toolName: "bash", toolCallId: "1", input: { command: "pwd" } }), undefined);
    assert.equal(await call({ toolName: "bash", toolCallId: "2", input: { command: "echo a" } }), undefined);
    assert.equal(await call({ toolName: "bash", toolCallId: "3", input: { command: "echo b" } }), undefined);
    const over = await call({ toolName: "bash", toolCallId: "4", input: { command: "echo c" } });
    assert.equal(over?.block, true);
    assert.match(over.reason, /budget exhausted/);
  } finally { restore(); }
});
