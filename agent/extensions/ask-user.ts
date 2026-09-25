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
      "Ask the human for a decision using a choice list or confirmation.",
    parameters: Type.Object({
      question: Type.String({
        description: "Question in the response language, with the decision context",
      }),
      options: Type.Optional(
        Type.Array(Type.String(), {
          description: "Optional single-choice answers; omit for confirmation",
        }),
      ),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      if (ctx.mode !== "tui") {
        throw new Error("Interactive UI is not available");
      }

      if (params.options && params.options.length > 0) {
        const choice = await ctx.ui.select(params.question, params.options);
        if (choice === undefined) {
          return {
            content: [{ type: "text", text: "CANCELLED: the human cancelled the selection" }],
            details: {},
          };
        }
        return { content: [{ type: "text", text: String(choice) }], details: {} };
      }

      const ok = await ctx.ui.confirm(params.question, "");
      return {
        content: [{ type: "text", text: ok ? "CONFIRMED" : "DENIED" }],
        details: {},
      };
    },
  });
}
