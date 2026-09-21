import { useRef, useState } from "react";
import type { Approval } from "../../lib/types";
import { useDialogFocus } from "../../hooks/use-dialog-focus";
import { approvalText } from "./helpers";

export function ApprovalDialog({ approval, onDecision }: { approval: Approval; onDecision: (decision: "approve" | "deny") => Promise<void> }) {
  const [pending, setPending] = useState<"approve" | "deny" | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const approveRef = useRef<HTMLButtonElement>(null);
  useDialogFocus(true, dialogRef, approveRef, () => { if (!pending) void decide("deny"); });
  const decide = async (decision: "approve" | "deny") => { if (pending) return; setPending(decision); try { await onDecision(decision); } finally { setPending(null); } };
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4"><div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="approval-title" className="w-full max-w-md border border-slate-200 bg-white p-5 shadow-xl"><p className="text-xs uppercase tracking-wide text-accent-700">需要授权</p><h2 id="approval-title" className="mt-2 text-lg font-semibold">{approval.tool_name}</h2><p className="mt-2 text-sm text-slate-600">风险等级：{approval.risk_level}</p><p className="mt-1 text-sm text-slate-600">{approvalText(approval)}</p><div className="mt-5 flex justify-end gap-2"><button type="button" onClick={() => void decide("deny")} disabled={Boolean(pending)} className="border border-red-300 px-3 py-1.5 text-sm text-red-700 disabled:opacity-50">{pending === "deny" ? "处理中..." : "拒绝"}</button><button ref={approveRef} type="button" onClick={() => void decide("approve")} disabled={Boolean(pending)} className="bg-slate-900 px-3 py-1.5 text-sm text-white disabled:bg-slate-300">{pending === "approve" ? "处理中..." : "允许并继续"}</button></div></div></div>;
}
