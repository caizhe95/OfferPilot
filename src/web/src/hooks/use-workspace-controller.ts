import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { bootstrapProfile } from "../lib/profile";
import { createReportExport, createRun, requestJson, uploadAudio } from "../lib/api";
import { consumeSseWithRetry } from "../lib/sse";
import { initialRunViewState, reduceRunState } from "../lib/run-state";
import type { Approval, Followup, Message, Report, Run, RunCall, RunEvent } from "../lib/types";
import { isTerminalRun } from "../lib/types";
import { useSessionData } from "./use-session-data";
import { useSessions } from "./use-sessions";

type Props = { sessionId: string | null };
export type DetailTab = "summary" | "followups" | "reports" | "runs";

function safeError(error: unknown): string {
  if (error && typeof error === "object" && "message" in error) return String((error as Error).message);
  return "请求失败，请稍后重试。";
}

function prependRun(current: Run[], run: Run): Run[] {
  return [run, ...current.filter((item) => item.id !== run.id)];
}

function mergeRun(current: Run[], run: Run): Run[] {
  return current.some((item) => item.id === run.id)
    ? current.map((item) => item.id === run.id ? { ...item, ...run } : item)
    : prependRun(current, run);
}

function eventRunStatus(event: RunEvent): Run["status"] | null {
  if (event.type === "run_started" || event.type === "run_resumed") return "running";
  if (event.type === "approval_required") return "waiting_approval";
  if (event.type === "run_complete") {
    const status = event.data.status;
    if (status === "completed" || status === "waiting_approval" || status === "failed" || status === "cancelled") return status;
  }
  if (event.type === "run_completed") return "completed";
  if (event.type === "run_failed") return "failed";
  if (event.type === "run_cancelled") return "cancelled";
  if (event.type === "run_interrupted") return "interrupted";
  return null;
}

function isTerminalEvent(event: RunEvent): boolean {
  if (event.type === "run_complete") return ["completed", "failed", "cancelled"].includes(String(event.data.status));
  return ["run_completed", "run_failed", "run_cancelled", "run_interrupted"].includes(event.type);
}

export function useWorkspaceController({ sessionId }: Props) {
  const router = useRouter();
  const sessions = useSessions();
  const data = useSessionData();
  const [runView, dispatchRun] = useReducer(reduceRunState, initialRunViewState);
  const [mode, setMode] = useState<"coach" | "diagnosis">("coach");
  const [coachInput, setCoachInput] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [followupId, setFollowupId] = useState<string | null>(null);
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [manualTranscript, setManualTranscript] = useState("");
  const [audioStatus, setAudioStatus] = useState("");
  const [titleDraft, setTitleDraft] = useState("");
  const [editingTitle, setEditingTitle] = useState(false);
  const [detailTab, setDetailTab] = useState<DetailTab>("summary");
  const [selectedReport, setSelectedReport] = useState<Report | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [selectedRunEvents, setSelectedRunEvents] = useState<RunEvent[]>([]);
  const [selectedRunCalls, setSelectedRunCalls] = useState<RunCall[]>([]);
  const [confirmAction, setConfirmAction] = useState<"delete" | "reset" | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reconnecting, setReconnecting] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const streamAbortRef = useRef<AbortController | null>(null);
  const watchedRunRef = useRef<string | null>(null);
  const audioRunIdRef = useRef<string | null>(null);
  const audioTranscriptRunRef = useRef<string | null>(null);
  const lastSequenceRef = useRef(0);
  const pendingDraftRef = useRef<{ sessionId: string; value: string } | null>(null);

  const readSessionDraft = useCallback((id: string): string => {
    if (pendingDraftRef.current?.sessionId === id) return pendingDraftRef.current.value;
    if (typeof window === "undefined") return "";
    const currentKey = `offerpilot-draft-${id}`;
    const legacyKey = `offerpilot-v2-draft-${id}`;
    const value = (sessionStorage.getItem(currentKey) || sessionStorage.getItem(legacyKey) || "").trim();
    if (value) {
      pendingDraftRef.current = { sessionId: id, value };
      sessionStorage.removeItem(currentKey);
      sessionStorage.removeItem(legacyKey);
    }
    return value;
  }, []);

  const resetWorkspace = useCallback(() => {
    streamAbortRef.current?.abort();
    streamAbortRef.current = null;
    watchedRunRef.current = null;
    audioRunIdRef.current = null;
    audioTranscriptRunRef.current = null;
    lastSequenceRef.current = 0;
    pendingDraftRef.current = null;
    dispatchRun({ type: "reset" });
    data.reset();
    setMode("coach");
    setCoachInput("");
    setQuestion("");
    setAnswer("");
    setFollowupId(null);
    setTitleDraft("");
    setEditingTitle(false);
    setAudioFile(null);
    setManualTranscript("");
    setAudioStatus("");
    setSelectedReport(null);
    setSelectedRunId(null);
    setSelectedRunEvents([]);
    setSelectedRunCalls([]);
    setDetailTab("summary");
    setConfirmAction(null);
    setError("");
    setNotice("");
    setReconnecting(false);
  }, [data]);

  const activeRun = useMemo(() => {
    if (runView.run && !isTerminalRun(runView.run)) return runView.run;
    return data.runs.find((run) => !isTerminalRun(run)) || null;
  }, [data.runs, runView.run]);
  const readOnly = data.session?.status === "archived";
  const approval = runView.approval || activeRun?.pending_approval || null;

  const loadSession = useCallback(async (id: string, withLoading = true) => {
    const loaded = await data.loadSession(id, withLoading);
    if (!loaded) return null;
    data.setSession(loaded.session);
    setTitleDraft(loaded.session.title || "");
    const snapshotRun = loaded.coachState?.run && !isTerminalRun(loaded.coachState.run)
      ? loaded.coachState.run
      : null;
    const active = snapshotRun || loaded.runs.find((run) => !isTerminalRun(run));
    const audioRun = loaded.runs.find((run) => run.type === "audio_transcription" && !isTerminalRun(run));
    audioRunIdRef.current = audioRun?.id || null;
    const completedAudioRun = loaded.runs.find((run) => run.type === "audio_transcription" && run.status === "completed");
    const transcript = completedAudioRun?.result?.transcript;
    if (completedAudioRun && typeof transcript === "string" && transcript.trim() && !manualTranscript.trim() && audioTranscriptRunRef.current !== completedAudioRun.id) {
      audioTranscriptRunRef.current = completedAudioRun.id;
      setManualTranscript(transcript.trim());
      setAudioStatus("音频转写完成，已填入手动转写框，请确认后填入诊断。");
    }
    if (active && watchedRunRef.current !== active.id) {
      watchedRunRef.current = active.id;
      lastSequenceRef.current = 0;
      const restoredRun = loaded.coachState?.approval && !active.pending_approval
        ? { ...active, pending_approval: loaded.coachState.approval }
        : active;
      dispatchRun({ type: "set_run", run: restoredRun });
      for (const event of loaded.coachState?.trace || []) {
        lastSequenceRef.current = Math.max(lastSequenceRef.current, event.sequence);
        dispatchRun({ type: "event", event });
      }
    }
    const draft = readSessionDraft(id);
    if (draft) setCoachInput(draft);
    if (loaded.reports.length) {
      const latestReport = loaded.reports[0];
      setDetailTab("reports");
      setSelectedReport(latestReport);
      if (!latestReport.report_markdown) {
        const detail = await requestJson<Report>(`/sessions/${id}/reports/${latestReport.id}`);
        data.setReports((current) => current.map((item) => item.id === detail.id ? { ...item, ...detail } : item));
        setSelectedReport(detail);
      }
    } else {
      setDetailTab("summary");
    }
    return loaded;
  }, [data, manualTranscript, readSessionDraft]);

  const applyEvent = useCallback((event: RunEvent) => {
    if (event.session_id !== sessionId) return;
    lastSequenceRef.current = Math.max(lastSequenceRef.current, event.sequence);
    dispatchRun({ type: "event", event });
    if (event.run_id === audioRunIdRef.current) {
      if (event.type === "approval_required") setAudioStatus("已上传，正在等待转写授权。");
      else if (event.type === "run_started" || event.type === "run_resumed") setAudioStatus("正在转写音频。");
      else if (event.type === "run_complete" || event.type === "run_completed") {
        setAudioStatus("音频转写完成，可点击“填入诊断”。");
        setAudioFile(null);
      } else if (event.type === "run_failed") setAudioStatus("音频转写失败，可重新上传或手动填入。");
      else if (event.type === "run_cancelled") setAudioStatus("音频转写已取消，可重新上传。");
      else if (event.type === "run_interrupted") setAudioStatus("音频转写被中断，可重新上传。");
    }
    if (event.type === "text_delta" || event.type === "final_response") {
      const content = typeof event.data.content === "string" ? event.data.content : "";
      if (content) {
        data.setMessages((current) => {
          const last = current[current.length - 1];
          if (last?.role === "assistant" && last.kind === "stream") {
            return [...current.slice(0, -1), { ...last, content: event.type === "text_delta" ? `${last.content}${content}` : content }];
          }
          return [...current, { role: "assistant", kind: event.type === "text_delta" ? "stream" : "text", content }];
        });
      }
    }
    const status = eventRunStatus(event);
    if (status) data.setRuns((current) => current.map((run) => run.id === event.run_id ? { ...run, status } : run));
    if (event.type === "report_ready") setNotice("正式诊断报告已生成。");
    if (isTerminalEvent(event) && sessionId) {
      void loadSession(sessionId, false).catch((loadError) => setError(safeError(loadError)));
      void sessions.loadSessions().catch(() => undefined);
    }
  }, [data, loadSession, sessionId, sessions]);

  const watchRun = useCallback(async (run: Run, after = 0) => {
    streamAbortRef.current?.abort();
    const controller = new AbortController();
    streamAbortRef.current = controller;
    let nextAfter = after;
    while (!controller.signal.aborted) {
      setReconnecting(false);
      try {
        const result = await consumeSseWithRetry(run.id, nextAfter, applyEvent, controller.signal, 3);
        nextAfter = Math.max(nextAfter, result.lastSequence);
        if (result.terminalSeen) return;
      } catch (streamError) {
        if (controller.signal.aborted) return;
        setReconnecting(true);
        try {
          const [eventsData, latestRun] = await Promise.all([
            requestJson<{ events?: RunEvent[] }>(`/runs/${run.id}/events?after=${lastSequenceRef.current}`),
            requestJson<Run>(`/runs/${run.id}`),
          ]);
          const events = eventsData.events || [];
          events.forEach(applyEvent);
          nextAfter = Math.max(nextAfter, lastSequenceRef.current, latestRun.last_event_sequence || 0);
          if (!isTerminalRun(latestRun)) {
            dispatchRun({ type: "refresh_run", run: latestRun });
            data.setRuns((current) => mergeRun(current, latestRun));
            setError("运行仍在后台，事件连接正在恢复。");
          } else {
            dispatchRun({ type: "refresh_run", run: latestRun });
            data.setRuns((current) => mergeRun(current, latestRun));
            setReconnecting(false);
            return;
          }
        } catch (syncError) {
          setError(`运行事件连接中断，正在重试：${safeError(syncError || streamError)}`);
        }
        try {
          await new Promise<void>((resolve, reject) => {
            const timer = globalThis.setTimeout(resolve, 1500);
            controller.signal.addEventListener("abort", () => { globalThis.clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); }, { once: true });
          });
        } catch {
          return;
        }
      }
    }
  }, [applyEvent, data]);

  useEffect(() => {
    void bootstrapProfile().then(() => sessions.loadSessions("active")).catch((loadError) => setError(safeError(loadError)));
    // Bootstrap once for the mounted workspace.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    resetWorkspace();
    if (!sessionId) {
      data.setLoading(false);
      return () => { streamAbortRef.current?.abort(); };
    }
    void loadSession(sessionId).then((loaded) => {
      if (loaded) {
        const active = loaded.coachState?.run && !isTerminalRun(loaded.coachState.run)
          ? loaded.coachState.run
          : loaded.runs.find((run) => !isTerminalRun(run));
        if (active) void watchRun(active, Math.max(active.last_event_sequence || 0, lastSequenceRef.current));
      }
    }).catch((loadError) => setError(safeError(loadError)));
    return () => {
      streamAbortRef.current?.abort();
    };
    // Session changes intentionally restart the loader and stream together.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [data.messages, runView.phase]);

  const startRun = useCallback(async (type: Run["type"], input: Record<string, unknown>, localMessage?: Message) => {
    setError("");
    setNotice("");
    let id = data.session?.id;
    if (!id) {
      const created = await sessions.createNewSession();
      id = created.id;
      data.setSession(created);
      router.replace(`/session/${id}`);
    }
    if (readOnly) throw new Error("归档会话为只读状态，请先恢复会话。");
    if (localMessage) data.setMessages((current) => [...current, localMessage]);
    const result = await createRun(id, type, input);
    const run = result.run;
    pendingDraftRef.current = null;
    if (type === "audio_transcription") audioRunIdRef.current = run.id;
    data.setRuns((current) => prependRun(current, run));
    watchedRunRef.current = run.id;
    lastSequenceRef.current = 0;
    dispatchRun({ type: "set_run", run });
    void watchRun(run);
    return run;
  }, [data, readOnly, router, sessions, watchRun]);

  const createSessionAndNavigate = useCallback(async () => {
    setError("");
    setNotice("");
    try {
      const created = await sessions.createNewSession();
      data.setSession(created);
      router.push(`/session/${created.id}`);
      void sessions.loadSessions("active");
    } catch (createError) {
      setError(safeError(createError));
    }
  }, [data, router, sessions]);

  const submit = useCallback(async () => {
    if (activeRun || readOnly) return;
    try {
      if (mode === "coach") {
        const message = coachInput.trim();
        if (!message) return;
        setCoachInput("");
        await startRun("coach", { message }, { role: "user", content: message, kind: "coach_input" });
      } else {
        const diagnosisQuestion = question.trim();
        const diagnosisAnswer = answer.trim();
        if (!diagnosisQuestion || !diagnosisAnswer) return;
        setQuestion("");
        setAnswer("");
        await startRun("diagnosis", { question: diagnosisQuestion, answer: diagnosisAnswer, ...(followupId ? { followup_id: followupId } : {}) }, { role: "user", content: `[正式诊断]\n问题：${diagnosisQuestion}\n回答：${diagnosisAnswer}`, kind: "diagnosis_input" });
        setFollowupId(null);
      }
    } catch (submitError) {
      setError(safeError(submitError));
    }
  }, [activeRun, answer, coachInput, followupId, mode, question, readOnly, startRun]);

  const cancelRun = useCallback(async () => {
    if (!activeRun) return;
    try {
      const result = await requestJson<{ run?: Run }>(`/runs/${activeRun.id}/cancel`, { method: "POST", body: "{}" });
      setNotice("已请求取消，服务端正在收尾。");
      const cancelledRun = result.run ? { ...activeRun, ...result.run } : activeRun;
      if (result.run) {
        data.setRuns((current) => mergeRun(current, cancelledRun));
        dispatchRun({ type: "refresh_run", run: cancelledRun });
      }
      const after = lastSequenceRef.current;
      streamAbortRef.current?.abort();
      void watchRun(cancelledRun, after);
    } catch (cancelError) {
      setError(safeError(cancelError));
    }
  }, [activeRun, data, watchRun]);

  const rerun = useCallback(async (run: Run) => {
    if (!data.session || !run.input || !["failed", "cancelled", "interrupted"].includes(run.status)) return;
    try {
      const result = run.type === "report_export" && typeof run.input.report_id === "string"
        ? await createReportExport(data.session.id, run.input.report_id)
        : await createRun(data.session.id, run.type, run.input);
      data.setRuns((current) => prependRun(current, result.run));
      watchedRunRef.current = result.run.id;
      lastSequenceRef.current = 0;
      dispatchRun({ type: "set_run", run: result.run });
      void watchRun(result.run);
    } catch (rerunError) {
      setError(safeError(rerunError));
    }
  }, [data, watchRun]);

  const decideApproval = useCallback(async (decision: "approve" | "deny") => {
    if (!approval) return;
    try {
      const result = await requestJson<{ approval?: Approval; run?: Run }>(`/approvals/${approval.id}/decision`, { method: "POST", body: JSON.stringify({ decision }) });
      if (result.approval?.decision_reused) setNotice("该审批已经处理，当前状态已同步。");
      else if (decision === "deny") setNotice("已拒绝该受控操作，运行将结束。");
      else setNotice("已授权，服务端会自动恢复运行。");
      if (result.run) {
        data.setRuns((current) => mergeRun(current, result.run as Run));
        dispatchRun({ type: "refresh_run", run: result.run as Run });
      }
    } catch (decisionError) {
      setError(safeError(decisionError));
    }
  }, [approval, data]);

  const saveTitle = useCallback(async () => {
    if (!data.session) return;
    try {
      const updated = await sessions.updateSessionTitle(data.session.id, titleDraft);
      data.setSession(updated);
      setEditingTitle(false);
      setNotice("会话标题已更新。");
      void sessions.loadSessions();
    } catch (titleError) {
      setError(safeError(titleError));
    }
  }, [data, sessions, titleDraft]);

  const archiveSession = useCallback(async (archived: boolean) => {
    if (!data.session) return;
    try {
      const updated = await sessions.archiveSession(data.session.id, archived);
      data.setSession(updated);
      setNotice(archived ? "会话已归档。" : "会话已恢复。");
      void sessions.loadSessions(sessions.sessionFilter);
    } catch (archiveError) {
      setError(safeError(archiveError));
    }
  }, [data, sessions]);

  const deleteSession = useCallback(async () => {
    if (!data.session) return;
    try {
      await sessions.deleteSession(data.session.id);
      setConfirmAction(null);
      resetWorkspace();
      router.push("/");
      void sessions.loadSessions();
    } catch (deleteError) {
      setError(safeError(deleteError));
      setConfirmAction(null);
    }
  }, [data.session, resetWorkspace, router, sessions]);

  const resetProfile = useCallback(async () => {
    try {
      await requestJson("/profile/reset", { method: "POST", body: JSON.stringify({ confirmation: "RESET" }) });
      setConfirmAction(null);
      router.push("/");
      window.location.reload();
    } catch (resetError) {
      setError(safeError(resetError));
      setConfirmAction(null);
    }
  }, [router]);

  const saveTranscript = useCallback(async () => {
    const transcript = manualTranscript.trim();
    if (!transcript) return;
    if (data.session) {
      try {
        await requestJson(`/sessions/${data.session.id}/transcript`, { method: "POST", body: JSON.stringify({ transcript }) });
      } catch (transcriptError) {
        setError(safeError(transcriptError));
        return;
      }
    }
    setAnswer(transcript);
    setMode("diagnosis");
    setManualTranscript("");
    setNotice("转写文本已填入正式诊断输入框。");
    if (sessionId) void loadSession(sessionId, false);
  }, [data.session, loadSession, manualTranscript, sessionId]);

  const handleUpload = useCallback(async () => {
    if (!data.session || !audioFile || activeRun || readOnly) return;
    try {
      setAudioStatus("上传中...");
      const result = await uploadAudio(data.session.id, audioFile);
      setAudioStatus("已上传，正在等待转写授权。");
      await startRun("audio_transcription", { upload_id: result.upload.id });
    } catch (uploadError) {
      setAudioStatus("");
      setError(safeError(uploadError));
    }
  }, [activeRun, audioFile, data.session, readOnly, startRun]);

  const exportReport = useCallback(async (report: Report) => {
    if (!data.session || activeRun || readOnly) return;
    try {
      setSelectedReport(report);
      const result = await createReportExport(data.session.id, report.id);
      const run = result.run;
      data.setRuns((current) => prependRun(current, run));
      watchedRunRef.current = run.id;
      lastSequenceRef.current = 0;
      dispatchRun({ type: "set_run", run });
      void watchRun(run);
    } catch (exportError) {
      setError(safeError(exportError));
    }
  }, [activeRun, data, readOnly, watchRun]);

  const downloadSelectedReport = useCallback(() => {
    const exported = data.runs.find((run) => run.type === "report_export" && run.status === "completed" && run.result?.report && (run.result.report as Record<string, unknown>).id === selectedReport?.id);
    const report = exported?.result?.report;
    const markdown = report && typeof report === "object" && typeof (report as Record<string, unknown>).report_markdown === "string" ? String((report as Record<string, unknown>).report_markdown) : "";
    if (!markdown || !selectedReport) return;
    const href = URL.createObjectURL(new Blob([markdown], { type: "text/markdown;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = href;
    link.download = `diagnosis-${selectedReport.id}.md`;
    link.click();
    URL.revokeObjectURL(href);
  }, [data.runs, selectedReport]);

  const selectFollowup = useCallback((item: Followup) => {
    if (activeRun || readOnly) return;
    setMode("diagnosis");
    setQuestion(item.question);
    setFollowupId(item.id);
  }, [activeRun, readOnly]);

  const selectReport = useCallback(async (report: Report) => {
    setSelectedReport(report);
    setDetailTab("reports");
    if (report.report_markdown || !data.session) return;
    try {
      const detail = await requestJson<Report>(`/sessions/${data.session.id}/reports/${report.id}`);
      data.setReports((current) => current.map((item) => item.id === detail.id ? { ...item, ...detail } : item));
      setSelectedReport(detail);
    } catch (reportError) {
      setError(safeError(reportError));
    }
  }, [data]);

  const selectRun = useCallback(async (run: Run) => {
    if (selectedRunId === run.id) {
      setSelectedRunId(null);
      setSelectedRunEvents([]);
      setSelectedRunCalls([]);
      return;
    }
    setSelectedRunId(run.id);
    setSelectedRunEvents([]);
    setSelectedRunCalls([]);
    try {
      const [detail, eventResult, callResult] = await Promise.all([
        requestJson<Run>(`/runs/${run.id}`),
        requestJson<{ events?: RunEvent[] }>(`/runs/${run.id}/events`),
        requestJson<{ calls?: RunCall[] }>(`/runs/${run.id}/calls`),
      ]);
      data.setRuns((current) => current.map((item) => item.id === run.id ? { ...item, ...detail } : item));
      setSelectedRunEvents(eventResult.events || []);
      setSelectedRunCalls(callResult.calls || []);
    } catch (runError) {
      setError(safeError(runError));
    }
  }, [data, selectedRunId]);

  return {
    ...sessions,
    ...data,
    runView,
    activeRun,
    approval,
    mode,
    setMode,
    coachInput,
    setCoachInput,
    question,
    setQuestion,
    answer,
    setAnswer,
    followupId,
    audioFile,
    setAudioFile,
    manualTranscript,
    setManualTranscript,
    audioStatus,
    titleDraft,
    setTitleDraft,
    editingTitle,
    setEditingTitle,
    detailTab,
    setDetailTab,
    selectedReport,
    selectedRunId,
    selectedRunEvents,
    selectedRunCalls,
    confirmAction,
    setConfirmAction,
    error,
    notice,
    loading: data.loading,
    sidebarLoading: sessions.loading,
    reconnecting,
    bottomRef,
    submit,
    createSessionAndNavigate,
    cancelRun,
    rerun,
    decideApproval,
    saveTitle,
    archiveSession,
    deleteSession,
    resetProfile,
    saveTranscript,
    handleUpload,
    exportReport,
    downloadSelectedReport,
    selectFollowup,
    selectReport,
    selectRun,
    changeSessionFilter: sessions.changeFilter,
  };
}
