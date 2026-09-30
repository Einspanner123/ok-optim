/**
 * Execution-level tests: the plan is actually run and its output is asserted.
 *
 * Why this file exists (2026-09-30): the earlier suite only asserted argv *shape*
 * (`args.at(-1)`, `args.includes("--")`), so two real defects survived 42 green
 * tests — grep silently lost its pattern (it searched stdin and reported "no
 * match" for files that did match) and find emitted its expression before its
 * starting path (GNU find: `unknown predicate '--'`). A plausible-looking argv
 * is not evidence that the command runs.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { planCommand, readAllowed, resolveScope } from "../../agent/extensions/command-policy.ts";
import { executePlan } from "../../agent/extensions/safe-exec.ts";

const README = "MARKER_ALPHA first\nsecond line\nMARKER_ALPHA third\n";

/** 必须 await 回调：早期版本是同步的，finally 会在 async 测试体跑完前就删掉临时
 *  目录，测试于是拿一个已消失的 root 去 planCommand（ENOENT lstat）。 */
async function fixture(fn) {
  const root = mkdtempSync(join(tmpdir(), "ok-exec-"));
  try {
    for (const dir of ["task/hello/scripts", "task/deep", "runs", ".venv/bin"])
      mkdirSync(join(root, dir), { recursive: true });
    writeFileSync(join(root, "README.md"), README);
    writeFileSync(join(root, ".env"), "SECRET=1\n");
    writeFileSync(join(root, "task/deep/needle.txt"), "needle\n");
    writeFileSync(join(root, "task/hello/scripts/hello.py"), "print('hi')\n");
    writeFileSync(join(root, ".venv/bin/python"), "#!/bin/sh\nexit 0\n");
    return await fn({ root, task: "hello", scripts: ["hello"],
      readDirs: ["task", "runs"], readFiles: ["README.md"] });
  } finally { rmSync(root, { recursive: true, force: true }); }
}

async function run(command, policy) {
  const plan = planCommand(command, policy);
  let out = "";
  const { exitCode } = await executePlan(plan, policy.root, { PATH: "/usr/bin:/bin", LANG: "C" },
    { onData: chunk => { out += chunk.toString(); } });
  return { plan, out, exitCode };
}

test("grep finds a string that is really in the file", () => fixture(async p => {
  const { out, exitCode } = await run("grep -n MARKER_ALPHA README.md", p);
  assert.equal(exitCode, 0);
  assert.match(out, /MARKER_ALPHA/);
}));

test("grep -c reports the real match count, not zero", () => fixture(async p => {
  const { out, exitCode } = await run("grep -c MARKER_ALPHA README.md", p);
  assert.equal(exitCode, 0);
  assert.equal(out.trim(), "2");
}));

test("grep keeps its pattern in front of the path", () => fixture(p => {
  assert.deepEqual(planCommand("grep -n hello README.md", p).args,
    ["-n", "--", "hello", join(p.root, "README.md")]);
}));

test("grep with a numeric count option keeps pattern and path apart", () => fixture(async p => {
  const { out, exitCode } = await run("grep -m 1 -n MARKER_ALPHA README.md", p);
  assert.equal(exitCode, 0);
  assert.equal(out.trim().split("\n").length, 1);
}));

test("grep still refuses a path outside the readable roots", () => fixture(p => {
  assert.throws(() => planCommand("grep hello .env", p), /readable roots/);
  assert.throws(() => planCommand("grep -r hello .env", p), /readable roots/);
}));

test("find lists a nested file with a predicate", () => fixture(async p => {
  const { out, exitCode } = await run("find task -name needle.txt", p);
  assert.equal(exitCode, 0);
  assert.match(out, /needle\.txt/);
}));

test("find accepts -type f and lists both fixture files", () => fixture(async p => {
  const { out, exitCode } = await run("find task -type f", p);
  assert.equal(exitCode, 0);
  assert.match(out, /needle\.txt/);
  assert.match(out, /hello\.py/);
}));

test("find puts the starting path before the expression", () => fixture(p => {
  assert.deepEqual(planCommand("find task -name needle.txt", p).args,
    [join(p.root, "task"), "-name", "needle.txt"]);
  assert.deepEqual(planCommand("find task", p).args, [join(p.root, "task")]);
}));

test("find demands its own starting path", () => fixture(p => {
  // 起始目录缺失比谓词问题更根本，先报这一条。
  assert.throws(() => planCommand("find -name needle.txt", p), /starting path/);
  assert.throws(() => planCommand("find -delete", p), /starting path/);
  assert.throws(() => planCommand("find", p), /starting path/);
}));

test("find refuses predicates that write or execute", () => fixture(p => {
  for (const command of ["find task -delete", "find task -exec rm -rf", "find task -fprintf out",
    "find task -name x -delete", "find task -ok rm"])
    assert.throws(() => planCommand(command, p), /not allowed/);
}));

test("head -c keeps the requested byte count instead of ten lines", () => fixture(async p => {
  const { out, exitCode } = await run("head -c 5 README.md", p);
  assert.equal(exitCode, 0);
  assert.equal(out, "MARKE");
}));

test("head -n keeps the requested line count", () => fixture(async p => {
  const two = await run("head -n 2 README.md", p);
  assert.equal(two.out, "MARKER_ALPHA first\nsecond line\n");
  const one = await run("head -1 README.md", p);
  assert.equal(one.out, "MARKER_ALPHA first\n");
}));

test("tail -n reads the last lines instead of defaulting to ten", () => fixture(async p => {
  const { out } = await run("tail -n 1 README.md", p);
  assert.equal(out, "MARKER_ALPHA third\n");
}));

test("the never-returning follow option is refused", () => fixture(p => {
  for (const command of ["tail -f README.md", "tail -F README.md", "tail --follow=name README.md"])
    assert.throws(() => planCommand(command, p), /not available/);
}));

test("unusable input options and bad values are refused with a reason", () => fixture(p => {
  for (const command of ["grep -e foo README.md", "grep -f patterns README.md",
    "wc --files0-from list README.md"])
    assert.throws(() => planCommand(command, p), /not available/);
  assert.throws(() => planCommand("head -n README.md", p), /numeric value/);
  assert.throws(() => planCommand("grep README.md", p), /at least one file/);
  assert.throws(() => planCommand("grep", p), /requires a pattern/);
}));

test("other read-only commands still execute", () => fixture(async p => {
  for (const command of ["cat README.md", "wc -l README.md", "ls -la task", "ls", "pwd", "echo hello"]) {
    const { exitCode } = await run(command, p);
    assert.equal(exitCode, 0, command);
  }
}));

test("a declared readable path that does not exist is a configuration error", () => fixture(p => {
  const broken = { ...p, readDirs: [...p.readDirs, "no-such-dir"] };
  // 早期实现把这条误吞成"无权限"，于是连 readFiles 里的文件都读不到。
  assert.throws(() => resolveScope(broken), /no-such-dir/);
  assert.throws(() => readAllowed(broken, "README.md"), /no-such-dir/);
  assert.throws(() => planCommand("cat README.md", broken), /no-such-dir/);
}));

test("a good declaration keeps working", () => fixture(p => {
  assert.equal(readAllowed(p, "README.md"), true);
  assert.equal(readAllowed(p, "task/deep/needle.txt"), true);
  assert.equal(readAllowed(p, "task/../.env"), false);
  assert.equal(readAllowed(p, ".env"), false);
}));
