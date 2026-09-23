/** Execute an approved argv directly, with a minimal child environment. */
import { spawn } from "node:child_process";
import type { CommandPlan } from "./command-policy.ts";

export function executePlan(
  plan: CommandPlan, cwd: string, env: NodeJS.ProcessEnv,
  options: { onData: (data: Buffer) => void; signal?: AbortSignal; timeout?: number },
): Promise<{ exitCode: number }> {
  if (options.signal?.aborted) return Promise.reject(new Error("aborted"));
  const seconds = options.timeout ?? 600;
  if (!Number.isFinite(seconds) || seconds <= 0 || seconds > 3600)
    return Promise.reject(new Error("timeout must be in (0, 3600] seconds"));
  return new Promise((resolve, reject) => {
    const child = spawn(plan.executable, plan.args, {
      cwd, env, shell: false, detached: true, stdio: ["ignore", "pipe", "pipe"],
    });
    let stopped = false;
    const killGroup = (signal: NodeJS.Signals) => {
      if (child.pid) {
        try { process.kill(-child.pid, signal); } catch { /* already exited */ }
      }
    };
    const stop = () => {
      if (stopped) return;
      stopped = true;
      killGroup("SIGTERM");
      // Keep escalation even if the parent exits before a descendant.
      setTimeout(() => killGroup("SIGKILL"), 1000).unref();
    };
    const timer = setTimeout(stop, seconds * 1000);
    options.signal?.addEventListener("abort", stop, { once: true });
    if (options.signal?.aborted) stop();
    child.stdout.on("data", options.onData);
    child.stderr.on("data", options.onData);
    const cleanup = () => {
      clearTimeout(timer);
      options.signal?.removeEventListener("abort", stop);
    };
    child.once("error", error => { cleanup(); reject(error); });
    child.once("close", code => {
      cleanup();
      if (stopped) reject(new Error("command aborted or timed out"));
      else resolve({ exitCode: code ?? 1 });
    });
  });
}
