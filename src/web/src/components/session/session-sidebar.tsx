import { FilePlus2, RotateCcw } from "lucide-react";
import type { Session, SessionStatus } from "../../lib/types";
import { formatTime } from "./helpers";

type Props = {
  sessions: Session[];
  sessionId: string | null;
  filter: SessionStatus;
  cursor: string | null;
  loading: boolean;
  onNavigate: (path: string) => void;
  onCreate: () => void;
  onFilter: (filter: SessionStatus) => void;
  onLoadMore: () => void;
  onGrowth: () => void;
  onReset: () => void;
};

export function SessionSidebar(props: Props) {
  return <aside className="flex w-[280px] shrink-0 flex-col bg-slate-950 text-slate-200" aria-label="会话列表">
      <div className="flex items-center justify-between border-b border-slate-800 px-4 py-4">
        <button type="button" onClick={() => props.onNavigate("/")} className="text-left text-sm font-semibold tracking-wide text-white">OfferPilot</button>
        <button type="button" onClick={props.onCreate} className="flex items-center gap-1 border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800" aria-label="新建会话" title="新建会话"><FilePlus2 size={14} aria-hidden="true" />新建</button>
      </div>
      <div className="flex gap-1 border-b border-slate-800 p-2 text-xs" role="tablist" aria-label="会话筛选">
        {(["active", "archived"] as const).map((value) => <button type="button" role="tab" aria-selected={props.filter === value} key={value} onClick={() => props.onFilter(value)} className={`flex-1 rounded px-2 py-1.5 ${props.filter === value ? "bg-slate-800 text-white" : "text-slate-400 hover:bg-slate-900"}`}>{value === "active" ? "活动" : "归档"}</button>)}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {props.loading && <p className="px-2 py-3 text-xs text-slate-500" role="status">加载中...</p>}
        {!props.loading && !props.sessions.length && <p className="px-2 py-3 text-xs text-slate-500">暂无会话</p>}
        {props.sessions.map((item) => <button type="button" key={item.id} onClick={() => props.onNavigate(`/session/${item.id}`)} className={`mb-1 w-full rounded px-3 py-2 text-left hover:bg-slate-900 ${item.id === props.sessionId ? "bg-slate-800" : ""}`}><span className="block truncate text-sm text-slate-200">{item.title || "未命名技术练习"}</span><span className="mt-1 block text-[11px] text-slate-500">{formatTime(item.updated_at)}</span></button>)}
        {props.cursor && <button type="button" onClick={props.onLoadMore} className="w-full px-2 py-2 text-xs text-slate-400 hover:text-white">加载更多</button>}
      </div>
      <div className="border-t border-slate-800 p-3"><button type="button" onClick={props.onGrowth} className="w-full px-3 py-2 text-left text-xs text-slate-400 hover:bg-slate-900 hover:text-white">成长概览</button><button type="button" onClick={props.onReset} className="mt-1 flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-slate-500 hover:bg-red-950 hover:text-red-200"><RotateCcw size={14} aria-hidden="true" />重置全部数据</button></div>
  </aside>;
}
