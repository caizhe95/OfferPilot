import type { Growth, Run, Session } from "./types";

export const API = "/api";

export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

function errorMessage(payload: unknown, fallback: string): { message: string; code?: string } {
  if (!payload || typeof payload !== "object") return { message: fallback };
  const value = payload as { detail?: unknown; error?: { code?: string; message?: string } };
  const code = value.error?.code;
  const messages: Record<string, string> = {
    llm_unavailable: "模型服务未配置或暂不可用。",
    output_check_failed: "模型输出未通过结构检查，请稍后重试。",
    llm_output_truncated: "模型输出被截断，请稍后重试。",
    session_run_conflict: "当前会话已有运行中的任务。",
    active_run_not_settled: "运行尚未收敛，请稍后再试。",
    approval_decision_conflict: "审批已经作出相反决定。",
    session_archived_read_only: "归档会话为只读状态，请先恢复会话。",
    invalid_cursor: "历史分页游标无效，请重新加载列表。",
  };
  return { message: (code && messages[code]) || value.error?.message || (typeof value.detail === "string" ? value.detail : fallback), code };
}

export function createIdempotencyKey(): string {
  return typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (!(init.body instanceof FormData) && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(`${API}${path}`, { ...init, credentials: "include", headers });
  if (!response.ok) {
    const parsed = errorMessage(await response.json().catch(() => null), `请求失败 (${response.status})`);
    throw new ApiError(parsed.message, response.status, parsed.code);
  }
  if (response.status === 204) return null as T;
  return response.json() as Promise<T>;
}

export async function createSession(): Promise<Session> {
  return requestJson<Session>("/sessions", { method: "POST", body: JSON.stringify({}) });
}

export async function createRun(sessionId: string, type: Run["type"], input: Record<string, unknown>): Promise<{ run: Run; reused?: boolean }> {
  return requestJson(`/sessions/${sessionId}/runs`, {
    method: "POST",
    headers: { "Idempotency-Key": createIdempotencyKey() },
    body: JSON.stringify({ type, input }),
  });
}

export async function createReportExport(sessionId: string, reportId: string): Promise<{ run: Run; reused?: boolean }> {
  return requestJson("/coach/reports/export", {
    method: "POST",
    headers: { "Idempotency-Key": createIdempotencyKey() },
    body: JSON.stringify({ session_id: sessionId, report_id: reportId }),
  });
}

export async function uploadAudio(sessionId: string, file: File): Promise<{ upload: { id: string } }> {
  const body = new FormData();
  body.append("file", file);
  return requestJson(`/sessions/${sessionId}/audio-uploads`, { method: "POST", body });
}

export async function getGrowth(): Promise<Growth> {
  return requestJson<Growth>("/profile/growth");
}
