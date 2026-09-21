import { timingFromRun } from "../../lib/run-state";
import type { Run } from "../../lib/types";
import { runLabel, formatDuration } from "./helpers";

export function RunStatusCard({ run, phase, phaseData, reconnecting, onCancel }: { run: Run; phase: string; phaseData: Record<string, unknown>; reconnecting: boolean; onCancel: () => void }) {
  const timing = timingFromRun(run);
  return <div className="mx-auto mt-5 max-w-3xl border border-slate-200 bg-white px-4 py-3 text-xs"><div className="flex items-center justify-between gap-3"><div><span className="font-medium text-slate-800">{phase}</span><span className="ml-2 text-slate-500">{runLabel(run.type)}</span></div><button type="button" onClick={onCancel} className="text-red-700" aria-label="停止运行">停止</button></div><div aria-live="polite" className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-slate-500"><span>状态：{run.status}</span>{typeof phaseData.duration_ms === "number" && <span>阶段：{formatDuration(phaseData.duration_ms)}</span>}{typeof phaseData.received_chars === "number" && <span>已接收：{phaseData.received_chars} 字符</span>}{timing?.total_duration_ms !== undefined && <span>总耗时：{formatDuration(timing.total_duration_ms)}</span>}{reconnecting && <span className="text-accent-700">正在重连</span>}</div></div>;
}
