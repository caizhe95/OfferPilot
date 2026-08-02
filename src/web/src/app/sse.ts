export type RunCompleteStatus = "completed" | "waiting_approval" | "failed" | "cancelled";

type SseBase = {
  session_id: string;
  trace_id: string;
  sequence: number;
};

export type SessionStartEvent = SseBase & { type: "session_start"; mode?: "coach" | "diagnosis" };
export type DiagnosisStartedEvent = SseBase & { type: "diagnosis_started" };
export type TextDeltaEvent = SseBase & { type: "text_delta"; content: string };
export type ToolCallEvent = SseBase & { type: "tool_call"; tool_name: string; params?: Record<string, unknown> };
export type ToolResultEvent = SseBase & { type: "tool_result"; tool_name: string; result?: unknown };
export type PermissionRequiredEvent = SseBase & {
  type: "permission_required";
  request_id: string;
  tool_name: string;
  risk_level: string;
  params?: Record<string, unknown>;
  message?: string;
};
export type ReportReadyEvent = SseBase & { type: "report_ready"; report_id: string; overall_score?: number };
export type FinalResponseEvent = SseBase & { type: "final_response"; content: string; termination_reason?: string };
export type ErrorEvent = SseBase & { type: "error"; code?: string; message: string };
export type RunCompleteEvent = SseBase & {
  type: "run_complete";
  status: RunCompleteStatus;
  success?: boolean;
  waiting_approval?: boolean;
};

export type CoachSseEvent =
  | SessionStartEvent
  | DiagnosisStartedEvent
  | TextDeltaEvent
  | ToolCallEvent
  | ToolResultEvent
  | PermissionRequiredEvent
  | ReportReadyEvent
  | FinalResponseEvent
  | ErrorEvent
  | RunCompleteEvent;

export type UnknownSseEvent = { type: "unknown"; raw: unknown };
export type ParsedSseEvent = CoachSseEvent | UnknownSseEvent;

type EventHandler = (event: ParsedSseEvent) => void;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasBase(value: Record<string, unknown>): value is Record<string, unknown> & SseBase {
  return (
    typeof value.session_id === "string" &&
    typeof value.trace_id === "string" &&
    typeof value.sequence === "number" &&
    Number.isSafeInteger(value.sequence)
  );
}

function asOptionalParams(value: unknown): Record<string, unknown> | undefined {
  return isRecord(value) ? value : undefined;
}

export function parseSseEvent(value: unknown): ParsedSseEvent {
  if (!isRecord(value) || !hasBase(value) || typeof value.type !== "string") {
    return { type: "unknown", raw: value };
  }

  switch (value.type) {
    case "session_start":
      return { ...value, type: "session_start", mode: value.mode === "diagnosis" ? "diagnosis" : "coach" };
    case "diagnosis_started":
      return { ...value, type: "diagnosis_started" };
    case "text_delta":
      return typeof value.content === "string"
        ? { ...value, type: "text_delta", content: value.content }
        : { type: "unknown", raw: value };
    case "tool_call":
      return typeof value.tool_name === "string"
        ? { ...value, type: "tool_call", tool_name: value.tool_name, params: asOptionalParams(value.params) }
        : { type: "unknown", raw: value };
    case "tool_result":
      return typeof value.tool_name === "string"
        ? { ...value, type: "tool_result", tool_name: value.tool_name, result: value.result }
        : { type: "unknown", raw: value };
    case "permission_required":
      return (
        typeof value.request_id === "string" &&
        typeof value.tool_name === "string" &&
        typeof value.risk_level === "string"
      )
        ? {
            ...value,
            type: "permission_required",
            request_id: value.request_id,
            tool_name: value.tool_name,
            risk_level: value.risk_level,
            params: asOptionalParams(value.params),
            message: typeof value.message === "string" ? value.message : undefined,
          }
        : { type: "unknown", raw: value };
    case "report_ready":
      return typeof value.report_id === "string"
        ? {
            ...value,
            type: "report_ready",
            report_id: value.report_id,
            overall_score: typeof value.overall_score === "number" ? value.overall_score : undefined,
          }
        : { type: "unknown", raw: value };
    case "final_response":
      return typeof value.content === "string"
        ? {
            ...value,
            type: "final_response",
            content: value.content,
            termination_reason: typeof value.termination_reason === "string" ? value.termination_reason : undefined,
          }
        : { type: "unknown", raw: value };
    case "error":
      return typeof value.message === "string"
        ? {
            ...value,
            type: "error",
            code: typeof value.code === "string" ? value.code : undefined,
            message: value.message,
          }
        : { type: "unknown", raw: value };
    case "run_complete":
      return ["completed", "waiting_approval", "failed", "cancelled"].includes(String(value.status))
        ? {
            ...value,
            type: "run_complete",
            status: value.status as RunCompleteStatus,
            success: typeof value.success === "boolean" ? value.success : undefined,
            waiting_approval: typeof value.waiting_approval === "boolean" ? value.waiting_approval : undefined,
          }
        : { type: "unknown", raw: value };
    default:
      return { type: "unknown", raw: value };
  }
}

/** Decode SSE framing incrementally. Transport comments such as `: ping` are intentionally ignored. */
export class SseDecoder {
  private buffer = "";
  private dataLines: string[] = [];

  push(chunk: string, onEvent: EventHandler): void {
    this.buffer += chunk.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    let newline = this.buffer.indexOf("\n");
    while (newline >= 0) {
      const line = this.buffer.slice(0, newline);
      this.buffer = this.buffer.slice(newline + 1);
      this.consumeLine(line, onEvent);
      newline = this.buffer.indexOf("\n");
    }
  }

  finish(onEvent: EventHandler): void {
    if (this.buffer) this.consumeLine(this.buffer, onEvent);
    this.buffer = "";
    this.dispatch(onEvent);
  }

  private consumeLine(line: string, onEvent: EventHandler): void {
    if (line === "") {
      this.dispatch(onEvent);
      return;
    }
    if (line.startsWith(":")) return;
    if (!line.startsWith("data:")) return;
    const data = line.slice(5);
    this.dataLines.push(data.startsWith(" ") ? data.slice(1) : data);
  }

  private dispatch(onEvent: EventHandler): void {
    if (this.dataLines.length === 0) return;
    const payload = this.dataLines.join("\n");
    this.dataLines = [];
    try {
      onEvent(parseSseEvent(JSON.parse(payload)));
    } catch {
      onEvent({ type: "unknown", raw: payload });
    }
  }
}

export async function consumeSseResponse(response: Response, onEvent: EventHandler): Promise<void> {
  const reader = response.body?.getReader();
  if (!reader) throw new Error("SSE response body is unavailable");
  const decoder = new TextDecoder();
  const parser = new SseDecoder();
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    parser.push(decoder.decode(value, { stream: true }), onEvent);
  }
  parser.push(decoder.decode(), onEvent);
  parser.finish(onEvent);
}
