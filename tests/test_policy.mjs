import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, symlinkSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { planCommand, readAllowed, scriptEnvironment } from "../agent/extensions/command-policy.ts";
import { executePlan } from "../agent/extensions/safe-exec.ts";

function fixture(fn) {
  const root = mkdtempSync(join(tmpdir(), "ok-policy-"));
  try {
    for (const dir of ["task/hello/scripts", "runs", ".venv/bin"]) mkdirSync(join(root, dir), { recursive: true });
    for (const name of ["README.md", ".env", "task/hello/scripts/hello.py",
      "task/hello/scripts/undeclared.py"]) writeFileSync(join(root, name), "fixture");
    writeFileSync(join(root, ".venv/bin/python"), "fixture");
    return fn({ root, task: "hello", scripts: ["hello"] });
  } finally { rmSync(root, { recursive: true, force: true }); }
}

for (const command of [
  "cat .env", "head -n 1 .env", "tail .env", "wc .env", "cat /etc/passwd",
  "echo $OPENAI_API_KEY", "echo safe & touch /tmp/no",
  "git diff --output=/tmp/no", "git show HEAD:.env", "cat README.md; pwd",
  "echo $(pwd)", "echo `pwd`", "echo safe\npwd", "cat *", "tail -f README.md",
  "uv run python task/hello/scripts/undeclared.py",
  "uv run python task/other/scripts/hello.py",
  "uv run python task/hello/scripts/../hello.py",
]) {
  test("blocks " + JSON.stringify(command), () => fixture(p => {
    assert.throws(() => planCommand(command, p));
  }));
}
test("allows literal script arguments and uses prepared Python directly", () => fixture(p => {
  const plan = planCommand('uv run python task/hello/scripts/hello.py --echo "two words"', p);
  assert.equal(plan.executable, join(p.root, ".venv/bin/python"));
  assert.deepEqual(plan.args.slice(-2), ["--echo", "two words"]);
}));
test("allows bounded file reads and root directory listing", () => fixture(p => {
  assert.equal(planCommand("cat README.md", p).args.at(-1), join(p.root, "README.md"));
  assert.equal(planCommand("head -n 20 README.md", p).args[1], "20");
  assert.equal(planCommand("ls -la", p).args.at(-1), p.root);
}));
test("path boundary does not accept a sibling prefix", () => fixture(p => {
  mkdirSync(join(p.root, "runs-secret"));
  writeFileSync(join(p.root, "runs-secret/key"), "dummy");
  assert.equal(readAllowed(p.root, "runs-secret/key"), false);
}));
test("rejects symlink escapes in read and command paths", () => fixture(p => {
  symlinkSync(join(p.root, ".env"), join(p.root, "runs/key"));
  assert.equal(readAllowed(p.root, "runs/key"), false);
  assert.throws(() => planCommand("cat runs/key", p));
}));
test("rejects script symlinks", () => fixture(p => {
  symlinkSync("/usr/bin/true", join(p.root, "task/hello/scripts/link.py"));
  p.scripts.push("link");
  assert.throws(() => planCommand("uv run python task/hello/scripts/link.py", p));
}));

test("ingest workspace is readable but internal modules remain unexecutable", () => fixture(p => {
  mkdirSync(join(p.root, ".ingest/candidates/sample"), { recursive: true });
  writeFileSync(join(p.root, ".ingest/candidates/sample/candidate.json"), "{}");
  assert.equal(readAllowed(p.root, ".ingest/candidates/sample/candidate.json"), true);
  symlinkSync(join(p.root, ".env"), join(p.root, ".ingest/secret"));
  assert.equal(readAllowed(p.root, ".ingest/secret"), false);
  writeFileSync(join(p.root, "task/hello/scripts/_state.py"), "");
  assert.throws(() => planCommand("uv run python task/hello/scripts/_state.py", p));
  assert.throws(() => planCommand('python -c "import _state"', p));
  assert.throws(() => planCommand('uv run python -c "import _state"', p));
}));
test("missing task fails closed", () => fixture(p => {
  assert.throws(() => planCommand("cat README.md", { ...p, task: "" }));
}));
test("does not inherit provider secrets or loader injection", () => {
  const env = scriptEnvironment({
    OPENAI_API_KEY: "dummy", NODE_OPTIONS: "--bad", BASH_ENV: "/bad",
    PYTHONPATH: "/bad", FOO: "bar", AGENT_TASK: "hello",
    AGENT_SCRIPT_ENV_JSON: JSON.stringify(["FOO", "OPENAI_API_KEY", "NODE_OPTIONS", "BASH_ENV", "PYTHONPATH"]),
  });
  assert.equal(env.FOO, "bar");
  for (const key of ["OPENAI_API_KEY", "NODE_OPTIONS", "BASH_ENV", "PYTHONPATH"])
    assert.equal(env[key], undefined);
});
test("argv executor never interprets shell syntax", async () => {
  let output = "";
  await executePlan({ executable: "/usr/bin/printf", args: ["%s", "$(printf injected) & literal"] },
    tmpdir(), {}, { onData: chunk => output += chunk });
  assert.equal(output, "$(printf injected) & literal");
});
test("executor passes only its explicit environment", async () => {
  let output = "";
  await executePlan({ executable: "/usr/bin/env", args: [] }, tmpdir(),
    scriptEnvironment({ OPENAI_API_KEY: "dummy" }), { onData: chunk => output += chunk });
  assert.equal(output.includes("OPENAI_API_KEY"), false);
});
test("timeout is a failure", async () => {
  await assert.rejects(executePlan({ executable: "/bin/sleep", args: ["5"] },
    tmpdir(), {}, { onData() {}, timeout: 0.05 }), /timed out/);
});
test("pre-aborted command is never started", async () => {
  const controller = new AbortController(); controller.abort();
  await assert.rejects(executePlan({ executable: "/usr/bin/true", args: [] },
    tmpdir(), {}, { onData() {}, signal: controller.signal }), /aborted/);
});
