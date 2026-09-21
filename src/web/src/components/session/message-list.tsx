import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message } from "../../lib/types";

export function MessageList({ messages, loading, error, notice, activeRun, runStatus, bottomRef }: { messages: Message[]; loading: boolean; error: string; notice: string; activeRun: React.ReactNode; runStatus: React.ReactNode; bottomRef: React.RefObject<HTMLDivElement> }) {
  const visibleMessages = messages.filter((message) => message.role === "user" || message.role === "assistant");
  return <div className="flex-1 overflow-y-auto px-4 py-6 sm:px-8">
    {error && <div role="alert" className="mx-auto mb-4 max-w-3xl border-l-2 border-red-500 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}
    {notice && <div role="status" className="mx-auto mb-4 max-w-3xl border-l-2 border-primary-500 bg-primary-50 px-4 py-3 text-sm text-primary-800">{notice}</div>}
    {loading && <p className="mx-auto max-w-3xl py-10 text-center text-sm text-slate-500" role="status">正在加载会话...</p>}
    {!loading && !visibleMessages.length && <div className="mx-auto flex min-h-[42vh] max-w-3xl items-center justify-center text-center"><div><p className="text-2xl font-semibold text-slate-800">开始一次技术面试练习</p><p className="mt-2 text-sm text-slate-500">输入练习目标，或切换到正式诊断。</p></div></div>}
    <div className="mx-auto max-w-3xl space-y-5">{visibleMessages.map((message, index) => <article key={`${message.id || "local"}-${index}`} className={message.role === "user" ? "ml-auto max-w-[88%]" : "max-w-[92%]"}><p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-slate-400">{message.role === "user" ? "你" : "OfferPilot"}</p><div className={message.role === "user" ? "whitespace-pre-wrap border border-primary-100 bg-primary-50 px-4 py-3 text-sm leading-6" : "markdown-body border-l-2 border-slate-200 px-4 py-1 text-sm leading-6"}>{message.role === "assistant" ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown> : message.content}</div></article>)}</div>
    {activeRun}
    {runStatus}
    <div ref={bottomRef} />
  </div>;
}
