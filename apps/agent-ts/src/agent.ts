/**
 * Agent adapter - wraps pi-agent-core Agent runtime.
 * No hand-written agent loop. All tool calling and reasoning is driven by pi-mono.
 */

import { Agent as PiAgent } from "@earendil-works/pi-agent-core";
import type {
  AgentTool,
  AgentToolResult,
  AgentEvent as PiAgentEvent,
  BeforeToolCallContext,
  AfterToolCallContext,
  AgentMessage as PiAgentMessage,
} from "@earendil-works/pi-agent-core";
import {
  streamSimple,
  type Model,
} from "@earendil-works/pi-ai/compat";
import type {
  AssistantMessage,
  TextContent,
} from "@earendil-works/pi-ai";
import type { TSchema } from "typebox";
import { Type } from "typebox";
import { v4 as uuidv4 } from "uuid";
import type {
  AgentEvent,
  AgentResult,
  RegisteredTool,
} from "./types";

// ---------------------------------------------------------------------------
// Adapter types
// ---------------------------------------------------------------------------

export interface AgentOptions {
  maxSteps: number; // kept for API compat; pi runtime manages bounds internally
  maxToolCalls: number;
  systemPrompt: string;
  model: Model<any>;
  mockMode?: boolean;
}

// ---------------------------------------------------------------------------
// Helper to extract text from pi messages
// ---------------------------------------------------------------------------

function getMessageContent(msg: PiAgentMessage): Array<{ type: string; text?: string }> {
  // PiAgentMessage = Message | CustomAgentMessages[keyof CustomAgentMessages]
  // Standard messages have a `content` array field
  if ("content" in msg && Array.isArray((msg as any).content)) {
    return (msg as any).content;
  }
  return [];
}

function getMessageRole(msg: PiAgentMessage): string {
  if ("role" in msg) {
    return (msg as any).role;
  }
  return "unknown";
}

// ---------------------------------------------------------------------------
// Agent adapter
// ---------------------------------------------------------------------------

export class Agent {
  private options: AgentOptions;
  private tools: RegisteredTool[] = [];
  private piAgent!: PiAgent;
  private streamFn: typeof streamSimple;

  constructor(options: AgentOptions) {
    this.options = options;
    this.streamFn = streamSimple;
  }

  /** Register tools with this agent. */
  addTools(tools: RegisteredTool[] | RegisteredTool): void {
    if (Array.isArray(tools)) {
      this.tools.push(...tools);
    } else {
      this.tools.push(tools);
    }
  }

  async run(
    userInput: string,
    onEvent?: (event: AgentEvent) => void,
  ): Promise<AgentResult> {
    const sessionId = uuidv4();
    const events: AgentEvent[] = [];
    let finalOutput = "";

    const emit = (event: AgentEvent) => {
      events.push(event);
      onEvent?.(event);
    };

    emit({ type: "session_start", session_id: sessionId });

    try {
      // Build pi agent tools
      const piTools = this.tools.map((t) => adaptTool(t));

      // Create pi Agent instance
      this.piAgent = new PiAgent({
        initialState: {
          systemPrompt: this.options.systemPrompt,
          model: this.options.model,
          tools: piTools,
        },
        streamFn: this.streamFn,
        beforeToolCall: async (ctx: BeforeToolCallContext) => {
          // Emit tool_call event
          emit({
            type: "tool_call",
            tool_name: ctx.toolCall.name,
            params: ctx.args as Record<string, unknown>,
          });
          return undefined; // allow all for now
        },
        afterToolCall: async (ctx: AfterToolCallContext) => {
          // Emit tool_result event
          emit({
            type: "tool_result",
            tool_name: ctx.toolCall.name,
            result: ctx.result.details,
          });
          return undefined;
        },
      });

      // Subscribe to pi events for streaming
      let assistantText = "";
      this.piAgent.subscribe(async (event: PiAgentEvent) => {
        switch (event.type) {
          case "message_update": {
            const content = getMessageContent(event.message);
            for (const block of content) {
              if (block.type === "text" && block.text) {
                const prevLen = assistantText.length;
                assistantText = block.text;
                const delta = assistantText.slice(prevLen);
                if (delta) {
                  emit({ type: "text_delta", content: delta });
                }
              }
            }
            break;
          }
          case "message_end": {
            const role = getMessageRole(event.message);
            if (role === "assistant") {
              const content = getMessageContent(event.message);
              const textBlocks = content.filter((c) => c.type === "text");
              const toolCalls = content.filter((c) => c.type === "toolCall");
              if (toolCalls.length === 0 && textBlocks.length > 0) {
                finalOutput = textBlocks.map((t) => t.text ?? "").join("");
              }
            }
            break;
          }
        }
      });

      // Start the agent
      await this.piAgent.prompt(userInput);
      await this.piAgent.waitForIdle();

      if (!finalOutput) {
        finalOutput = assistantText || "No output generated.";
      }

      // Count tool calls
      let toolCallsMade = 0;
      for (const msg of this.piAgent.state.messages) {
        const content = getMessageContent(msg);
        toolCallsMade += content.filter((c) => c.type === "toolCall").length;
      }

      emit({ type: "done", final_output: finalOutput });
      emit({ type: "session_end", session_id: sessionId });

      return {
        success: true,
        sessionId,
        events,
        finalOutput,
        stepsTaken: 0,
        toolCallsMade,
      };
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Unknown error";
      emit({ type: "error", message });
      emit({ type: "session_end", session_id: sessionId });

      return {
        success: false,
        sessionId,
        events,
        finalOutput: "",
        stepsTaken: 0,
        toolCallsMade: 0,
        error: message,
      };
    }
  }
}

// ---------------------------------------------------------------------------
// Tool adaptation helper
// ---------------------------------------------------------------------------

function adaptTool(
  tool: RegisteredTool,
): AgentTool<any> {
  const schema = jsonSchemaToTypeBox(tool.schema.parameters);

  return {
    name: tool.schema.name,
    description: tool.schema.description,
    label: tool.schema.name,
    parameters: schema,
    execute: async (
      _toolCallId: string,
      params: unknown,
      _signal?: AbortSignal,
    ): Promise<AgentToolResult<any>> => {
      try {
        const result = await tool.executor(params as Record<string, unknown>);
        return {
          content: [
            {
              type: "text",
              text:
                typeof result === "string" ? result : JSON.stringify(result),
            },
          ],
          details: result,
        };
      } catch (err) {
        const message =
          err instanceof Error ? err.message : "Unknown error";
        return {
          content: [{ type: "text", text: `Error: ${message}` }],
          details: { error: true, message },
        };
      }
    },
  };
}

/**
 * Convert a JSON Schema object to a TypeBox schema.
 */
function jsonSchemaToTypeBox(schema: Record<string, unknown>): TSchema {
  const type = schema.type as string;

  if (type === "object") {
    const properties = schema.properties as Record<string, unknown> | undefined;
    if (properties) {
      const objProps: Record<string, TSchema> = {};
      for (const [key, propSchema] of Object.entries(properties)) {
        objProps[key] = jsonSchemaToTypeBox(propSchema as Record<string, unknown>);
      }
      return Type.Object(objProps);
    }
    return Type.Object({});
  }

  if (type === "string") return Type.String();
  if (type === "number") return Type.Number();
  if (type === "integer") return Type.Integer();
  if (type === "boolean") return Type.Boolean();
  if (type === "array") {
    const items = schema.items as Record<string, unknown> | undefined;
    return Type.Array(items ? jsonSchemaToTypeBox(items) : Type.Any());
  }

  return Type.Any();
}
