import assert from "node:assert/strict";
import test from "node:test";
import path from "node:path";
import { loadExtensions } from "../../agent/vendor/node_modules/@earendil-works/pi-coding-agent/dist/core/extensions/loader.js";

test("ask_user registers only for interactive runs", async () => {
  const previous = process.env.AGENT_INTERACTIVE;
  try {
    for (const mode of [undefined, "0", "1"]) {
      if (mode === undefined) delete process.env.AGENT_INTERACTIVE;
      else process.env.AGENT_INTERACTIVE = mode;
      const result = await loadExtensions(
        [path.resolve("agent/extensions/ask-user.ts")], process.cwd());
      assert.deepEqual(result.errors, []);
      assert.equal(result.extensions.length, 1);
      assert.equal(result.extensions[0].tools.has("ask_user"), mode === "1");
    }
  } finally {
    if (previous === undefined) delete process.env.AGENT_INTERACTIVE;
    else process.env.AGENT_INTERACTIVE = previous;
  }
});
