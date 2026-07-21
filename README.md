# OfferPilot Lite

基于 **FastAPI + 原生 Function Calling Coach Agent + Next.js** 的受限自主技术面试教练。

业务聚焦：技术面试练习、复盘、追问和题库推荐。正式评分仅由受限的 `run_diagnosis` 复合工具执行；项目不是开放域 Chatbot，也不处理简历、JD 或泛求职问题。

## 架构

```text
Web UI (Next.js)
  └─→ FastAPI Backend (Python)
        ├─→ Python Agent Loop
        │     ├─→ Harness / Tool Registry / Budget / Output Checker
        │     ├─→ Permission / Session / Checkpoint / Progress
        │     └─→ Trace / Eval / Memory candidates
        ├─→ SQLite / FTS5 / Embedding
        ├─→ OpenAI-compatible chat + Embedding
        └─→ MiMo ASR
```

## 快速启动

```powershell
copy .env.example .env
```

编辑 `.env`，分别设置文本模型、Embedding 与 MiMo：

```env
OFFERPILOT_OPENAI_API_KEY=
OFFERPILOT_OPENAI_BASE_URL=https://your-text-gateway.example/v1
OFFERPILOT_OPENAI_MODEL=
OFFERPILOT_EMBEDDING_API_KEY=
OFFERPILOT_EMBEDDING_BASE_URL=https://your-embedding-gateway.example/v1
OFFERPILOT_EMBEDDING_MODEL=
MIMO_API_KEY=
```

启动后端：

```powershell
cd apps\api
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

启动前端：

```powershell
cd apps\web
npm.cmd install
npm.cmd run dev
```

打开 http://localhost:3000

Docker Compose：

```powershell
docker compose up --build
```

## 当前业务边界

- 支持：单题对标诊断，用户提供“面试题 + 自己的回答”。
- 支持：音频上传、ASR 权限确认、transcript 回填回答框。
- 支持：FTS5 + Embedding 双通道题库检索、RRF 合并。
- 支持：Permission / Session / Audit / Memory candidates / Trace / Eval。
- 不支持：开放域知识问答。
- 不支持：多 Agent、模拟面试、TTS、JD/简历分析。

## Coach Agent

后端主链路运行在 `apps/api/app/agent/`：

- `coach_loop.py`：原生 Function Calling Coach Loop，最多 4 轮、8 次工具调用和 45 秒活跃执行预算。
- `registry.py`：按 Profile 限制的工具注册表，使用严格 Pydantic 参数 schema。
- `coaching/state.py`：跨审批、跨进程恢复的 Agent 轨迹与审批状态。

诊断链路：

```text
用户消息 + 最近 12 条会话历史
-> native tool_calls
-> 受限 Coach 工具
-> run_diagnosis（FTS5 + Embedding + RRF + 一次结构化评分 + output_check）
-> 报告持久化 / 记忆审批 / Trace
-> final_response 或 waiting_approval -> resume
```

## 知识库与检索

本项目使用题库型 RAG：一个 Markdown 文件代表一道面试题，含 Q / 新手答 / 高手答 / 考察点 / 常见缺失 / 追问。

```text
面试题
  ├─→ FTS5 关键词检索（top 10）
  ├─→ Embedding 语义检索（top 10）
  └─→ RRF (k=60) 合并去重 → top 5
```

- Embedding 独立配置，不复用文本模型配置。
- 正式环境要求 Embedding 可用；未配置、调用失败或当前模型无向量时诊断返回可重试错误。
- `/api/tools/search-knowledge` 是 internal/debug tool API，不作为用户功能展示。
- 显式重建：`POST /api/admin/knowledge/reindex`，必须携带 `X-OfferPilot-Admin-Key`。
- CLI 重建：`python -m app.knowledge.reindex`。

Embedding 配置：

```env
OFFERPILOT_EMBEDDING_PROVIDER=openai-compatible
OFFERPILOT_EMBEDDING_API_KEY=
OFFERPILOT_EMBEDDING_BASE_URL=
OFFERPILOT_EMBEDDING_MODEL=BAAI/bge-m3
OFFERPILOT_EMBEDDING_TIMEOUT_SECONDS=30
OFFERPILOT_REQUIRE_EMBEDDING=true
OFFERPILOT_ADMIN_KEY=change-me-before-enabling-reindex
```

## API

- 唯一正式 SSE 入口：`POST /api/coach`
- Coach 通过原生 Function Calling 选择受限工具；正式评分只能经由确定性 `run_diagnosis` 复合工具。
- 权限路径：`/api/permission/*`
- 知识检索：`GET /api/tools/search-knowledge` 或 `POST /api/tools/search-knowledge`
- Coach 恢复：`POST /api/coach/resume`
- 报告导出：`GET /api/coach/reports/export`
- 会话归属接口要求 `X-OfferPilot-Profile-Id`。Web 会自动生成并保存在当前浏览器；这是匿名本地身份，不是账户登录。

## 测试

```powershell
cd apps\api
python -m pytest tests
```

```powershell
cd apps\web
npm.cmd run build
```

```powershell
cd D:\项目\OfferPilot\lite-offerpilot
docker compose config
```

## 目录结构

```text
lite-offerpilot/
├── apps/
│   ├── api/             FastAPI 后端 + Python Agent Loop
│   └── web/             Next.js 前端
├── harness/
│   ├── rules/           行为规则
├── knowledge/           题库型 RAG Markdown
├── docs/                架构与设计文档
├── docker-compose.yml
├── .env.example
└── README.md
```

### 后端模块

`apps/api/app/` 按业务领域组织，避免把路由、模型调用和存储逻辑混在一起：

- `agent/`：固定诊断 Loop、工具注册与运行时类型。
- `diagnosis/`：单次结构化诊断、Context、报告持久化与 HTTP 入口。
- `knowledge/`：题库解析、FTS5、Embedding、RRF 与 reload。
- `audio/`：MiMo ASR 上传和转写。
- `session/`、`permission/`、`trace/`：会话、审批审计和可观测性。
- `harness/`、`eval/`、`reports/`：输出约束、回归评估和报告导出。
- `core/`、`llm/`、`chat/`：共享配置/数据库/错误、模型适配和 SSE 入口。
