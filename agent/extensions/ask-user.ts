/**
 * ask-user: 运行中裁决升级工具。
 *
 * 仅交互模式（pi TUI）可用；json/print 模式返回 NOT_INTERACTIVE，
 * agent 按各任务 SKILL.md 约定走 needs_human 分支（写 pending + 安全收尾）。
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "ask_user",
    label: "Ask user",
    description:
      "向用户提问获取人工裁决（选项二选一/多选一，或确认）。" +
      "仅交互模式可用；非交互模式会返回 NOT_INTERACTIVE，此时按 SKILL.md 的 needs_human 分支处理。",
    parameters: Type.Object({
      question: Type.String({
        description: "要问的问题，说明背景与需要裁决的点",
      }),
      options: Type.Optional(
        Type.Array(Type.String(), {
          description: "可选选项列表（提供则展示为单选；不提供则展示为 确认/取消）",
        }),
      ),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      if (ctx.mode !== "tui") {
        return {
          content: [
            {
              type: "text",
              text:
                "NOT_INTERACTIVE: ask_user 在非交互模式不可用。" +
                "请按 SKILL.md 的 needs_human 分支处理：记录 pending 项（说明决策点与候选），然后安全收尾。",
            },
          ],
          details: {},
        };
      }

      if (params.options && params.options.length > 0) {
        const choice = await ctx.ui.select("Agent 需要你的决策", params.options);
        if (choice === undefined) {
          return {
            content: [{ type: "text", text: "CANCELLED: 用户取消了选择" }],
            details: {},
          };
        }
        return { content: [{ type: "text", text: String(choice) }], details: {} };
      }

      const ok = await ctx.ui.confirm("Agent 需要确认", params.question);
      return {
        content: [{ type: "text", text: ok ? "CONFIRMED" : "DENIED" }],
        details: {},
      };
    },
  });
}
