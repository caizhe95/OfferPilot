/** Agent event types emitted during execution. */
export type AgentEvent =
  | { type: "tool_call"; tool_name: string; params: Record<string, unknown> }
  | { type: "tool_result"; tool_name: string; result: unknown }
  | { type: "text_delta"; content: string }
  | { type: "error"; message: string }
  | { type: "done"; final_output: string }
  | { type: "session_start"; session_id: string }
  | { type: "session_end"; session_id: string };

/** Message role in conversation. */
export type MessageRole = "system" | "user" | "assistant" | "tool";

/** A chat message. */
export interface ChatMessage {
  role: MessageRole;
  content: string;
}

/** Tool definition schema. */
export interface ToolSchema {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  risk_level: "low" | "medium" | "high" | "critical";
}

/** Tool execution function. */
export type ToolExecutor = (
  params: Record<string, unknown>,
) => Promise<unknown>;

/** Registered tool with schema and executor. */
export interface RegisteredTool {
  schema: ToolSchema;
  executor: ToolExecutor;
}

/** Agent run result. */
export interface AgentResult {
  success: boolean;
  sessionId: string;
  events: AgentEvent[];
  finalOutput: string;
  stepsTaken: number;
  toolCallsMade: number;
  error?: string;
}
