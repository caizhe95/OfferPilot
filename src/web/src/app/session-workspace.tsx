"use client";

import { useRouter } from "next/navigation";
import { ApprovalDialog } from "../components/session/approval-dialog";
import { AudioTools } from "../components/session/audio-tools";
import { Composer } from "../components/session/composer";
import { ConfirmDialog } from "../components/session/confirm-dialog";
import { DetailsPanel } from "../components/session/details-panel";
import { MessageList } from "../components/session/message-list";
import { RunStatusCard } from "../components/session/run-status-card";
import { SessionHeader } from "../components/session/session-header";
import { SessionSidebar } from "../components/session/session-sidebar";
import { useWorkspaceController, type DetailTab } from "../hooks/use-workspace-controller";
import type { SessionStatus } from "../lib/types";

type Props = { sessionId: string | null };

const DETAIL_TABS: DetailTab[] = ["summary", "followups", "reports", "runs"];
const DETAIL_LABELS: Record<DetailTab, string> = { summary: "摘要", followups: "追问", reports: "报告", runs: "任务" };

function DetailTabs({ active, onChange }: { active: DetailTab; onChange: (tab: DetailTab) => void }) {
  return <div className="flex gap-1 border-b border-slate-200 p-2 text-[11px]" aria-label="会话详情分类">
    {DETAIL_TABS.map((tab) => (
      <button
        type="button"
        role="tab"
        aria-selected={active === tab}
        key={tab}
        onClick={() => onChange(tab)}
        className={`px-3 py-1.5 ${active === tab ? "bg-slate-900 text-white" : "text-slate-500 hover:bg-slate-100"}`}
      >
        {DETAIL_LABELS[tab]}
      </button>
    ))}
  </div>;
}

function DesktopWorkspace({ sessionId }: Props) {
  const router = useRouter();
  const workspace = useWorkspaceController({ sessionId });
  const detailContent = <DetailsPanel
    tab={workspace.detailTab}
    summary={workspace.summary}
    followups={workspace.followups}
    reports={workspace.reports}
    runs={workspace.runs}
    selectedReport={workspace.selectedReport}
    selectedRunId={workspace.selectedRunId}
    selectedRunEvents={workspace.selectedRunEvents}
    selectedRunCalls={workspace.selectedRunCalls}
    onSelectReport={(report) => { void workspace.selectReport(report); }}
    onSelectRun={(run) => { void workspace.selectRun(run); }}
    onFollowup={workspace.selectFollowup}
    onExport={(report) => { void workspace.exportReport(report); }}
    onDownload={workspace.downloadSelectedReport}
    onRerun={(run) => { void workspace.rerun(run); }}
  />;

  return <div className="flex min-h-screen bg-slate-100 text-slate-900">
    <SessionSidebar
      sessions={workspace.sessions}
      sessionId={sessionId}
      filter={workspace.sessionFilter}
      cursor={workspace.sessionCursor}
      loading={workspace.sidebarLoading}
      onNavigate={(path) => router.push(path)}
      onCreate={() => void workspace.createSessionAndNavigate()}
      onFilter={(filter: SessionStatus) => workspace.changeSessionFilter(filter)}
      onLoadMore={() => void workspace.loadMoreSessions()}
      onGrowth={() => router.push("/growth")}
      onReset={() => workspace.setConfirmAction("reset")}
    />
    <main className="flex min-h-0 min-w-0 flex-1 flex-col">
      <SessionHeader
        session={workspace.session}
        titleDraft={workspace.titleDraft}
        editingTitle={workspace.editingTitle}
        activeLabel={workspace.activeRun ? `${workspace.activeRun.type} · ${workspace.runView.phase}` : workspace.session?.status === "archived" ? "归档会话，只读查看" : "准备好开始下一轮练习"}
        onTitleChange={workspace.setTitleDraft}
        onSaveTitle={workspace.saveTitle}
        onRename={() => workspace.setEditingTitle(true)}
        onArchive={() => void workspace.archiveSession(workspace.session?.status !== "archived")}
        onDelete={() => workspace.setConfirmAction("delete")}
      />
      <div className="flex min-h-0 flex-1">
        <section className="flex min-h-0 min-w-0 flex-1 flex-col">
          <MessageList
            messages={workspace.messages}
            loading={workspace.loading && Boolean(sessionId)}
            error={workspace.error}
            notice={workspace.notice}
            activeRun={null}
            runStatus={workspace.activeRun ? <RunStatusCard run={workspace.activeRun} phase={workspace.runView.phase} phaseData={workspace.runView.phaseData} reconnecting={workspace.reconnecting} onCancel={workspace.cancelRun} /> : null}
            bottomRef={workspace.bottomRef}
          />
          <AudioTools
            session={workspace.session}
            file={workspace.audioFile}
            setFile={workspace.setAudioFile}
            manualTranscript={workspace.manualTranscript}
            setManualTranscript={workspace.setManualTranscript}
            status={workspace.audioStatus}
            activeRun={workspace.activeRun}
            readOnly={workspace.session?.status === "archived"}
            onUpload={workspace.handleUpload}
            onSaveTranscript={workspace.saveTranscript}
          />
          <Composer
            mode={workspace.mode}
            setMode={workspace.setMode}
            coachInput={workspace.coachInput}
            setCoachInput={workspace.setCoachInput}
            question={workspace.question}
            setQuestion={workspace.setQuestion}
            answer={workspace.answer}
            setAnswer={workspace.setAnswer}
            followupId={workspace.followupId}
            activeRun={workspace.activeRun}
            readOnly={workspace.session?.status === "archived"}
            onSubmit={workspace.submit}
            onCancel={workspace.cancelRun}
          />
        </section>
        <aside className="flex w-[360px] shrink-0 flex-col border-l border-slate-200 bg-white" aria-label="会话详情">
          <DetailTabs active={workspace.detailTab} onChange={workspace.setDetailTab} />
          <div className="min-h-0 flex-1 overflow-y-auto p-4">

            {detailContent}
          </div>
        </aside>
      </div>
    </main>
    {workspace.approval && <ApprovalDialog approval={workspace.approval} onDecision={workspace.decideApproval} />}
    {workspace.confirmAction && <ConfirmDialog action={workspace.confirmAction} onClose={() => workspace.setConfirmAction(null)} onConfirm={workspace.confirmAction === "delete" ? workspace.deleteSession : workspace.resetProfile} />}
  </div>;
}

function DesktopOnlyNotice() {
  return <div className="flex min-h-screen items-center justify-center bg-slate-100 p-8">
    <div className="max-w-md border border-slate-200 bg-white p-6 text-center">
      <h1 className="text-lg font-semibold">请使用桌面浏览器</h1>
      <p className="mt-2 text-sm leading-6 text-slate-600">OfferPilot 当前面向桌面端使用，请使用宽度不小于 1280px 的浏览器。</p>
    </div>
  </div>;
}

export default function SessionWorkspace({ sessionId }: Props) {
  return <>
    <div className="hidden min-[1280px]:block">
      <DesktopWorkspace sessionId={sessionId} />
    </div>
    <div className="block min-[1280px]:hidden">
      <DesktopOnlyNotice />
    </div>
  </>;
}
