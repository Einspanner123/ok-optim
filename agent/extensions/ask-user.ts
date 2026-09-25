/**
 * ask-user: 运行中裁决升级工具。
 *
 * 仅由交互启动器加载；非交互环境即使误加载本扩展也不注册工具。
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

export default function (pi: ExtensionAPI) {
  if (process.env.AGENT_INTERACTIVE !== "1") return;
  pi.registerTool({
    name: "ask_user",
    label: "Ask user",
    description:
      "向用户提问获取人工裁决（选项二选一/多选一，或确认）。",
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
        throw new Error("Interactive UI is not available");
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
