"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { useParams } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { PROGRESS_STAGES, STAGE_LABELS, getSessionStatusStyle } from "../../ui";
import { bootstrapProfile, withProfileHeaders } from "../../profile";
import {
  consumeSseResponse,
  type CoachSseEvent,
  type ParsedSseEvent,
  type RunCompleteStatus,
} from "../../sse";

const API = "/api";

type Message = {
  role: "user" | "assistant" | "system";
  content: string;
  events?: CoachSseEvent[];
};

type ApprovalFlow = "coach" | "audio" | "export";
type ApprovalStatus = "pending" | "approved" | "executing";

type ProgressStep = {
  stage: string;
  done: boolean;
};

type PendingApproval = {
  request_id: string;
  tool_name: string;
  risk_level: string;
  params: Record<string, unknown>;
  message?: string;
  flowKind: ApprovalFlow;
  status: ApprovalStatus;
};

type CoachStateApproval = {
  request_id: string;
  tool_name: string;
  risk_level: string;
  flow_kind: ApprovalFlow;
  status: ApprovalStatus;
  params: Record<string, unknown>;
  trace_id: string;
  created_at?: string;
  expires_at?: string;
};

type CoachState = {
  session?: Record<string, unknown>;
  run?: { trace_id?: string; status?: string } | null;
  trace?: Record<string, unknown> | null;
  approvals?: CoachStateApproval[];
};

type AudioStatus =
  | "idle"
  | "selected"
  | "uploading"
  | "approval_required"
  | "transcribed"
  | "failed"
  | "manual";

function emptyProgress(): ProgressStep[] {
  return PROGRESS_STAGES.map((stage) => ({ stage, done: false }));
}

function applyProgressStages(stages: string[], status?: string): ProgressStep[] {
  const completed = new Set(stages);
  if (status === "completed") {
    PROGRESS_STAGES.forEach((stage) => completed.add(stage));
  }
  return PROGRESS_STAGES.map((stage) => ({ stage, done: completed.has(stage) }));
}

function isReportLike(content: string) {
  return content.includes("内容维度") || content.includes("语音维度") || content.includes("|") || content.includes("##");
}

function summarize(value: unknown, max = 240) {
  if (value == null) return "";
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

function isApprovalFlow(value: unknown): value is ApprovalFlow {
  return value === "coach" || value === "audio" || value === "export";
}

function isApprovalStatus(value: unknown): value is ApprovalStatus {
  return value === "pending" || value === "approved" || value === "executing";
}

function asPublicParams(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function approvalFromState(approval: any): PendingApproval | null {
  if (
    !approval ||
    typeof approval.request_id !== "string" ||
    typeof approval.tool_name !== "string" ||
    typeof approval.risk_level !== "string" ||
    !isApprovalFlow(approval.flow_kind) ||
    !isApprovalStatus(approval.status)
  ) {
    return null;
  }
  return {
    request_id: approval.request_id,
    tool_name: approval.tool_name,
    risk_level: approval.risk_level,
    params: asPublicParams(approval.params),
    flowKind: approval.flow_kind,
    status: approval.status,
  };
}

function approvalSummary(approval: PendingApproval): string {
  if (approval.flowKind === "audio") {
    const filename = typeof approval.params.filename === "string" ? approval.params.filename : "受控音频文件";
    const size = typeof approval.params.size === "number" ? `${(approval.params.size / 1024).toFixed(1)} KB` : "大小未知";
    return `文件：${filename}；大小：${size}；风险：${approval.risk_level}`;
  }
  if (approval.flowKind === "export") {
    const reportId = typeof approval.params.report_id === "string" ? approval.params.report_id : "报告编号不可用";
    return `报告编号：${reportId}；风险：${approval.risk_level}`;
  }
  return `风险：${approval.risk_level}。内部执行参数不会展示。`;
}

function eventToolName(event: CoachSseEvent): string | undefined {
  return event.type === "tool_call" || event.type === "tool_result" || event.type === "permission_required"
    ? event.tool_name
    : undefined;
}

function eventMessage(event: CoachSseEvent): string | undefined {
  return event.type === "permission_required" || event.type === "error" ? event.message : undefined;
}

function toolResultError(event: CoachSseEvent): string | undefined {
  if (event.type !== "tool_result" || typeof event.result !== "object" || event.result === null) return undefined;
  const result = event.result as Record<string, unknown>;
  return result.error ? String(result.message || "工具失败") : undefined;
}

function apiErrorMessage(payload: any, fallback: string) {
  const code = payload?.error?.code;
  if (code === "llm_unavailable") return "模型服务未配置或暂不可用。";
  if (code === "output_check_failed") return "模型输出未通过结构检查，请稍后重试。";
  return payload?.error?.message || payload?.detail || fallback;
}

function extractTraceId(session: any, checkpoint: any, events: any[]) {
  const fromSession = session?.metadata?.trace_id || session?.trace_id;
  const fromCheckpoint =
    checkpoint?.trace_id ||
    checkpoint?.state?.trace_id ||
    checkpoint?.messages?.find?.((m: any) => m?.trace_id)?.trace_id;
  const fromProgress = events
    .map((event) => event?.metadata?.trace_id)
    .find(Boolean);
  return fromSession || fromCheckpoint || fromProgress || null;
}

export default function SessionPage() {
  const params = useParams();
  const sessionId = params.id as string;
  const [messages, setMessages] = useState<Message[]>([]);
  const [mode, setMode] = useState<"coach" | "diagnosis">("coach");
  const [input, setInput] = useState("");
  const [diagnosisQuestion, setDiagnosisQuestion] = useState("");
  const [diagnosisAnswer, setDiagnosisAnswer] = useState("");
  const [loading, setLoading] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  const [session, setSession] = useState<any>(null);
  const [traceId, setTraceId] = useState<string | null>(null);
  const [trace, setTrace] = useState<any>(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const [traceError, setTraceError] = useState("");
  const [progress, setProgress] = useState<ProgressStep[]>(emptyProgress());
  const [pendingApproval, setPendingApproval] = useState<PendingApproval | null>(null);
  const [resuming, setResuming] = useState(false);
  const [reportMarkdown, setReportMarkdown] = useState("");
  const [reportId, setReportId] = useState<string | null>(null);
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [audioStatus, setAudioStatus] = useState<AudioStatus>("idle");
  const [audioError, setAudioError] = useState("");
  const [audioInfo, setAudioInfo] = useState("");
  const [manualTranscript, setManualTranscript] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const activeTraceRef = useRef<string | null>(null);
  const submitRef = useRef<((draftMessage?: string) => Promise<void>) | null>(null);
  const draftLoadedRef = useRef(false);
  const draftRef = useRef<string | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const updateProgress = useCallback((stage: string) => {
    setProgress((prev) => {
      const idx = PROGRESS_STAGES.indexOf(stage);
      if (idx < 0) return prev;
      return prev.map((p, i) => (i <= idx ? { ...p, done: true } : p));
    });
  }, []);

  const applyTranscript = useCallback((transcript: string) => {
    if (mode === "diagnosis") setDiagnosisAnswer(transcript);
    else setInput(transcript);
  }, [mode]);

  const refreshSessionState = useCallback(async () => {
    const [sessionResult, messagesResult, progressResult, checkpointResult, stateResult] =
      await Promise.allSettled([
        fetch(`${API}/sessions/${sessionId}`, { headers: withProfileHeaders() }).then((r) => {
          if (!r.ok) throw new Error(`Session ${r.status}`);
          return r.json();
        }),
        fetch(`${API}/sessions/${sessionId}/messages?n=100`, { headers: withProfileHeaders() }).then((r) => {
          if (!r.ok) throw new Error(`Messages ${r.status}`);
          return r.json();
        }),
        fetch(`${API}/sessions/${sessionId}/progress`, { headers: withProfileHeaders() }).then((r) => {
          if (!r.ok) throw new Error(`Progress ${r.status}`);
          return r.json();
        }),
        fetch(`${API}/sessions/${sessionId}/checkpoints/latest`, { headers: withProfileHeaders() }).then((r) => {
          if (r.status === 404) return null;
          if (!r.ok) throw new Error(`Checkpoint ${r.status}`);
          return r.json();
        }),
        fetch(`${API}/coach/state?session_id=${encodeURIComponent(sessionId)}`, { headers: withProfileHeaders() }).then((r) => {
          if (!r.ok) throw new Error(`Coach state ${r.status}`);
          return r.json() as Promise<CoachState>;
        }),
      ]);

    const durableState = stateResult.status === "fulfilled" ? stateResult.value : null;
    const nextSession =
      durableState?.session || (sessionResult.status === "fulfilled" ? sessionResult.value : null);
    const backendMessages =
      messagesResult.status === "fulfilled" ? messagesResult.value?.messages || [] : [];
    const progressEvents =
      progressResult.status === "fulfilled" ? progressResult.value?.events || [] : [];
    const checkpoint =
      checkpointResult.status === "fulfilled" ? checkpointResult.value : null;

    if (nextSession) setSession(nextSession);

    if (backendMessages.length > 0) {
      const restored = backendMessages.map((msg: any) => ({
        role: msg.role,
        content: msg.content,
      }));
      setMessages(restored);
      const lastAssistant = [...restored].reverse().find((msg) => msg.role === "assistant");
      if (lastAssistant?.content && isReportLike(lastAssistant.content)) {
        setReportMarkdown(lastAssistant.content);
      }
    }

    const eventStages = progressEvents.map((event: any) => event.stage).filter(Boolean);
    const checkpointStages = Array.isArray(checkpoint?.progress) ? checkpoint.progress : [];
    setProgress(applyProgressStages([...checkpointStages, ...eventStages], String(nextSession?.status || "")));

    const durableTraceId = durableState?.trace?.id || durableState?.run?.trace_id;
    const nextTraceId = durableTraceId || extractTraceId(nextSession, checkpoint, progressEvents);
    if (typeof nextTraceId === "string" && nextTraceId) {
      activeTraceRef.current = nextTraceId;
      setTraceId(nextTraceId);
    }
    if (durableState?.trace) setTrace(durableState.trace);

    const restoredApproval = durableState?.approvals
      ?.map(approvalFromState)
      .find((approval): approval is PendingApproval => approval !== null);
    setPendingApproval(restoredApproval || null);
    if (restoredApproval?.flowKind === "audio") setAudioStatus("approval_required");
    if (!restoredApproval && durableState?.run?.status === "running") {
      setLoading(true);
      setActionMessage("已恢复活跃运行，正在等待服务端终态。");
    }

    const failures = [sessionResult, messagesResult, progressResult, checkpointResult, stateResult].filter(
      (result) => result.status === "rejected"
    );
    setLoadError(failures.length ? "部分会话状态加载失败，可稍后重试。" : "");
  }, [sessionId]);

  useEffect(() => {
    if (!draftLoadedRef.current) {
      draftLoadedRef.current = true;
      const key = `offerpilot-draft-${sessionId}`;
      draftRef.current = sessionStorage.getItem(key)?.trim() || null;
      sessionStorage.removeItem(key);
    }
    let active = true;
    setInitialLoading(true);
    bootstrapProfile()
      .then(refreshSessionState)
      .then(async () => {
        const draft = draftRef.current;
        if (!active || !draft) return;
        draftRef.current = null;
        await submitRef.current?.(draft);
      })
      .catch((err) => setLoadError(`会话加载失败: ${err.message}`))
      .finally(() => {
        if (active) setInitialLoading(false);
      });
    return () => {
      active = false;
    };
  }, [refreshSessionState, sessionId]);

  const refreshTrace = async () => {
    if (!traceId) return;
    setTraceLoading(true);
    setTraceError("");
    try {
      const res = await fetch(`${API}/traces/${traceId}`, { headers: withProfileHeaders() });
      if (!res.ok) throw new Error(`Trace ${res.status}`);
      setTrace(await res.json());
    } catch (err: any) {
      setTraceError(`Trace 加载失败: ${err.message}`);
    } finally {
      setTraceLoading(false);
    }
  };

  const consumeCoachResumeStream = async (response: Response) => {
    const events: CoachSseEvent[] = [];
    const seenSequences = new Set<string>();
    let assistantText = "";
    let renderedAssistant = false;

    const applyEvent = (event: ParsedSseEvent) => {
      if (event.type === "unknown") {
        setActionMessage("已忽略无法识别的实时事件。");
        return;
      }
      const sequenceKey = `${event.trace_id}:${event.sequence}`;
      if (seenSequences.has(sequenceKey)) return;
      seenSequences.add(sequenceKey);
      events.push(event);
      activeTraceRef.current = event.trace_id;
      setTraceId(event.trace_id);
      if (event.type === "text_delta") {
        assistantText += event.content;
        setMessages((prev) => {
          if (!renderedAssistant) {
            renderedAssistant = true;
            return [...prev, { role: "assistant", content: assistantText, events }];
          }
          const updated = [...prev];
          const lastIndex = updated.length - 1;
          updated[lastIndex] = { ...updated[lastIndex], content: assistantText, events };
          return updated;
        });
      } else if (event.type === "tool_result" && event.tool_name === "save_memory") {
        updateProgress("memory_updated");
      } else if (event.type === "permission_required") {
        setPendingApproval({
          request_id: event.request_id,
          tool_name: event.tool_name,
          risk_level: event.risk_level,
          params: event.params || {},
          message: event.message,
          flowKind: "coach",
          status: "pending",
        });
      } else if (event.type === "final_response" && !renderedAssistant) {
        renderedAssistant = true;
        setMessages((prev) => [...prev, { role: "assistant", content: event.content, events }]);
      } else if (event.type === "error") {
        setActionMessage(apiErrorMessage(event, event.message || "执行过程中出现错误。"));
      }
    };

    await consumeSseResponse(response, applyEvent);
    return events;
  };

  const handleSubmit = async (draftMessage?: string) => {
    const isDiagnosis = !draftMessage && mode === "diagnosis";
    const question = diagnosisQuestion.trim();
    const answer = diagnosisAnswer.trim();
    const coachMessage = draftMessage?.trim() || input.trim();
    if (loading || (isDiagnosis ? !question || !answer : !coachMessage)) return;

    const userContent = isDiagnosis
      ? `面试题：${question}\n\n我的回答：${answer}`
      : coachMessage;
    if (isDiagnosis) {
      setDiagnosisQuestion("");
      setDiagnosisAnswer("");
    } else {
      setInput("");
    }
    setLoading(true);
    setActionMessage("");
    setReportMarkdown("");
    setReportId(null);
    setTrace(null);
    setProgress(emptyProgress());
    activeTraceRef.current = null;
    setMessages((prev) => [...prev, { role: "user", content: userContent }]);
    updateProgress("input_received");

    const abortController = new AbortController();
    abortRef.current = abortController;

    try {
      await bootstrapProfile();
      const res = await fetch(`${API}/coach`, {
        method: "POST",
        headers: withProfileHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(
          isDiagnosis
            ? {
                mode: "diagnosis",
                session_id: sessionId,
                diagnosis: { question, answer },
              }
            : { mode: "coach", message: userContent, session_id: sessionId }
        ),
        signal: abortController.signal,
      });

      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(apiErrorMessage(detail, `Chat request failed: ${res.status}`));
      }

      const events: CoachSseEvent[] = [];
      const seenSequences = new Set<string>();
      let assistantText = "";
      let waitingApproval = false;
      let terminalStatus: RunCompleteStatus | null = null;

      await consumeSseResponse(res, (event: ParsedSseEvent) => {
        if (event.type === "unknown") {
          setActionMessage("已忽略无法识别的实时事件。");
          return;
        }
        const sequenceKey = `${event.trace_id}:${event.sequence}`;
        if (seenSequences.has(sequenceKey)) return;
        seenSequences.add(sequenceKey);
        events.push(event);
        activeTraceRef.current = event.trace_id;
        setTraceId(event.trace_id);

        switch (event.type) {
          case "text_delta":
            assistantText += event.content;
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last?.role === "assistant") {
                const updated = [...prev];
                updated[updated.length - 1] = { ...last, content: assistantText, events };
                return updated;
              }
              return [...prev, { role: "assistant", content: assistantText, events }];
            });
            break;
          case "tool_call":
            updateProgress(event.tool_name === "search_knowledge" ? "knowledge_retrieved" : "diagnosis_evaluated");
            break;
          case "tool_result":
            if (event.tool_name === "save_memory") updateProgress("memory_updated");
            break;
          case "report_ready":
            setReportId(event.report_id);
            updateProgress("diagnosis_evaluated");
            updateProgress("report_generated");
            updateProgress("output_checked");
            break;
          case "final_response":
            if (isDiagnosis) setReportMarkdown(event.content);
            break;
          case "run_complete":
            terminalStatus = event.status;
            if (event.status === "waiting_approval" || event.waiting_approval) {
              waitingApproval = true;
              setActionMessage("等待用户确认后继续执行。");
            } else if (event.status === "completed") {
              updateProgress("completed");
            }
            break;
          case "error":
            setActionMessage(apiErrorMessage(event, event.message || "执行过程中出现错误。"));
            break;
          case "permission_required":
            waitingApproval = true;
            setPendingApproval({
              request_id: event.request_id,
              tool_name: event.tool_name,
              risk_level: event.risk_level,
              params: event.params || {},
              message: event.message,
              flowKind: "coach",
              status: "pending",
            });
            setActionMessage("需要确认工具调用。");
            break;
        }
      });

      const finalEvent = events.find((event) => event.type === "final_response");
      const finalOutput = finalEvent?.content || assistantText;

      if (finalOutput || events.length > 0) {
        setMessages((prev) => {
          const filtered = prev.filter(
            (msg, index) =>
              !(
                index === prev.length - 1 &&
                msg.role === "assistant" &&
                (msg.content === assistantText || msg.content === finalOutput)
              )
          );
          return [...filtered, { role: "assistant", content: finalOutput, events }];
        });
      }

      if (waitingApproval && !finalOutput) {
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: "已暂停，等待你确认权限后继续。", events },
        ]);
      }

      await refreshSessionState();
      if (!terminalStatus && !abortController.signal.aborted) {
        setActionMessage("实时连接已断开，已从服务端状态恢复；等待审批可继续执行。");
      }
    } catch (err: any) {
      if (err.name !== "AbortError") {
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: `错误: ${err.message}` },
        ]);
        try {
          await refreshSessionState();
        } catch {}
        setActionMessage("连接中断或请求失败，已从服务端状态恢复；待审批操作可继续执行。");
      }
    } finally {
      setLoading(false);
      abortRef.current = null;
    }
  };

  submitRef.current = handleSubmit;

  const handleStop = async () => {
    const activeTraceId = activeTraceRef.current || traceId;
    if (!loading || !activeTraceId) return;
    setActionMessage("");
    try {
      const response = await fetch(`${API}/coach/cancel`, {
        method: "POST",
        headers: withProfileHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ session_id: sessionId, trace_id: activeTraceId }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload.status !== "cancel_requested") {
        throw new Error(apiErrorMessage(payload, `Cancel failed: ${response.status}`));
      }
      abortRef.current?.abort();
      setActionMessage("停止请求已确认，服务端正在收尾。");
    } catch (err: any) {
      setActionMessage(`停止请求失败：${err.message}。仍在继续监听服务端终态。`);
    }
  };

  const handleApprove = async () => {
    if (!pendingApproval || resuming) return;
    if (pendingApproval.status === "executing") {
      setActionMessage("该操作已由服务端开始执行，请等待完成后刷新状态。");
      await refreshSessionState();
      return;
    }
    setResuming(true);
    setActionMessage("");
    setAudioError("");
    setAudioInfo("");

    try {
      if (pendingApproval.status === "pending") {
        const approveResp = await fetch(`${API}/permission/approve`, {
          method: "POST",
          headers: withProfileHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({
            request_id: pendingApproval.request_id,
            session_id: sessionId,
          }),
        });
        if (!approveResp.ok) {
          const detail = await approveResp.json().catch(() => ({}));
          throw new Error(apiErrorMessage(detail, `Approve failed: ${approveResp.status}`));
        }
      }

      let resumeUrl = `${API}/audio/resume`;
      let resumeBody: Record<string, string> = {
        request_id: pendingApproval.request_id,
        session_id: sessionId,
      };
      if (pendingApproval.flowKind === "coach") {
        resumeUrl = `${API}/coach/resume`;
      } else if (pendingApproval.flowKind === "export") {
        const exportReportId = pendingApproval.params.report_id;
        if (typeof exportReportId !== "string" || !exportReportId) {
          throw new Error("导出审批缺少报告编号。");
        }
        resumeUrl = `${API}/coach/reports/export/resume`;
        resumeBody = {
          ...resumeBody,
          report_id: exportReportId,
        };
      }
      const resumeResp = await fetch(resumeUrl, {
        method: "POST",
        headers: withProfileHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(resumeBody),
      });
      if (!resumeResp.ok) {
        const detail = await resumeResp.json().catch(() => ({}));
        throw new Error(apiErrorMessage(detail, `Resume failed: ${resumeResp.status}`));
      }

      if (pendingApproval.flowKind === "coach") {
        setLoading(true);
        await consumeCoachResumeStream(resumeResp);
        setActionMessage("记忆已保存，教练已继续本轮回复。");
        setPendingApproval(null);
        await refreshSessionState();
        return;
      }
      const resumeData = await resumeResp.json();
      if (pendingApproval.tool_name === "save_memory") {
        updateProgress("memory_updated");
        setActionMessage("记忆已保存，后续诊断会注入这次结论。");
      } else if (pendingApproval.tool_name === "transcribe_audio") {
        if (resumeData.status === "transcribed" && resumeData.transcript) {
          applyTranscript(resumeData.transcript);
          setAudioFile(null);
          setAudioStatus("transcribed");
          setAudioInfo(
            `转写完成：${resumeData.provider || ""}，${resumeData.transcript.length} 字。可继续诊断。`
          );
        } else {
          setAudioStatus("manual");
          setAudioError(`转写失败: ${resumeData.error || "未知错误"}。请手动粘贴 transcript。`);
        }
      } else if (pendingApproval.tool_name === "export_report") {
        const report = resumeData.report || resumeData.result || resumeData;
        const markdown = typeof report === "string" ? report : report?.report_markdown;
        if (typeof markdown === "string") {
          setReportMarkdown(markdown);
          downloadMarkdown(markdown);
        }
        setActionMessage("报告导出权限已执行。");
      } else {
        setActionMessage("工具权限已批准并执行。");
      }

      setPendingApproval(null);
      await refreshSessionState();
    } catch (err: any) {
      setActionMessage(`权限执行失败: ${err.message}`);
    } finally {
      setResuming(false);
      setLoading(false);
    }
  };

  const handleDeny = async () => {
    if (!pendingApproval || resuming || pendingApproval.status !== "pending") return;
    setResuming(true);
    setActionMessage("");
    try {
      const denyResp = await fetch(`${API}/permission/deny`, {
        method: "POST",
        headers: withProfileHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          request_id: pendingApproval.request_id,
          session_id: sessionId,
        }),
      });
      if (!denyResp.ok) {
        const detail = await denyResp.json().catch(() => ({}));
        throw new Error(apiErrorMessage(detail, `Deny failed: ${denyResp.status}`));
      }
      const denyData = await denyResp.json();
      const deniedTool = pendingApproval.tool_name;
      if (pendingApproval.flowKind === "coach" && denyData.coach_resume_required) {
        setLoading(true);
        const resumeResp = await fetch(`${API}/coach/resume`, {
          method: "POST",
          headers: withProfileHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ request_id: pendingApproval.request_id, session_id: sessionId }),
        });
        if (!resumeResp.ok) {
          const detail = await resumeResp.json().catch(() => ({}));
          throw new Error(apiErrorMessage(detail, `Resume failed: ${resumeResp.status}`));
        }
        await consumeCoachResumeStream(resumeResp);
        setActionMessage("已拒绝工具调用，教练已根据该结果继续回复。");
      } else {
        setActionMessage("已拒绝工具调用，工具不会执行。");
      }
      setPendingApproval(null);
      if (deniedTool === "transcribe_audio") {
        setAudioStatus("manual");
        setAudioError("已拒绝 ASR 调用，可以手动粘贴 transcript。");
      }
      await refreshSessionState();
    } catch (err: any) {
      setActionMessage(`拒绝失败: ${err.message}`);
    } finally {
      setResuming(false);
      setLoading(false);
    }
  };

  const handleCopyReport = () => {
    navigator.clipboard.writeText(reportMarkdown);
    setActionMessage("报告已复制。");
  };

  const downloadMarkdown = (markdown: string) => {
    const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `diagnosis-${sessionId.substring(0, 8)}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleExportReport = async () => {
    if (!reportId) {
      setActionMessage("报告编号尚未恢复，无法发起导出。");
      return;
    }
    setActionMessage("");
    try {
      const response = await fetch(`${API}/coach/reports/export`, {
        method: "POST",
        headers: withProfileHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ session_id: sessionId, report_id: reportId }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(apiErrorMessage(data, `Export failed: ${response.status}`));
      if (data.type === "permission_required") {
        setPendingApproval({
          request_id: String(data.request_id || ""),
          tool_name: "export_report",
          risk_level: String(data.risk_level || "high"),
          params: asPublicParams(data.params),
          message: typeof data.message === "string" ? data.message : undefined,
          flowKind: "export",
          status: "pending",
        });
        setActionMessage("报告导出需要确认。");
      } else {
        const markdown = typeof data.report_markdown === "string" ? data.report_markdown : reportMarkdown;
        if (!markdown) throw new Error("导出响应未包含报告内容。");
        setReportMarkdown(markdown);
        downloadMarkdown(markdown);
        setActionMessage("报告已导出。");
      }
      await refreshSessionState();
    } catch (err: any) {
      setActionMessage(`报告导出失败: ${err.message}`);
    }
  };

  const handleAudioUpload = async () => {
    if (!audioFile) return;
    setAudioStatus("uploading");
    setAudioError("");
    setAudioInfo("");

    const formData = new FormData();
    formData.append("file", audioFile);
    formData.append("session_id", sessionId);

    try {
      const res = await fetch(`${API}/audio/upload`, {
        method: "POST",
        headers: withProfileHeaders(),
        body: formData,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(apiErrorMessage(data, `Upload failed: ${res.status}`));

      if (data.status === "approval_required") {
        setPendingApproval({
          request_id: data.request_id || "",
          tool_name: "transcribe_audio",
          risk_level: data.risk_level || "medium",
          params: asPublicParams(data.params),
          message: data.message,
          flowKind: "audio",
          status: "pending",
        });
        setAudioStatus("approval_required");
        setAudioInfo("ASR 需要授权，批准后才会调用转写服务。");
      } else if (data.status === "transcribed" && data.transcript) {
        applyTranscript(data.transcript);
        setAudioFile(null);
        setAudioStatus("transcribed");
        setAudioInfo(`转写完成：${data.provider || ""}，${data.transcript.length} 字。`);
      } else if (data.status === "asr_failed") {
        setAudioStatus("manual");
        setAudioError(`转写失败: ${data.error || "未知错误"}。请手动粘贴 transcript。`);
      } else if (data.status === "saved") {
        setAudioStatus("manual");
        setAudioInfo("音频已保存，请在下方手动粘贴 transcript。");
      }
      await refreshSessionState();
    } catch (err: any) {
      setAudioStatus("failed");
      setAudioError(`上传失败: ${err.message}`);
    }
  };

  const handleManualTranscript = async () => {
    if (!manualTranscript.trim()) {
      setAudioError("Transcript 不能为空。");
      return;
    }

    const formData = new FormData();
    formData.append("session_id", sessionId);
    formData.append("transcript", manualTranscript.trim());

    try {
      const res = await fetch(`${API}/audio/transcript/manual`, {
        method: "POST",
        headers: withProfileHeaders(),
        body: formData,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(apiErrorMessage(data, `Manual transcript failed: ${res.status}`));
      applyTranscript(data.transcript || manualTranscript.trim());
      setManualTranscript("");
      setAudioFile(null);
      setAudioStatus("transcribed");
      setAudioError("");
      setAudioInfo("手动 transcript 已保存并填入输入框。");
      await refreshSessionState();
    } catch (err: any) {
      setAudioError(`保存失败: ${err.message}`);
    }
  };

  const hasProgress = progress.some((step) => step.done) || loading || session?.status === "waiting_approval";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-gray-500">
        <a href="/" className="hover:text-indigo-600">
          返回列表
        </a>
        <div className="flex flex-wrap items-center gap-3">
          <span>Session: {sessionId.substring(0, 8)}</span>
          <span
            className={`px-2 py-0.5 rounded text-xs ${getSessionStatusStyle(session?.status)}`}
          >
            {session?.status || (initialLoading ? "loading" : "unknown")}
          </span>
          {traceId && (
            <button
              onClick={refreshTrace}
              className="text-xs text-indigo-600 hover:text-indigo-700"
              disabled={traceLoading}
            >
              Trace: {traceId.substring(0, 8)}
            </button>
          )}
        </div>
      </div>

      {initialLoading && (
        <div className="rounded border border-gray-200 bg-white p-3 text-sm text-gray-500">
          正在恢复会话状态...
        </div>
      )}

      {loadError && (
        <div className="rounded border border-yellow-200 bg-yellow-50 p-3 text-sm text-yellow-700">
          {loadError}
          <button
            onClick={refreshSessionState}
            className="ml-3 rounded border border-yellow-300 px-2 py-0.5 text-xs"
          >
            重试
          </button>
        </div>
      )}

      {actionMessage && (
        <div className="rounded border border-blue-100 bg-blue-50 p-3 text-sm text-blue-700">
          {actionMessage}
        </div>
      )}

      {hasProgress && (
        <div className="flex flex-wrap gap-1">
          {progress.map((p) => (
            <span
              key={p.stage}
              className={`px-2 py-0.5 rounded text-xs ${
                p.done
                  ? "bg-indigo-100 text-indigo-700"
                  : session?.status === "waiting_approval" && p.stage === "memory_updated"
                    ? "bg-yellow-100 text-yellow-700"
                    : "bg-gray-100 text-gray-400"
              }`}
            >
              {STAGE_LABELS[p.stage] || p.stage}
            </span>
          ))}
          {session?.status === "waiting_approval" && (
            <span className="px-2 py-0.5 rounded text-xs bg-yellow-100 text-yellow-700">
              等待授权
            </span>
          )}
        </div>
      )}

      {pendingApproval && (
        <div className="p-4 bg-yellow-50 border border-yellow-200 rounded-lg">
          <p className="font-medium text-yellow-800 mb-2">需要确认工具调用</p>
          <p className="text-sm text-yellow-700 mb-1">
            工具: <code>{pendingApproval.tool_name}</code>
          </p>
          <p className="text-sm text-yellow-700 mb-1">
            风险等级: {pendingApproval.risk_level}
          </p>
          {pendingApproval.message && (
            <p className="text-sm text-yellow-700 mb-1">{pendingApproval.message}</p>
          )}
          <p className="mt-2 text-xs text-yellow-700">{approvalSummary(pendingApproval)}</p>
          <div className="flex gap-2 mt-3">
            <button
              onClick={handleApprove}
              disabled={resuming || pendingApproval.status === "executing"}
              className="px-4 py-1.5 bg-green-600 text-white rounded text-sm hover:bg-green-700 disabled:opacity-50"
            >
              {resuming
                ? "执行中..."
                : pendingApproval.status === "approved"
                  ? "继续执行"
                  : pendingApproval.status === "executing"
                    ? "服务端执行中"
                    : "允许并继续"}
            </button>
            {pendingApproval.status === "pending" && (
              <button
                onClick={handleDeny}
                disabled={resuming}
                className="px-4 py-1.5 bg-red-600 text-white rounded text-sm hover:bg-red-700 disabled:opacity-50"
              >
                拒绝
              </button>
            )}
          </div>
        </div>
      )}

      {trace && (
        <div className="rounded-lg border border-gray-200 bg-white p-4">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-sm font-medium text-gray-700">Trace Events</p>
            <span className="text-xs text-gray-400">{trace.status}</span>
          </div>
          <div className="max-h-64 space-y-2 overflow-auto">
            {(trace.events || []).map((event: any, index: number) => (
              <div key={`${event.id || event.event_type}-${index}`} className="rounded bg-gray-50 p-2 text-xs">
                <div className="font-medium text-gray-700">
                  {event.step_index}. {event.event_type}
                </div>
                <div className="mt-1 whitespace-pre-wrap text-gray-500">
                  {summarize(event.data || event, 360)}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {traceError && (
        <div className="rounded border border-red-100 bg-red-50 p-3 text-sm text-red-600">
          {traceError}
        </div>
      )}

      <div className="space-y-4 min-h-[300px] max-h-[60vh] overflow-y-auto bg-white rounded-lg border border-gray-200 p-4">
        {messages.length === 0 && !loading && !initialLoading && (
          <div className="text-center text-gray-400 py-12">
            <p className="mb-2">开始 AI 面试诊断</p>
            <p className="text-sm">输入面试题和候选回答，或上传面试录音</p>
          </div>
        )}

        {messages.map((msg, i) => (
          <div
            key={i}
            className={`p-3 rounded-lg ${
              msg.role === "user" ? "bg-indigo-50 ml-8" : "bg-gray-50 mr-8"
            }`}
          >
            <div className="text-xs text-gray-400 mb-1">
              {msg.role === "user" ? "你" : msg.role === "assistant" ? "AI 诊断" : "系统"}
            </div>
            {msg.role === "assistant" && msg.content ? (
              <div className="markdown-body">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
              </div>
            ) : (
              <div className="whitespace-pre-wrap text-sm">{msg.content}</div>
            )}

            {msg.events && msg.events.length > 0 && (
              <div className="mt-2 pt-2 border-t border-gray-200">
                <details className="text-xs">
                  <summary className="text-gray-500 cursor-pointer">
                    查看工具调用 ({msg.events.filter((e) => e.type === "tool_call").length})
                  </summary>
                  <div className="mt-2 space-y-1">
                    {msg.events
                      .filter((e) =>
                        ["tool_call", "tool_result", "permission_required", "error"].includes(e.type)
                      )
                      .map((e, j) => (
                        <div key={j} className="rounded bg-white p-2 text-gray-500">
                          <span className="font-medium">
                            {e.type === "tool_call"
                              ? "工具调用"
                              : e.type === "tool_result"
                                ? "工具结果"
                                : e.type === "permission_required"
                                  ? "等待授权"
                                  : "错误"}
                          </span>
                          {eventToolName(e) && <span>：{eventToolName(e)}</span>}
                          {eventMessage(e) && <span className="ml-2 text-red-500">{eventMessage(e)}</span>}
                          {toolResultError(e) && (
                            <span className="ml-2 text-red-500">
                              {toolResultError(e)}
                            </span>
                          )}
                        </div>
                      ))}
                  </div>
                </details>
              </div>
            )}
          </div>
        ))}

        {loading && (
          <div className="flex items-center gap-1 text-gray-400 p-3">
            <span>AI 正在诊断</span>
            <span className="typing-dot">.</span>
            <span className="typing-dot">.</span>
            <span className="typing-dot">.</span>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {reportMarkdown && (
        <div className="flex gap-2">
          <button
            onClick={handleCopyReport}
            className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
          >
            复制报告
          </button>
          <button
            onClick={handleExportReport}
            disabled={!reportId}
            className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
          >
            导出 Markdown
          </button>
        </div>
      )}

      <div className="p-3 bg-gray-50 rounded-lg border border-dashed border-gray-300">
        <div className="flex flex-wrap items-center gap-3">
          <label
            className={`text-sm text-gray-600 ${
              audioStatus === "uploading" ? "cursor-not-allowed opacity-50" : "cursor-pointer hover:text-indigo-600"
            }`}
          >
            <input
              type="file"
              accept=".wav,.mp3"
              className="hidden"
              disabled={audioStatus === "uploading"}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                if (!["audio/wav", "audio/mpeg", "audio/mp3"].includes(file.type)) {
                  setAudioError("仅支持 wav/mp3 音频文件。");
                  setAudioStatus("failed");
                  return;
                }
                if (file.size > 25 * 1024 * 1024) {
                  setAudioError("文件不能超过 25MB。");
                  setAudioStatus("failed");
                  return;
                }
                setAudioFile(file);
                setAudioStatus("selected");
                setAudioError("");
                setAudioInfo("");
              }}
            />
            选择音频文件
          </label>
          {audioFile && (
            <span className="text-sm text-gray-500">
              {audioFile.name} ({(audioFile.size / 1024 / 1024).toFixed(1)}MB)
            </span>
          )}
          {audioFile && (
            <button
              onClick={handleAudioUpload}
              disabled={audioStatus === "uploading"}
              className="px-3 py-1 text-sm bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50"
            >
              {audioStatus === "uploading" ? "上传中..." : "上传并请求转写"}
            </button>
          )}
          {(audioStatus === "manual" || audioStatus === "failed") && (
            <button
              onClick={() => setAudioStatus("manual")}
              className="px-3 py-1 text-sm border border-gray-300 rounded hover:bg-white"
            >
              手动 transcript
            </button>
          )}
        </div>
        {audioInfo && <p className="text-sm text-green-600 mt-2">{audioInfo}</p>}
        {audioError && <p className="text-sm text-red-500 mt-2">{audioError}</p>}
        {audioStatus === "manual" && (
          <div className="mt-3 space-y-2">
            <textarea
              value={manualTranscript}
              onChange={(e) => setManualTranscript(e.target.value)}
              rows={4}
              className="w-full rounded border border-gray-300 p-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
              placeholder="粘贴音频 transcript，保存后会填入下方诊断输入框"
            />
            <button
              onClick={handleManualTranscript}
              className="px-3 py-1.5 text-sm bg-indigo-600 text-white rounded hover:bg-indigo-700"
            >
              保存 transcript
            </button>
          </div>
        )}
        <p className="text-xs text-gray-400 mt-2">
          支持 .wav / .mp3，最大 25MB。ASR 会先请求权限，转写后文本将填入输入框。
        </p>
      </div>

      <div className="border-t border-gray-200 pt-4 space-y-3">
        <div className="inline-flex border border-gray-300 rounded-md overflow-hidden" role="tablist" aria-label="运行模式">
          <button
            type="button"
            role="tab"
            aria-selected={mode === "coach"}
            onClick={() => setMode("coach")}
            disabled={loading}
            className={`px-3 py-1.5 text-sm ${mode === "coach" ? "bg-indigo-600 text-white" : "bg-white text-gray-700"}`}
          >
            练习对话
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "diagnosis"}
            onClick={() => setMode("diagnosis")}
            disabled={loading}
            className={`px-3 py-1.5 text-sm border-l border-gray-300 ${mode === "diagnosis" ? "bg-indigo-600 text-white" : "bg-white text-gray-700"}`}
          >
            正式诊断
          </button>
        </div>

        {mode === "coach" ? (
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSubmit();
              }
            }}
            placeholder="输入练习目标、追问方向或题库推荐需求"
            rows={4}
            className="w-full p-3 border border-gray-300 rounded-md resize-none focus:outline-none focus:ring-2 focus:ring-indigo-400 text-sm"
            disabled={loading}
          />
        ) : (
          <div className="space-y-2">
            <textarea
              value={diagnosisQuestion}
              onChange={(e) => setDiagnosisQuestion(e.target.value)}
              placeholder="面试题"
              rows={3}
              className="w-full p-3 border border-gray-300 rounded-md resize-none focus:outline-none focus:ring-2 focus:ring-indigo-400 text-sm"
              disabled={loading}
            />
            <textarea
              value={diagnosisAnswer}
              onChange={(e) => setDiagnosisAnswer(e.target.value)}
              placeholder="候选人回答"
              rows={6}
              className="w-full p-3 border border-gray-300 rounded-md resize-none focus:outline-none focus:ring-2 focus:ring-indigo-400 text-sm"
              disabled={loading}
            />
          </div>
        )}

        <div className="flex justify-end gap-2">
          {loading && (
            <button
              type="button"
              onClick={handleStop}
              className="px-4 py-2 border border-red-300 text-red-700 rounded-md hover:bg-red-50"
            >
              停止
            </button>
          )}
          <button
            onClick={() => void handleSubmit()}
            disabled={loading || (mode === "coach" ? !input.trim() : !diagnosisQuestion.trim() || !diagnosisAnswer.trim())}
            className="px-6 py-2 bg-indigo-600 text-white rounded-md hover:bg-indigo-700 transition disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? "运行中..." : mode === "coach" ? "发送" : "开始诊断"}
          </button>
        </div>
      </div>
    </div>
  );
}
