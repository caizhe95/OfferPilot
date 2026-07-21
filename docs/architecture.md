# OfferPilot Lite 架构设计

## 服务拓扑

```text
┌─────────────────────────────────────────────┐
│                   Web UI                    │
│              Next.js / React                │
└──────────────────┬──────────────────────────┘
                   │ HTTP + SSE
                   ▼
┌─────────────────────────────────────────────┐
│              FastAPI Backend                │
│                                             │
│  ┌──────────────────────────────────────┐   │
│  │        Python Agent Loop              │   │
│  │  Tool Registry / Harness / Budget     │   │
│  │  Output Checker / Trace Events        │   │
│  └──────────────────────────────────────┘   │
│                                             │
│  ┌──────────┐  ┌───────────┐  ┌──────────┐ │
│  │ Session  │  │ Permission│  │ Memory   │ │
│  │ Progress │  │ Audit     │  │ Eval     │ │
│  │Checkpoint│  │ Approval  │  │ Trace    │ │
│  └──────────┘  └───────────┘  └──────────┘ │
│                                             │
│  ┌──────────────────────────────────────┐   │
│  │       SQLite / FTS5 / Embedding       │   │
│  └──────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

## 目录结构

```text
lite-offerpilot/
  apps/
    api/              FastAPI 后端与 Python Agent Loop
    web/              Web UI

  harness/
    rules/            全局和行为约束规则

  knowledge/          题库型 Markdown 知识库
  docs/               架构和设计文档
```

## 数据流

1. 用户在 Web UI 输入面试题和回答。
2. FastAPI 创建 Session，记录 `input_received` 和 `qa_extracted`。
3. Python Agent Loop 执行固定单题诊断工具计划。
4. `search_knowledge` 同时执行 FTS5 与向量检索，使用 RRF 合并参考题。
5. Context 的固定规则、同一 Profile 已审批记忆和检索证据进入一次 `diagnose_interview` 结构化模型调用。
6. Python 校验考点证据、计算总分、渲染固定报告并保存结构化结果。
7. 如存在 memory candidate，返回 `permission_required` 卡片，但不绕过用户确认写入。
8. Trace、progress、checkpoint 和 SSE 事件同步返回给前端。

## 匿名 Profile

Web 首次访问生成 UUID 并保存在浏览器本地存储。会话、音频、报告、审批和 trace 请求通过 `X-OfferPilot-Profile-Id` 绑定到该 Profile。它用于匿名隔离与跨会话记忆，不是账户认证。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11+, FastAPI, SQLite, FTS5 |
| Agent Runtime | Python hand-written loop |
| 模型接口 | OpenAI-compatible Text / Embedding，MiMo ASR |
| 前端 | Next.js, React, TailwindCSS |
| 部署 | Docker, Docker Compose |

## 不做

- 多 Agent 协作
- 模拟面试流程
- TTS（文字转语音）
- JD / 简历解析
- 开放域知识库问答
- 独立向量数据库（当前使用 SQLite 中的 JSON 向量与内存余弦计算）
