import type { RunEvent } from "./types";

type Handler = (event: RunEvent) => void;
const TERMINAL_EVENTS = new Set(["run_complete", "run_completed", "run_failed", "run_cancelled", "run_interrupted"]);

function isTerminalEvent(event: RunEvent): boolean {
  if (event.type !== "run_complete") return TERMINAL_EVENTS.has(event.type);
  return event.data.status === "completed" || event.data.status === "failed" || event.data.status === "cancelled";
}

export class SseReconnectError extends Error {
  readonly lastSequence: number;

  constructor(message: string, lastSequence: number) {
    super(message);
    this.name = "SseReconnectError";
    this.lastSequence = lastSequence;
  }
}

export class SseDecoder {
  private buffer = "";
  private dataLines: string[] = [];

  push(chunk: string, onEvent: Handler): void {
    this.buffer += chunk.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    let newline = this.buffer.indexOf("\n");
    while (newline >= 0) {
      const line = this.buffer.slice(0, newline);
      this.buffer = this.buffer.slice(newline + 1);
      if (line === "") this.dispatch(onEvent);
      else if (!line.startsWith(":") && line.startsWith("data:")) {
        const value = line.slice(5);
        this.dataLines.push(value.startsWith(" ") ? value.slice(1) : value);
      }
      newline = this.buffer.indexOf("\n");
    }
  }

  finish(onEvent: Handler): void {
    if (this.buffer && this.buffer.startsWith("data:")) this.dataLines.push(this.buffer.slice(5).trimStart());
    this.buffer = "";
    this.dispatch(onEvent);
  }

  private dispatch(onEvent: Handler): void {
    if (!this.dataLines.length) return;
    const raw = this.dataLines.join("\n");
    this.dataLines = [];
    try {
      const value = JSON.parse(raw) as RunEvent;
      if (typeof value.type === "string" && typeof value.session_id === "string" && typeof value.run_id === "string" && typeof value.sequence === "number" && Number.isFinite(value.sequence)) onEvent(value);
    } catch {
      // Ignore malformed or partial SSE payloads; the server snapshot will reconcile state.
    }
  }
}

export async function consumeSseResponse(response: Response, onEvent: Handler): Promise<boolean> {
  const reader = response.body?.getReader();
  if (!reader) throw new Error("SSE response body is unavailable");
  const decoder = new TextDecoder();
  const parser = new SseDecoder();
  let terminalSeen = false;
  const handleEvent = (event: RunEvent) => {
    if (isTerminalEvent(event)) terminalSeen = true;
    onEvent(event);
  };
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    parser.push(decoder.decode(value, { stream: true }), handleEvent);
  }
  parser.push(decoder.decode(), handleEvent);
  parser.finish(handleEvent);
  return terminalSeen;
}

export async function consumeSseWithRetry(runId: string, after: number, onEvent: Handler, signal?: AbortSignal, maxAttempts = 3): Promise<{ lastSequence: number; terminalSeen: boolean }> {
  let lastSequence = after;
  let attempt = 0;
  while (attempt < maxAttempts) {
    if (signal?.aborted) return { lastSequence, terminalSeen: false };
    try {
      const response = await fetch(`/api/runs/${runId}/stream?after=${lastSequence}`, { credentials: "include", signal, headers: lastSequence ? { "Last-Event-ID": String(lastSequence) } : undefined });
      if (!response.ok) throw new Error(`运行事件连接失败 (${response.status})`);
      const terminalSeen = await consumeSseResponse(response, (event) => { lastSequence = Math.max(lastSequence, event.sequence); onEvent(event); });
      if (terminalSeen) return { lastSequence, terminalSeen: true };
      throw new SseReconnectError("SSE stream ended before a terminal event", lastSequence);
    } catch (error) {
      if (signal?.aborted) return { lastSequence, terminalSeen: false };
      attempt += 1;
      if (attempt >= maxAttempts) {
        if (error instanceof SseReconnectError) throw error;
        throw new SseReconnectError(error instanceof Error ? error.message : "SSE stream failed", lastSequence);
      }
      await new Promise<void>((resolve, reject) => {
        const timer = globalThis.setTimeout(resolve, 400 * attempt);
        signal?.addEventListener("abort", () => { globalThis.clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); }, { once: true });
      });
    }
  }
  throw new SseReconnectError("SSE stream failed", lastSequence);
}
