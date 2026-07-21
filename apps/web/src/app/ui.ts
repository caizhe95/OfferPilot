export const SESSION_STATUS_STYLES: Record<string, string> = {
  created: "bg-gray-100 text-gray-600",
  running: "bg-blue-100 text-blue-700",
  waiting_approval: "bg-yellow-100 text-yellow-700",
  completed: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
  cancelled: "bg-gray-100 text-gray-500",
};

export const PROGRESS_STAGES = [
  "input_received",
  "knowledge_retrieved",
  "diagnosis_evaluated",
  "memory_updated",
  "report_generated",
  "output_checked",
  "completed",
];

export const STAGE_LABELS: Record<string, string> = {
  input_received: "接收输入",
  knowledge_retrieved: "知识检索",
  diagnosis_evaluated: "诊断评估",
  memory_updated: "记忆更新",
  report_generated: "生成报告",
  output_checked: "输出检查",
  completed: "完成",
};

export function getSessionStatusStyle(status?: string) {
  return SESSION_STATUS_STYLES[status || ""] || "bg-gray-100 text-gray-600";
}
