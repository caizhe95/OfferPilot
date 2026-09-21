export type SessionStatus = "active" | "archived";
export type RunType = "coach" | "diagnosis" | "audio_transcription" | "report_export";
export type RunStatus = "pending" | "running" | "waiting_approval" | "completed" | "failed" | "cancelled" | "interrupted";

export type Session = {
  id: string;
  title: string;
  title_source: "auto" | "manual";
  status: SessionStatus;
  created_at?: string;
  updated_at?: string;
};

export type Message = {
  id?: number;
  session_id?: string;
  run_id?: string | null;
  role: "user" | "assistant" | "system" | "tool";
  kind?: string;
  content: string;
  created_at?: string;
};

export type Timing = {
  queue_duration_ms?: number;
  approval_wait_ms?: number;
  active_duration_ms?: number;
  total_duration_ms?: number;
};

export type RunCallType = "llm" | "embedding" | "asr" | "tool";

export type RunCall = {
  id: number;
  logical_call_id: string;
  parent_call_id: string;
  call_type: RunCallType;
  provider: string;
  model: string;
  operation_name: string;
  status: "running" | "succeeded" | "failed" | "cancelled";
  started_at: string;
  ended_at: string;
  duration_ms: number;
  attempt_count: number;
  retry_count: number;
  error_category: string;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  reasoning_tokens: number | null;
  first_token_ms: number | null;
  audio_seconds: number | null;
  price_currency: string;
  audio_price_per_minute: string;
  estimated_cost: string;
  usage_known: boolean;
  price_known: boolean;
  unknown_reason: string;
};

export type RunMetrics = {
  calls_by_type: Record<RunCallType, { calls: number; succeeded: number; failed: number; retries: number }>;
  call_count: number;
  success_count: number;
  failure_count: number;
  retry_count: number;
  total_tokens: number;
  unknown_token_calls: number;
  audio_seconds: string;
  estimated_costs: Record<string, string>;
  cost_unknown_reasons: string[];
};

export type Approval = {
  id: string;
  run_id: string;
  tool_name: string;
  risk_level: string;
  flow_kind?: "coach" | "audio" | "export";
  public_params?: Record<string, unknown>;
  decision?: "approve" | "deny" | null;
  decision_reused?: boolean;
};

export type RunEvent = {
  type: string;
  session_id: string;
  trace_id?: string;
  run_id: string;
  sequence: number;
  created_at: string;
  data: Record<string, unknown>;
};

export type CoachState = {
  session_id: string;
  run: Run | null;
  trace: RunEvent[];
  approval: Approval | null;
};

export type Run = {
  id: string;
  run_id?: string;
  session_id: string;
  type: RunType;
  run_type?: RunType;
  status: RunStatus;
  input?: Record<string, unknown>;
  state?: Record<string, unknown>;
  result?: Record<string, unknown>;
  error_code?: string | null;
  error_message?: string | null;
  created_at?: string;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at?: string;
  timing?: Timing | null;
  metrics?: RunMetrics;
  pending_approval?: Approval | null;
  last_event_sequence?: number;
};

export type Followup = {
  id: string;
  question: string;
  reason: string;
  status: "pending" | "in_progress" | "answered";
  exam_point_id?: string | null;
};

export type Report = {
  id: string;
  run_id?: string;
  question: string;
  overall_score: number | null;
  created_at: string;
  report_markdown?: string;
};

export type SessionSummary = {
  session_id?: string;
  summary_version?: number;
  source_message_id?: number | null;
  summary_json?: {
    current_goal?: string;
    recurring_weaknesses?: string[];
    mastered_topics?: string[];
    pending_followup_ids?: string[];
    latest_diagnosis?: Record<string, unknown> | null;
  };
  updated_at?: string;
};

export type Growth = {
  summary_json?: {
    recurring_weaknesses?: string[];
    mastered_topics?: string[];
    observed_points?: number;
    diagnosis_count?: number;
  };
  updated_at?: string;
};

export const TERMINAL_RUN_STATES = new Set<RunStatus>(["completed", "failed", "cancelled", "interrupted"]);

export function isTerminalRun(run?: Run | null): boolean {
  return Boolean(run && TERMINAL_RUN_STATES.has(run.status));
}

export function runLabel(type: RunType): string {
  return { coach: "练习", diagnosis: "正式诊断", audio_transcription: "音频转写", report_export: "报告导出" }[type];
}
