import { Archive, Check, Pencil, Trash2 } from "lucide-react";
import type { Session } from "../../lib/types";

type Props = { session: Session | null; titleDraft: string; editingTitle: boolean; activeLabel: string; onTitleChange: (value: string) => void; onSaveTitle: () => void; onRename: () => void; onArchive: () => void; onDelete: () => void; };

export function SessionHeader(props: Props) {
  return <header className="flex min-h-[65px] items-center justify-between border-b border-slate-200 bg-white px-5 py-3">
    <div className="min-w-0 flex-1">{props.session && props.editingTitle ? <input aria-label="会话标题" value={props.titleDraft} onChange={(event) => props.onTitleChange(event.target.value)} maxLength={120} className="w-full max-w-xl border-b border-primary-400 bg-transparent px-0 py-1 text-lg font-semibold outline-none" /> : <h1 className="truncate text-lg font-semibold">{props.session?.title || "新的技术面试练习"}</h1>}<p className="mt-1 text-xs text-slate-500">{props.activeLabel}</p></div>
    <div className="flex items-center gap-2 text-xs">
      {props.session && <div className="flex items-center gap-2">{props.editingTitle ? <button type="button" onClick={props.onSaveTitle} className="flex items-center gap-1 border border-primary-300 px-3 py-1.5 text-primary-700" title="保存标题"><Check size={14} aria-hidden="true" />保存</button> : <button type="button" onClick={props.onRename} className="flex items-center gap-1 border border-slate-300 px-3 py-1.5" title="重命名会话"><Pencil size={14} aria-hidden="true" />重命名</button>}<button type="button" onClick={props.onArchive} className="flex items-center gap-1 border border-slate-300 px-3 py-1.5" title={props.session.status === "archived" ? "恢复会话" : "归档会话"}><Archive size={14} aria-hidden="true" />{props.session.status === "archived" ? "恢复" : "归档"}</button><button type="button" onClick={props.onDelete} className="flex items-center gap-1 border border-red-200 px-3 py-1.5 text-red-700" title="删除会话"><Trash2 size={14} aria-hidden="true" />删除</button></div>}
    </div>
  </header>;
}
