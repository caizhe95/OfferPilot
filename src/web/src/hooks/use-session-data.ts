import { useCallback, useRef, useState } from "react";
import { requestJson } from "../lib/api";
import type { CoachState, Followup, Message, Report, Run, Session, SessionSummary } from "../lib/types";

export function useSessionData() {
  const [session, setSession] = useState<Session | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [summary, setSummary] = useState<SessionSummary | null>(null);
  const [followups, setFollowups] = useState<Followup[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [loading, setLoading] = useState(true);
  const loadVersion = useRef(0);

  const reset = useCallback(() => {
    loadVersion.current += 1;
    setSession(null);
    setMessages([]);
    setRuns([]);
    setSummary(null);
    setFollowups([]);
    setReports([]);
  }, []);

  const loadSession = useCallback(async (id: string, withLoading = true) => {
    const version = ++loadVersion.current;
    if (withLoading) setLoading(true);
    try {
      const [sessionData, messageData, runData, summaryData, followupData, reportData, coachState] = await Promise.all([
        requestJson<Session>(`/sessions/${id}`),
        requestJson<{ messages?: Message[] }>(`/sessions/${id}/messages?n=300`),
        requestJson<{ runs?: Run[] }>(`/sessions/${id}/runs`),
        requestJson<SessionSummary>(`/sessions/${id}/summary`),
        requestJson<{ followups?: Followup[] }>(`/sessions/${id}/followups`),
        requestJson<{ reports?: Report[] }>(`/sessions/${id}/reports`),
        requestJson<CoachState>(`/coach/state?session_id=${encodeURIComponent(id)}`).catch(() => null),
      ]);
      if (version !== loadVersion.current) return null;
      setSession(sessionData);
      setMessages(messageData.messages || []);
      setRuns(runData.runs || []);
      setSummary(summaryData);
      setFollowups(followupData.followups || []);
      setReports(reportData.reports || []);
    return { session: sessionData, runs: runData.runs || [], reports: reportData.reports || [], coachState };
    } finally {
      if (withLoading && version === loadVersion.current) setLoading(false);
    }
  }, []);

  return {
    session,
    setSession,
    messages,
    setMessages,
    runs,
    setRuns,
    summary,
    setSummary,
    followups,
    setFollowups,
    reports,
    setReports,
    loading,
    setLoading,
    loadSession,
    reset,
  };
}
