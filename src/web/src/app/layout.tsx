import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "OfferPilot Lite - AI 面试诊断",
  description: "单 Agent 高工程版 AI Agent / LLM 面试诊断系统",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <nav className="bg-white border-b border-gray-200 px-6 py-3 flex items-center gap-4">
          <a href="/" className="text-lg font-bold text-indigo-600">
            OfferPilot Lite
          </a>
          <span className="text-sm text-gray-400">AI 面试诊断</span>
        </nav>
        <main className="max-w-4xl mx-auto p-4">{children}</main>
      </body>
    </html>
  );
}
