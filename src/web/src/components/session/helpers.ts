import type { Approval, RunType } from "../../lib/types";

export function formatTime(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function formatDuration(value?: number | null): string {
  if (value === undefined || value === null) return "-";
  if (value < 1000) return `${Math.round(value)} ms`;
  return `${(value / 1000).toFixed(1)} s`;
}

export function approvalText(approval: Approval): string {
  if (approval.flow_kind === "audio") {
    const filename = typeof approval.public_params?.filename === "string" ? approval.public_params.filename : "受控音频文件";
    const size = typeof approval.public_params?.size === "number" ? `，${(approval.public_params.size / 1024).toFixed(1)} KB` : "";
    return `${filename}${size}`;
  }
  if (approval.flow_kind === "export") return "生成当前正式诊断报告的 Markdown 文件";
  return "继续当前练习并调用受控工具";
}

export function runLabel(type: RunType): string {
  return { coach: "练习", diagnosis: "正式诊断", audio_transcription: "音频转写", report_export: "报告导出" }[type];
}
