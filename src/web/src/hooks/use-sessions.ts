import { useCallback, useState } from "react";
import { createSession, requestJson } from "../lib/api";
import type { Session, SessionStatus } from "../lib/types";

type SessionListResponse = { sessions?: Session[]; next_cursor?: string | null };

function mergeSessions(current: Session[], incoming: Session[]): Session[] {
  const seen = new Set(current.map((item) => item.id));
  return [...current, ...incoming.filter((item) => !seen.has(item.id))];
}

export function useSessions() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sessionFilter, setSessionFilter] = useState<SessionStatus>("active");
  const [sessionCursor, setSessionCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const loadSessions = useCallback(async (filter: SessionStatus = sessionFilter, append = false) => {
    setLoading(true);
    try {
      const query = new URLSearchParams({ status: filter, limit: "20" });
      if (append && sessionCursor) query.set("cursor", sessionCursor);
      const data = await requestJson<SessionListResponse>(`/sessions?${query.toString()}`);
      setSessions((current) => append ? mergeSessions(current, data.sessions || []) : (data.sessions || []));
      setSessionCursor(data.next_cursor || null);
    } finally {
      setLoading(false);
    }
  }, [sessionCursor, sessionFilter]);

  const changeFilter = useCallback((filter: SessionStatus) => {
    setSessionFilter(filter);
    setSessionCursor(null);
    void loadSessions(filter, false);
  }, [loadSessions]);

  const createNewSession = useCallback(() => createSession(), []);

  const updateSessionTitle = useCallback(async (id: string, title: string) => {
    return requestJson<Session>(`/sessions/${id}`, { method: "PATCH", body: JSON.stringify({ title }) });
  }, []);

  const archiveSession = useCallback(async (id: string, archived: boolean) => {
    return requestJson<Session>(`/sessions/${id}`, { method: "PATCH", body: JSON.stringify({ archived }) });
  }, []);

  const deleteSession = useCallback(async (id: string) => {
    await requestJson(`/sessions/${id}`, { method: "DELETE", body: "{}" });
  }, []);

  return {
    sessions,
    sessionFilter,
    sessionCursor,
    loading,
    loadSessions,
    loadMoreSessions: () => loadSessions(sessionFilter, true),
    createNewSession,
    updateSessionTitle,
    archiveSession,
    deleteSession,
    changeFilter,
  };
}
