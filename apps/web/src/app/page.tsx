"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { getSessionStatusStyle } from "./ui";

const API = "/api";

export default function HomePage() {
  const router = useRouter();
  const [sessions, setSessions] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API}/sessions?limit=50`)
      .then((r) => r.json())
      .then((data) => setSessions(data.sessions || []))
      .finally(() => setLoading(false));
  }, []);

  const createSession = async () => {
    const res = await fetch(`${API}/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ metadata: {} }),
    });
    const data = await res.json();
    router.push(`/session/${data.id}`);
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">诊断会话</h1>
        <button
          onClick={createSession}
          className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition"
        >
          新建诊断
        </button>
      </div>

      {loading ? (
        <div className="text-center text-gray-400 py-12">加载中...</div>
      ) : sessions.length === 0 ? (
        <div className="text-center py-12">
          <p className="text-gray-400 mb-4">暂无会话，点击上方按钮开始</p>
        </div>
      ) : (
        <div className="space-y-2">
          {sessions.map((s: any) => (
            <div
              key={s.id}
              onClick={() => router.push(`/session/${s.id}`)}
              className="flex items-center justify-between p-4 bg-white rounded-lg border border-gray-200 cursor-pointer hover:border-indigo-300 transition"
            >
              <div>
                <span className="font-mono text-sm text-gray-500">
                  {s.id.substring(0, 8)}...
                </span>
              </div>
              <div className="flex items-center gap-3">
                <span className={`px-2 py-0.5 rounded text-xs font-medium ${getSessionStatusStyle(s.status)}`}>
                  {s.status}
                </span>
                <span className="text-xs text-gray-400">
                  {new Date(s.created_at).toLocaleString("zh-CN")}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
