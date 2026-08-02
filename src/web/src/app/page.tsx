"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { bootstrapProfile, withProfileHeaders } from "./profile";

const API = "/api";

export default function HomePage() {
  const router = useRouter();
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const canSubmit = message.trim() && !loading;

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setLoading(true);
    setError("");
    try {
      await bootstrapProfile();
      const sessionResponse = await fetch(`${API}/sessions`, {
        method: "POST",
        headers: withProfileHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({}),
      });
      if (!sessionResponse.ok) {
        const data = await sessionResponse.json();
        setError(data.detail || "教练暂时不可用，请重试");
      } else {
        const session = await sessionResponse.json();
        sessionStorage.setItem(`offerpilot-draft-${session.id}`, message.trim());
        router.push(`/session/${session.id}`);
      }
    } catch (e: any) {
      setError(e.message || "网络错误");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">技术面试教练</h1>
      <p className="text-sm text-gray-500 mb-4">
        询问练习建议、查看历史，或直接提交一题与回答进行正式诊断。
      </p>

      {/* Input form */}
      <div className="space-y-4 mb-6">
        <div>
          <textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder="例如：推荐一题考察 RAG 检索的练习题；或：请诊断这道题……我的回答是……"
            rows={7}
            className="w-full p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent resize-none"
          />
        </div>
        <button
          onClick={handleSubmit}
          disabled={!canSubmit}
          className={`w-full py-3 rounded-lg text-white font-medium transition ${
            canSubmit
              ? "bg-indigo-600 hover:bg-indigo-700"
              : "bg-gray-300 cursor-not-allowed"
          }`}
        >
          {loading ? "教练思考中..." : "开始本轮练习"}
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="p-4 mb-4 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
          {error}
        </div>
      )}

      {/* Loading indicator */}
      {loading && (
        <div className="text-center py-8 text-gray-400">
          <div className="inline-block w-6 h-6 border-2 border-indigo-600 border-t-transparent rounded-full animate-spin mb-2" />
          <p>正在进行结构化诊断，请稍候...</p>
        </div>
      )}

    </div>
  );
}
