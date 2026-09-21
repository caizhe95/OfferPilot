"use client";

import { useParams } from "next/navigation";
import SessionWorkspace from "../../session-workspace";

export default function SessionPage() {
  const params = useParams<{ id: string }>();
  return <SessionWorkspace sessionId={String(params.id || "")} />;
}
