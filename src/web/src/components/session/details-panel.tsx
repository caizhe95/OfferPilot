import { Download } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { runEventLabel } from "../../lib/run-state";
import type { Followup, Report, Run, RunCall, RunEvent, RunMetrics, SessionSummary } from "../../lib/types";
import { formatDuration, formatTime, runLabel } from "./helpers";

type Props = {
  tab: "summary" | "followups" | "reports" | "runs";
  summary: SessionSummary | null;
  followups: Followup[];
  reports: Report[];
  runs: Run[];
  selectedReport: Report | null;
  selectedRunId: string | null;
  selectedRunEvents: RunEvent[];
  selectedRunCalls: RunCall[];
  onSelectReport: (report: Report) => void;
  onSelectRun: (run: Run) => void;
  onFollowup: (item: Followup) => void;
  onExport: (report: Report) => void;
  onDownload: () => void;
  onRerun: (run: Run) => void;
};

function Info({ label, value }: { label: string; value: string }) {
  return <div><p className="text-[11px] uppercase tracking-wide text-slate-400">{label}</p><p className="mt-1 text-sm leading-6 text-slate-700">{value}</p></div>;
}

function RunEventDetails({ events }: { events: RunEvent[] }) {
  const visibleEvents = events.filter((event) => ["knowledge_merged", "context_built", "diagnosis_model_started", "diagnosis_model_progress", "diagnosis_model_fallback", "diagnosis_model_completed", "output_validated", "report_ready", "run_complete", "run_completed", "run_failed", "run_cancelled", "run_interrupted"].includes(event.type));
  return <details className="mt-2 border-t border-slate-100 pt-2">
    <summary className="cursor-pointer text-xs text-primary-700">技术指标</summary>
    <div className="mt-2 space-y-2 border-l border-slate-200 pl-3 text-[11px] text-slate-500">
      {visibleEvents.map((event) => <div key={`${event.run_id}-${event.sequence}`}>
        <p className="font-medium text-slate-700">{runEventLabel(event.type)}</p>
        <div className="flex flex-wrap gap-x-3 gap-y-1">
          {typeof event.data.duration_ms === "number" && <span>耗时 {formatDuration(event.data.duration_ms)}</span>}
          {typeof event.data.first_token_ms === "number" && <span>首 Token {formatDuration(event.data.first_token_ms)}</span>}
          {typeof event.data.generated_chars === "number" && <span>生成 {event.data.generated_chars} 字符</span>}
          {typeof event.data.received_chars === "number" && <span>已接收 {event.data.received_chars} 字符</span>}
          {typeof event.data.stream_mode === "string" && <span>模式 {event.data.stream_mode}</span>}
          {typeof event.data.input_tokens === "number" && <span>输入 Token {event.data.input_tokens}</span>}
          {typeof event.data.output_tokens === "number" && <span>输出 Token {event.data.output_tokens}</span>}
          {typeof event.data.finish_reason === "string" && <span>结束 {event.data.finish_reason}</span>}
          {typeof event.data.success === "boolean" && <span>{event.data.success ? "校验成功" : "校验失败"}</span>}
        </div>
      </div>)}
      {!visibleEvents.length && <p>暂无可展示阶段指标。</p>}
    </div>
  </details>;
}

const CALL_LABELS = { llm: "LLM", embedding: "Embedding", asr: "ASR", tool: "Tool" } as const;

function estimatedCost(metrics: RunMetrics): string {
  const known = Object.entries(metrics.estimated_costs).map(([currency, amount]) => `${amount} ${currency}`);
  if (metrics.cost_unknown_reasons.length) return known.length ? `${known.join("、")}，部分未知` : "未知";
  return known.join("、") || (metrics.call_count ? "无 Provider 费用" : "-");
}

function RunMetricsDetails({ run, calls, events }: { run: Run; calls: RunCall[]; events: RunEvent[] }) {
  const metrics = run.metrics;
  return <div className="mt-3 space-y-3 border-t border-slate-200 pt-3 text-[11px] text-slate-600">
    {metrics ? <>
      <div className="grid grid-cols-2 gap-x-3 gap-y-2">
        {(Object.keys(CALL_LABELS) as Array<keyof typeof CALL_LABELS>).map((type) => <span key={type}>{CALL_LABELS[type]} {metrics.calls_by_type[type]?.calls || 0}</span>)}
        <span>成功 {metrics.success_count}</span><span>失败 {metrics.failure_count}</span>
        <span>重试 {metrics.retry_count}</span><span>Token {metrics.total_tokens}</span>
        <span>Token 未知 {metrics.unknown_token_calls}</span><span>音频 {Number(metrics.audio_seconds || 0).toFixed(1)} s</span>
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-2 border-t border-slate-100 pt-2">
        <span>排队 {formatDuration(run.timing?.queue_duration_ms)}</span><span>审批 {formatDuration(run.timing?.approval_wait_ms)}</span>
        <span>执行 {formatDuration(run.timing?.active_duration_ms)}</span><span>总计 {formatDuration(run.timing?.total_duration_ms)}</span>
      </div>
      <p className="border-t border-slate-100 pt-2"><span className="text-slate-400">预估费用：</span>{estimatedCost(metrics)}</p>
    </> : <p>暂无聚合指标。</p>}
    <div className="space-y-2 border-t border-slate-100 pt-2">
      <p className="font-medium text-slate-700">调用明细</p>
      {calls.map((call) => <details key={call.id}>
        <summary className="cursor-pointer text-slate-700">{CALL_LABELS[call.call_type]} · {call.operation_name || call.logical_call_id} · {call.status}</summary>
        <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-1 border-l border-slate-200 pl-3">
          <span>耗时 {formatDuration(call.duration_ms)}</span><span>尝试 {call.attempt_count}</span>
          <span>重试 {call.retry_count}</span><span>总 Token {call.call_type === "tool" || call.call_type === "asr" ? "不适用" : call.total_tokens ?? "未知"}</span>
          {call.first_token_ms !== null && <span>首 Token {formatDuration(call.first_token_ms)}</span>}
          {call.audio_seconds !== null && <span>音频 {call.audio_seconds.toFixed(1)} s</span>}
          <span className="col-span-2">预估费用 {call.call_type === "tool" ? "不计费" : call.estimated_cost && call.price_currency ? `${call.estimated_cost} ${call.price_currency}` : "未知"}</span>
          {call.error_category && <span className="col-span-2 text-red-700">错误 {call.error_category}</span>}
        </div>
      </details>)}
      {!calls.length && <p>暂无调用明细。</p>}
    </div>
    <RunEventDetails events={events} />
  </div>;
}

export function DetailsPanel(props: Props) {
  const data = props.summary?.summary_json || {};
  if (props.tab === "summary") return <section className="space-y-4"><h2 className="text-sm font-semibold">会话摘要</h2><Info label="当前目标" value={data.current_goal || "尚未形成目标"} /><Info label="反复薄弱点" value={(data.recurring_weaknesses || []).join("、") || "完成两次诊断后显示"} /><Info label="已掌握主题" value={(data.mastered_topics || []).join("、") || "连续两次覆盖后显示"} /><Info label="最近诊断" value={data.latest_diagnosis ? String(data.latest_diagnosis.question || "已生成") : "暂无"} /></section>;
  if (props.tab === "followups") return <section className="space-y-3"><h2 className="text-sm font-semibold">待回答追问</h2>{props.followups.filter((item) => item.status === "pending").map((item) => <div key={item.id} className="border-t border-slate-100 pt-3"><p className="text-sm leading-6">{item.question}</p><p className="mt-1 text-xs text-slate-500">{item.reason}</p><button type="button" onClick={() => props.onFollowup(item)} className="mt-2 text-xs text-primary-700">回答追问</button></div>)}{!props.followups.some((item) => item.status === "pending") && <p className="text-xs text-slate-500">暂无待回答追问。</p>}</section>;
  if (props.tab === "reports") return <section className="space-y-3">
    <h2 className="text-sm font-semibold">报告历史</h2>
    {props.reports.map((report) => <div key={report.id} className={`border-t border-slate-100 pt-3 ${props.selectedReport?.id === report.id ? "bg-slate-50" : ""}`}>
      <button type="button" onClick={() => props.onSelectReport(report)} className="w-full text-left"><p className="text-sm">{report.question}</p><p className="mt-1 text-xs text-slate-500">评分：{report.overall_score ?? "-"} · {formatTime(report.created_at)}</p></button>
      <div className="mt-2 flex gap-3 text-xs">
        <button type="button" onClick={() => props.onExport(report)} className="text-primary-700">导出 Markdown</button>
        {props.selectedReport?.id === report.id && <button type="button" onClick={props.onDownload} disabled={!props.selectedReport.report_markdown} className="flex items-center gap-1 text-primary-700 disabled:text-slate-400"><Download size={13} aria-hidden="true" />下载</button>}
      </div>
    </div>)}
    {props.selectedReport?.report_markdown && <div className="markdown-body mt-4 max-h-[45vh] overflow-y-auto border-t border-slate-200 pt-4 text-xs leading-6"><ReactMarkdown remarkPlugins={[remarkGfm]}>{props.selectedReport.report_markdown}</ReactMarkdown></div>}
    {!props.reports.length && <p className="text-xs text-slate-500">完成一次正式诊断后，这里会生成结构化报告。</p>}
  </section>;
  return <section className="space-y-3">
    <h2 className="text-sm font-semibold">任务记录</h2>
    {props.runs.slice(0, 20).map((run) => <div key={run.id} className="border-t border-slate-100 pt-3">
      <div className="flex items-center justify-between gap-2 text-xs"><span>{runLabel(run.type)}</span><span className="text-slate-500">{run.status}</span></div>
      <p className="mt-1 text-[11px] text-slate-500">创建：{formatTime(run.created_at)}</p>
      {run.error_code && <p className="mt-1 text-[11px] text-red-700">错误：{run.error_code}</p>}
      {run.timing?.total_duration_ms !== undefined && <p className="mt-1 text-[11px] text-slate-500">耗时：{formatDuration(run.timing.total_duration_ms)}</p>}
      <div className="mt-2 flex items-center gap-3 text-xs"><button type="button" onClick={() => props.onSelectRun(run)} className="text-primary-700">{props.selectedRunId === run.id ? "收起指标" : "查看指标"}</button>{["failed", "cancelled", "interrupted"].includes(run.status) && <button type="button" onClick={() => props.onRerun(run)} className="text-primary-700">重新运行</button>}</div>
      {props.selectedRunId === run.id && <RunMetricsDetails run={run} calls={props.selectedRunCalls} events={props.selectedRunEvents} />}
    </div>)}
    {!props.runs.length && <p className="text-xs text-slate-500">暂无任务记录。</p>}
  </section>;
}
