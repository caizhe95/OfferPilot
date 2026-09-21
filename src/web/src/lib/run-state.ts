import type { Approval, Run, RunEvent, RunStatus, Timing } from "../lib/types";

export type RunViewState = {
  run: Run | null;
  events: RunEvent[];
  phase: string;
  phaseData: Record<string, unknown>;
  approval: Approval | null;
  lastSequence: number;
  reconnecting: boolean;
};

export const initialRunViewState: RunViewState = {
  run: null,
  events: [],
  phase: "等待开始",
  phaseData: {},
  approval: null,
  lastSequence: 0,
  reconnecting: false,
};

export type RunStateAction =
  | { type: "reset" }
  | { type: "set_run"; run: Run }
  | { type: "refresh_run"; run: Run }
  | { type: "event"; event: RunEvent };

const phaseLabels: Record<string, string> = {
  run_created: "已创建",
  run_started: "开始运行",
  run_resumed: "已恢复",
  knowledge_merged: "知识检索完成",
  context_built: "上下文已构建",
  diagnosis_started: "诊断已开始",
  diagnosis_model_started: "模型开始诊断",
  diagnosis_model_progress: "诊断进行中",
  diagnosis_model_fallback: "切换兼容模式",
  diagnosis_model_completed: "模型诊断完成",
  output_validated: "输出校验完成",
  report_ready: "报告已生成",
  run_complete: "运行已收敛",
  approval_required: "等待授权",
  run_waiting_approval: "等待授权",
  tool_call: "正在调用受控工具",
  tool_result: "受控工具已返回",
  tool_result_complete: "受控工具已完成",
  final_response: "正在整理回答",
  run_completed: "已完成",
  run_failed: "运行失败",
  run_cancelled: "已取消",
  run_interrupted: "已中断",
};

export function runEventLabel(type: string): string {
  return phaseLabels[type] || "运行事件";
}

function approvalFromEvent(event: RunEvent): Approval | null {
  if (event.type !== "approval_required") return null;
  const data = event.data;
  return {
    id: String(data.approval_id || ""),
    run_id: event.run_id,
    tool_name: String(data.tool_name || "受控工具"),
    risk_level: String(data.risk_level || "medium"),
    flow_kind: data.flow_kind === "audio" || data.flow_kind === "export" ? data.flow_kind : "coach",
    public_params: typeof data.public_params === "object" && data.public_params !== null ? data.public_params as Record<string, unknown> : {},
  };
}

export function reduceRunState(state: RunViewState, action: RunStateAction): RunViewState {
  if (action.type === "reset") return initialRunViewState;
  if (action.type === "set_run") {
    return {
      ...initialRunViewState,
      run: action.run,
      approval: action.run.pending_approval || null,
    };
  }
  if (action.type === "refresh_run") {
    if (state.run?.id !== action.run.id) return state;
    return { ...state, run: action.run, approval: action.run.pending_approval || null };
  }
  const { event } = action;
  if (event.run_id !== state.run?.id && state.run) return state;
  const eventKey = `${event.trace_id || event.run_id}:${event.sequence}`;
  if (state.events.some((item) => `${item.trace_id || item.run_id}:${item.sequence}` === eventKey)) return state;
  const phase = phaseLabels[event.type] || state.phase;
  const approval = event.type === "approval_required" ? approvalFromEvent(event) : event.type === "run_resumed" ? null : state.approval;
  const nextRun: Run | null = state.run ? { ...state.run, status: statusFromEvent(state.run.status, event) } : state.run;
  return {
    ...state,
    run: nextRun,
    events: [...state.events, event],
    phase,
    phaseData: event.data,
    approval,
    lastSequence: Math.max(state.lastSequence, event.sequence),
  };
}

function statusFromEvent(current: RunStatus, event: RunEvent): RunStatus {
  const eventType = event.type;
  if (eventType === "run_started" || eventType === "run_resumed") return "running";
  if (eventType === "approval_required") return "waiting_approval";
  if (eventType === "run_complete") {
    const status = event.data.status;
    if (status === "completed" || status === "waiting_approval" || status === "failed" || status === "cancelled") return status;
  }
  if (eventType === "run_completed") return "completed";
  if (eventType === "run_failed") return "failed";
  if (eventType === "run_cancelled") return "cancelled";
  if (eventType === "run_interrupted") return "interrupted";
  return current;
}

export function timingFromRun(run: Run | null): Timing | null {
  if (!run) return null;
  if (run.timing) return run.timing;
  const timing = run.result?.timing;
  return timing && typeof timing === "object" ? timing as Timing : null;
}
