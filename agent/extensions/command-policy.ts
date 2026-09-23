/** Fail-closed command planning. No accepted command is executed by a shell. */
import { realpathSync, statSync } from "node:fs";
import { resolve, sep } from "node:path";

export interface Policy {
  root: string;
  task: string;
  scripts: string[];
}
export interface CommandPlan { executable: string; args: string[] }

const READ_DIRS = ["task", "runs", "single-cell-hub"];
const READ_FILES = ["AGENTS.md", "README.md", "pyproject.toml", "main.py",
  ".env.example", ".python-version"];

function inside(path: string, base: string): boolean {
  return path === base || path.startsWith(base + sep);
}

export function readAllowed(root: string, raw: string): boolean {
  if (!raw || raw.includes("\0")) return false;
  try {
    const base = realpathSync(root);
    const path = realpathSync(resolve(base, raw));
    return READ_DIRS.some(dir => inside(path, resolve(base, dir)))
      || READ_FILES.some(file => path === resolve(base, file));
  } catch { return false; }
}

/** A literal argv grammar, deliberately smaller than shell syntax. */
export function tokenize(command: string): string[] {
  if (/[\x00-\x1f\x7f&|;<>`$\\*?~{}()[\]#!]/u.test(command))
    throw new Error("shell operators, expansion and control characters are disabled");
  const result: string[] = [];
  let token = "", quote = "", started = false;
  for (const char of command.trim()) {
    if (quote) {
      if (char === quote) quote = "";
      else token += char;
      started = true;
    } else if (char === "'" || char === '"') {
      quote = char; started = true;
    } else if (char === " ") {
      if (started) result.push(token);
      token = ""; started = false;
    } else { token += char; started = true; }
  }
  if (quote) throw new Error("unterminated quote");
  if (started) result.push(token);
  if (!result.length) throw new Error("empty command");
  return result;
}

function binary(name: string): string {
  for (const dir of ["/usr/bin", "/bin"]) {
    const path = resolve(dir, name);
    try { if (statSync(path).isFile()) return path; } catch { /* try next */ }
  }
  throw new Error("missing system executable: " + name);
}

export function planCommand(command: string, policy: Policy): CommandPlan {
  const root = realpathSync(policy.root);
  if (!/^[A-Za-z0-9_-]+$/.test(policy.task))
    throw new Error("missing or invalid task");
  const words = tokenize(command);
  const [name, ...args] = words;
  if (name === "uv") {
    if (words[1] !== "run" || words[2] !== "python")
      throw new Error("only declared task scripts are allowed");
    const relative = words[3] ?? "";
    const prefix = "task/" + policy.task + "/scripts/";
    if (!relative.startsWith(prefix)) throw new Error("wrong task");
    const script = relative.slice(prefix.length);
    if (!/^[A-Za-z0-9_-]+\.py$/.test(script)
        || !policy.scripts.includes(script.slice(0, -3)))
      throw new Error("undeclared script");
    const target = resolve(root, relative);
    if (realpathSync(target) !== target || !statSync(target).isFile())
      throw new Error("script symlinks are disabled");
    // Use the already prepared environment: uv must never sync/install at run time.
    const python = resolve(root, ".venv/bin/python");
    if (!statSync(python).isFile()) throw new Error("project Python is missing");
    return { executable: python, args: ["-B", "-E", "-s", target, ...words.slice(4)] };
  }
  if (name === "pwd" && args.length === 0)
    return { executable: binary("pwd"), args: [] };
  if (name === "echo")
    return { executable: binary("printf"), args: ["%s\\n", args.join(" ")] };
  if (name === "git") {
    // No caller-provided Git configuration, revisions, output paths, or external diff.
    const safe = args[0] === "status"
      && args.slice(1).every(arg => ["--short", "--porcelain", "-s", "-b"].includes(arg));
    if (!safe) throw new Error("only git status with presentation flags is allowed");
    return { executable: binary("git"), args: ["--no-pager", "-c",
      "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", ...args] };
  }
  if (!["ls", "cat", "head", "tail", "wc"].includes(name))
    throw new Error("command is not allowed");
  const flags: Record<string, string[]> = {
    ls: ["-l", "-a", "-la", "-al", "-lh", "-lah", "-1"],
    cat: ["-n", "-b"], head: [], tail: [], wc: ["-l", "-w", "-c", "-m"],
  };
  const options: string[] = [], paths: string[] = [];
  let pathsOnly = false;
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (!pathsOnly && arg === "--") { pathsOnly = true; continue; }
    if (!pathsOnly && arg.startsWith("-")) {
      if ((name === "head" || name === "tail") && arg === "-n") {
        const count = args[++i] ?? "";
        if (!/^\d{1,6}$/.test(count)) throw new Error("invalid line count");
        options.push(arg, count);
      } else if (flags[name].includes(arg)) options.push(arg);
      else throw new Error("unsupported option: " + arg);
    } else paths.push(arg);
  }
  if (!paths.length && name === "ls") paths.push(".");
  if (!paths.length) throw new Error("explicit readable paths are required");
  const resolved = paths.map(path => {
    const target = realpathSync(resolve(root, path));
    if (!(name === "ls" && target === root) && !readAllowed(root, path))
      throw new Error("path outside readable roots: " + path);
    const stat = statSync(target);
    if (!(stat.isFile() || (name === "ls" && stat.isDirectory())))
      throw new Error("not a regular file/directory");
    return target;
  });
  return { executable: binary(name), args: [...options, "--", ...resolved] };
}

export function scriptEnvironment(source: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const keys = ["LANG", "LC_ALL", "NPU_PYTHON", "HTTP_PROXY", "HTTPS_PROXY",
    "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE",
    "AGENT_TASK", "AGENT_SLUG", "AGENT_RUN_DIR", "AGENT_RUN_ID", "AGENT_INTERACTIVE"];
  const taskKeys: unknown = JSON.parse(source.AGENT_SCRIPT_ENV_JSON ?? "[]");
  if (!Array.isArray(taskKeys) || taskKeys.some(k => typeof k !== "string"))
    throw new Error("invalid script environment declaration");
  const env: NodeJS.ProcessEnv = {
    PATH: "/usr/bin:/bin", PYTHONDONTWRITEBYTECODE: "1",
    GIT_CONFIG_NOSYSTEM: "1", GIT_CONFIG_GLOBAL: "/dev/null", GIT_TERMINAL_PROMPT: "0",
  };
  for (const key of [...keys, ...taskKeys]) {
    // Task metadata cannot re-enable loader injection or expose provider credentials.
    if (/^(OPENAI_|PI_|PYTHON|LD_|NODE_|BASH|ENV$|PATH$|HOME$|GIT_)/.test(key)) continue;
    if (source[key] !== undefined) env[key] = source[key];
  }
  return env;
}
