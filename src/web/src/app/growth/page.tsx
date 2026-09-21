"use client";

import { useEffect, useState } from "react";
import { getGrowth } from "../../lib/api";
import type { Growth } from "../../lib/types";

function Info({ label, value }: { label: string; value: string }) {
  return <div className="border border-slate-200 bg-white p-4"><p className="text-[11px] uppercase tracking-wide text-slate-400">{label}</p><p className="mt-1 text-sm leading-6 text-slate-700">{value}</p></div>;
}

function GrowthView() {
  const [growth, setGrowth] = useState<Growth | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    void getGrowth().then(setGrowth).catch((loadError: unknown) => setError(loadError instanceof Error ? loadError.message : "成长数据加载失败。"));
  }, []);
  return <main className="min-h-screen bg-slate-100 p-8">
    <div className="mx-auto max-w-4xl">
      <div className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-4">
        <h1 className="text-lg font-semibold">成长概览</h1>
        <button type="button" onClick={() => window.history.back()} className="border border-slate-300 px-3 py-1.5 text-xs">返回</button>
      </div>
      {error && <p className="mt-4 text-sm text-red-700">{error}</p>}
      {!error && !growth && <p className="mt-4 text-sm text-slate-500">正在加载成长数据...</p>}
      {growth && <div className="mt-6 grid grid-cols-2 gap-4">
        <Info label="诊断次数" value={String(growth.summary_json?.diagnosis_count ?? 0)} />
        <Info label="观察考点" value={String(growth.summary_json?.observed_points ?? 0)} />
        <Info label="反复薄弱点" value={(growth.summary_json?.recurring_weaknesses || []).join("、") || "暂无"} />
        <Info label="已掌握主题" value={(growth.summary_json?.mastered_topics || []).join("、") || "暂无"} />
      </div>}
    </div>
  </main>;
}

function DesktopOnlyNotice() {
  return <div className="flex min-h-screen items-center justify-center bg-slate-100 p-8">
    <div className="max-w-md border border-slate-200 bg-white p-6 text-center">
      <h1 className="text-lg font-semibold">请使用桌面浏览器</h1>
      <p className="mt-2 text-sm leading-6 text-slate-600">OfferPilot 当前面向桌面端使用，请使用宽度不小于 1280px 的浏览器。</p>
    </div>
  </div>;
}

export default function GrowthPage() {
  return <>
    <div className="hidden min-[1280px]:block"><GrowthView /></div>
    <div className="block min-[1280px]:hidden"><DesktopOnlyNotice /></div>
  </>;
}
