/**
 * bootstrap-guard: 禁止绕过 launcher 直接运行 pi。
 *
 * launcher 每次拉起 pi 时注入一次性令牌 AGENT_INVOKED_BY_LAUNCHER；
 * 缺令牌的会话立即终止。防误用而非防对抗（见 architecture.md）。
 *
 * 由 assembly 复制到 agent/runtime/extensions/（PI_CODING_AGENT_DIR 全局扩展位，必加载）。
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function (pi: ExtensionAPI) {
  pi.on("session_start", async () => {
    if (!process.env.AGENT_INVOKED_BY_LAUNCHER) {
      console.error(
        "\n[bootstrap-guard] 拒绝启动：pi 只能通过 'uv run ok run --task <task>' 由启动器拉起" +
        "（缺少 AGENT_INVOKED_BY_LAUNCHER 令牌）。\n",
      );
      process.exit(1);
    }
  });
}
