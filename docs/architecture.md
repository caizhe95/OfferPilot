# OfferPilot Lite 架构设计

## 服务拓扑

```
┌─────────────────────────────────────────────┐
│                   Web UI                     │
│              (Next.js / React)               │
└──────────────────┬──────────────────────────┘
                   │ HTTP + SSE
                   ▼
┌─────────────────────────────────────────────┐
│              FastAPI Backend                 │
│                                              │
│  ┌──────────┐  ┌───────────┐  ┌──────────┐ │
│  │ Session  │  │ Permission│  │ Memory   │ │
│  ├──────────┤  ├───────────┤  ├──────────┤ │
│  │ Progress │  │ Approval  │  │ Trace    │ │
│  ├──────────┤  ├───────────┤  ├──────────┤ │
│  │Checkpoint│  │ Audit     │  │ Eval     │ │
│  └──────────┘  └───────────┘  └──────────┘ │
│                                              │
│  ┌──────────────────────────────────────┐   │
│  │         SQLite / FTS5                 │   │
│  └──────────────────────────────────────┘   │
└──────────────────┬──────────────────────────┘
                   │ HTTP + SSE
                   ▼
┌─────────────────────────────────────────────┐
│          pi-mono Agent Service               │
│                                              │
│  ┌───────┐ ┌────────┐ ┌───────┐ ┌───────┐  │
│  │ Rules │ │ Skills │ │ Tools │ │ Hooks │  │
│  ├───────┤ ├────────┤ ├───────┤ ├───────┤  │
│  │Budget │ │ Output │ │Context │ │Memory │  │
│  │       │ │Checker │ │Builder │ │       │  │
│  └───────┘ └────────┘ └───────┘ └───────┘  │
└─────────────────────────────────────────────┘
```

## 目录结构

```
lite-offerpilot/
  apps/
    api/              FastAPI 后端
    agent-ts/         pi-mono 单 Agent 服务
    web/              Web UI

  harness/
    rules/            全局和行为约束规则
    skills/           可迁移的 AI Skills

  knowledge/
    selected/         精选的知识库 Markdown

  docs/               架构和设计文档
```

## 数据流

1. 用户在 Web UI 输入面试题和回答
2. FastAPI 创建 Session，进入 `input_received` 阶段
3. FastAPI 调用 Agent Service `/agent/run`
4. Agent 加载 Rules、匹配 Skill、调用 Tools
5. 涉及 medium/high 风险工具时，PermissionGate 拦截
6. 状态变更为 `waiting_approval`，Web UI 展示确认卡片
7. 用户 approve / deny，Agent 继续或 fallback
8. Agent 输出最终诊断报告
9. 报告保存到 SQLite，通过 SSE 流式返回 Web UI

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11+, FastAPI, SQLite, FTS5 |
| Agent | TypeScript, pi-mono |
| 前端 | Next.js, React, TailwindCSS |
| 部署 | Docker, Docker Compose |

## 不做

- 多 Agent 协作
- 模拟面试流程
- TTS（文字转语音）
- JD / 简历解析
- 向量数据库（第一版使用 FTS5）
