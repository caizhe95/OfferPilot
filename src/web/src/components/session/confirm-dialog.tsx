import { useEffect, useRef, useState } from "react";
import { Trash2 } from "lucide-react";
import { useDialogFocus } from "../../hooks/use-dialog-focus";

export function ConfirmDialog({ action, onClose, onConfirm }: { action: "delete" | "reset"; onClose: () => void; onConfirm: () => Promise<void> }) {
  const [pending, setPending] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useDialogFocus(true, dialogRef, cancelRef, () => { if (!pending) closeRef.current(); });
  const confirm = async () => { if (pending) return; setPending(true); try { await onConfirm(); } finally { setPending(false); } };
  const reset = action === "reset";
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4"><div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="confirm-title" className="w-full max-w-md border border-slate-200 bg-white p-5 shadow-xl"><h2 id="confirm-title" className="text-lg font-semibold">{reset ? "重置全部训练数据" : "删除当前会话"}</h2><p className="mt-2 text-sm leading-6 text-slate-600">{reset ? "这会删除当前匿名身份的全部训练数据，匿名 Cookie 会保留。" : "这会永久删除消息、运行、事件、报告、音频和来源记忆。"}</p><div className="mt-5 flex justify-end gap-2"><button ref={cancelRef} type="button" onClick={onClose} disabled={pending} className="border border-slate-300 px-3 py-1.5 text-sm">取消</button><button type="button" onClick={() => void confirm()} disabled={pending} className="flex items-center gap-1 bg-red-700 px-3 py-1.5 text-sm text-white disabled:opacity-50"><Trash2 size={14} aria-hidden="true" />{pending ? "处理中..." : `确认${reset ? "重置" : "删除"}`}</button></div></div></div>;
}
