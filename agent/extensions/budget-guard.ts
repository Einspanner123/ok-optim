/**
 * budget-guard: 机械护栏——工具调用预算耗尽后拒绝一切工具调用。
 *
 * 模型不守软预算（SKILL.md 失败预算），退化循环必须由 harness 机械终止：
 * 超过 AGENT_TOOL_BUDGET（默认 60）次工具调用后，所有工具调用被拒绝并收到
 * "立即总结收尾"指令——模型只能产出最终文本答复，会话自然结束而非爆 token。
 * launcher 记账的 usage 与本计数无关（本计数是 pi 会话内的真实执行数）。
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const BUDGET = Number(process.env.AGENT_TOOL_BUDGET ?? 60) || 60;

export default function (pi: ExtensionAPI) {
  let count = 0;
  let exhausted = false;
  pi.on("tool_call", async (event, ctx) => {
    if (exhausted) {
      return {
        block: true,
        reason:
          `工具调用预算已耗尽（上限 ${BUDGET} 次）。` +
          "不要再尝试任何工具。立即根据已获得的信息输出最终汇报：" +
          "已完成步骤、受阻原因（needs_human 事项）、遗留 pending 项。然后结束。",
      };
    }
    count += 1;
    if (count >= BUDGET) exhausted = true;
    return undefined;
  });
}
