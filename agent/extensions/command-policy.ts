/** Fail-closed command planning. No accepted command is executed by a shell. */
import { realpathSync, statSync } from "node:fs";
import { resolve, sep } from "node:path";

export interface Policy {
  root: string;
  task: string;
  scripts: string[];
  /** Readable directories, declared per task in skill.yaml. Absolute or root-relative. */
  readDirs: string[];
  /** Readable single files, declared per task in skill.yaml. Absolute or root-relative. */
  readFiles: string[];
}
export interface CommandPlan { executable: string; args: string[] }

/** 解析后的可读范围：声明路径已 realpath，且已确认存在。 */
export interface ReadScope { base: string; dirs: string[]; files: string[] }

// 只读命令集。grep/find 由 runs 数据实证为高频需求。
const READONLY = ["ls", "cat", "head", "tail", "wc", "grep", "find"];
// 选项形态检查：只读命令的选项不构成安全边界（ls -R 与 ls -la 同为只读），
// 这里只拦 -x;rm 这类拼接；真正的边界是 ReadScope。
const OPTION_RE = /^-{1,2}[A-Za-z0-9][A-Za-z0-9=,._-]*$/;
// 字面量字符集白名单：未知字符默认拒绝（黑名单永远可能漏一个）。
const SAFE_CHAR = /[A-Za-z0-9._/@:%=,+~-]/;

/**
 * 紧随其后需要一个数字的选项（head -c 2000 / head -n 5 / grep -m 3 / ls -w 80）。
 * 这张表只影响 token 分类——把值留在选项里，别让它掉进路径校验——与安全无关。
 * 表以外的选项一律按开关原样透传，绝不改写；未登记的"值型"选项会让它的值被
 * 当成路径并报 no such path，属于响亮失败，不会静默改变命令语义。
 * (2026-09-30) 表内容取自 runs 目录下 events.jsonl 里 193 次 bash 调用的实际选项分布。
 */
const NUMERIC_OPTIONS: Record<string, string[]> = {
  head: ["-c", "-n"],
  tail: ["-c", "-n"],
  grep: ["-m", "-A", "-B", "-C"],
  ls: ["-w"],
};
/** 本环境不可用的选项：--follow 永不返回（把会话挂到墙钟超时）；
 *  -e/-f/--files0-from 引入第二个输入源，模式一律走位置参数。 */
const REJECTED_OPTIONS: Record<string, string[]> = {
  tail: ["-f", "-F", "--follow"],
  grep: ["-e", "-f"],
  wc: ["--files0-from"],
};

/** find 的表达式不是"选项"而是"程序"：-delete 改盘、-exec 执行外部命令。
 *  这里只放行筛选 + 打印类谓词；runs 数据里 find 只出现过 -type / -name。 */
const FIND_FILTERS = ["-name", "-iname", "-path", "-ipath", "-regex", "-iregex",
  "-type", "-maxdepth", "-mindepth", "-size", "-mtime", "-mmin", "-newer", "-newermt"];
const FIND_FLAGS = ["-print", "-print0", "-prune", "-not", "-a", "-o"];

function inside(path: string, base: string): boolean {
  return path === base || path.startsWith(base + sep);
}

/** 把 skill.yaml 声明的可读目录/文件解析成规范绝对路径。
 *  声明了不存在的路径是**配置错误**，必须大声抛出：早期实现把它吞成"无权限"，
 *  结果是声明里多一条就静默拒绝全部读取（连 read_files 里的文件都读不到），
 *  报错文案还误导向 path outside readable roots。 */
function roots(base: string, declared: string[]): string[] {
  return declared.map(entry => {
    let target: string;
    try { target = realpathSync(resolve(base, entry)); }
    catch {
      throw new Error("declared readable path does not exist: " + entry
        + " (fix read_dirs/read_files in task/<task>/skill.yaml)");
    }
    const stat = statSync(target);
    if (!stat.isDirectory() && !stat.isFile())
      throw new Error("readable roots must be files or directories: " + entry);
    return target;
  });
}

/** 解析并校验可读声明；任一 entry 缺失即抛出（供启动期做一次 fail-closed 校验）。 */
export function resolveScope(policy: Policy): ReadScope {
  const base = realpathSync(policy.root);
  return { base, dirs: roots(base, policy.readDirs), files: roots(base, policy.readFiles) };
}

export function readAllowed(policy: Policy, raw: string): boolean {
  if (!raw || raw.includes("\0")) return false;
  // 配置错误在这里抛出，不再被 catch 吞成"无权限"。
  const scope = resolveScope(policy);
  let path: string;
  try { path = realpathSync(resolve(scope.base, raw)); } catch { return false; }
  if (path !== scope.base && !path.startsWith(scope.base + sep)) return false;
  return scope.dirs.some(dir => inside(path, dir)) || scope.files.includes(path);
}

/** A literal argv grammar, deliberately smaller than shell syntax.
 *  Meta characters are banned only OUTSIDE quotes — quoted content is a
 *  literal argument (e.g. --payload '<json>'), the shell never sees it. */
export function tokenize(command: string): string[] {
  const result: string[] = [];
  let token = "", quote = "", started = false;
  for (const char of command.trim()) {
    if (char === "\0" || char === "\n" || char === "\r")
      throw new Error("control characters are disabled");
    if (quote) {
      if (char === quote) quote = "";
      else token += char;
      started = true;
    } else if (char === "'" || char === '"') {
      quote = char; started = true;
    } else if (char === " ") {
      if (started) result.push(token);
      token = ""; started = false;
    } else if (!SAFE_CHAR.test(char)) {
      throw new Error("character not allowed: " + JSON.stringify(char));
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

/** 位置参数 -> realpath 后的可读绝对路径。不存在与越界分开报错，避免诊断误导。 */
function readable(policy: Policy, root: string, name: string, path: string): string {
  let target: string;
  try { target = realpathSync(resolve(root, path)); }
  catch { throw new Error("no such path: " + path); }
  if (!(name === "ls" && target === root) && !readAllowed(policy, path))
    throw new Error("path outside readable roots: " + path);
  const stat = statSync(target);
  const allowDirectory = name === "ls" || name === "find" || name === "grep";
  if (!(stat.isFile() || (allowDirectory && stat.isDirectory())))
    throw new Error("not a regular file/directory: " + path);
  return target;
}

/** 把 argv 拆成选项与位置参数。只做形态判断，不做安全判断。 */
function splitArgs(name: string, args: string[]): { options: string[]; positional: string[] } {
  const options: string[] = [], positional: string[] = [];
  const numeric = NUMERIC_OPTIONS[name] ?? [];
  const rejected = REJECTED_OPTIONS[name] ?? [];
  let pathsOnly = false;
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (!pathsOnly && arg === "--") { pathsOnly = true; continue; }
    if (pathsOnly || !arg.startsWith("-")) { positional.push(arg); continue; }
    if (!OPTION_RE.test(arg)) throw new Error("invalid option: " + arg);
    if (rejected.some(opt => arg === opt || arg.startsWith(opt + "=")))
      throw new Error("option not available for " + name + ": " + arg);
    // head -50 / tail -5：数字即行数，规范化为 -n <n>，与 -n 5 收敛成同一形态。
    if ((name === "head" || name === "tail") && /^-\d{1,9}$/.test(arg)) {
      options.push("-n", arg.slice(1));
      continue;
    }
    if (numeric.includes(arg)) {
      const value = args[i + 1];
      if (!/^\d{1,9}$/.test(value ?? ""))
        throw new Error(arg + " requires a numeric value");
      options.push(arg, value); i++;
      continue;
    }
    // 开关类选项原样透传。早期实现把任何非数字选项都改写成 -n 10，
    // 于是 head --bytes=5 f 实跑成 head -n 10 f（静默改变语义）。
    options.push(arg);
  }
  return { options, positional };
}

/** 从位置参数中取出需要做路径校验的那部分。grep 的首个位置参数是模式。 */
function pathOperands(name: string, positional: string[]): string[] {
  if (name === "grep") return positional.slice(1);
  return positional;
}

/** find 语法是 `find [起始目录...] [表达式...]`：起始目录必须排在表达式之前，
 *  表达式只允许筛选 + 打印类谓词。早期实现生成 `<谓词> -- <路径>`，既违反该语法
 *  （GNU find 报 unknown predicate '--'，带谓词的 find 全废），又会让 -delete
 *  这类谓词重新变得可执行。 */
function planFind(args: string[]): { options: string[]; paths: string[] } {
  const start = args.findIndex(arg => arg.startsWith("-"));
  const paths = start === -1 ? args : args.slice(0, start);
  const expression = start === -1 ? [] : args.slice(start);
  if (!paths.length)
    throw new Error("find requires a starting path before the expression");
  const options: string[] = [];
  for (let i = 0; i < expression.length; i++) {
    const token = expression[i];
    if (!OPTION_RE.test(token)) throw new Error("invalid find predicate: " + token);
    if (FIND_FLAGS.includes(token)) { options.push(token); continue; }
    if (!FIND_FILTERS.includes(token))
      throw new Error("find predicate is not allowed: " + token);
    const value = expression[i + 1];
    if (value === undefined || value.startsWith("-"))
      throw new Error(token + " requires a value");
    options.push(token, value); i++;
  }
  return { options, paths };
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
  if (!READONLY.includes(name))
    throw new Error("command is not allowed");

  if (name === "find") {
    const { options, paths } = planFind(args);
    // 起始目录排在表达式之前——这就是 GNU find 的语法。
    return { executable: binary("find"),
      args: [...paths.map(path => readable(policy, root, name, path)), ...options] };
  }

  const { options, positional } = splitArgs(name, args);
  const pattern = name === "grep" ? positional[0] : undefined;
  if (name === "grep" && pattern === undefined)
    throw new Error("grep requires a pattern");
  const operands = pathOperands(name, positional);
  if (!operands.length && name === "ls") operands.push(".");
  // grep 必须给出待搜文件：bash 子进程的 stdin 是 /dev/null，无文件的 grep 只可能
  // 返回"无匹配"，正是本次要消除的那类静默假阴性。
  if (!operands.length)
    throw new Error(name === "grep"
      ? "grep needs at least one file; the first argument is the pattern"
      : "explicit readable paths are required");
  const resolved = operands.map(path => readable(policy, root, name, path));
  // 路径一律是 realpath 后的绝对路径（不可能以 - 开头），因此不再需要 -- 作为
  // 选项终止符；grep 仍用 -- 把模式与选项隔开，避免模式被当作选项解析。
  if (name === "grep")
    return { executable: binary("grep"), args: [...options, "--", pattern, ...resolved] };
  return { executable: binary(name), args: [...options, ...resolved] };
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
