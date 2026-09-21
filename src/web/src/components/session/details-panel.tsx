import { Download } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { runEventLabel } from "../../lib/run-state";
import type { Followup, Report, Run, RunEvent, SessionSummary } from "../../lib/types";
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
      {props.selectedRunId === run.id && <RunEventDetails events={props.selectedRunEvents} />}
    </div>)}
    {!props.runs.length && <p className="text-xs text-slate-500">暂无任务记录。</p>}
  </section>;
}
