# OfferPilot Lite

基于 **FastAPI + 原生 Function Calling Coach Agent + Next.js** 的受限技术面试教练。

业务聚焦：技术面试练习、复盘、追问和题库推荐。普通练习使用受限 ReAct；正式评分通过显式的确定性 Workflow 执行。项目不是开放域 Chatbot，也不处理简历、JD 或泛求职问题。

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
Copy-Item .env.example .env
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
OFFERPILOT_PROFILE_SIGNING_KEY=至少32字节的随机值
OFFERPILOT_CORS_ORIGINS=http://localhost:3000
```

启动后端：

```powershell
& .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Set-Location src\api
& ..\..\.venv\Scripts\python.exe -m uvicorn offerpilot.main:app --host 0.0.0.0 --port 8000
```

启动前端：

```powershell
Set-Location src\web
npm.cmd install
npm.cmd run dev
```

打开 http://localhost:3000

Docker Compose：

```powershell
docker compose up --build
```

Compose 默认以本地 HTTP 调试配置运行，并通过 `API_PORT`、`WEB_PORT` 暴露 API 和 Web（默认分别为 `8000`、`3000`）。Web 镜像在构建期接收 `API_BASE_URL`，默认值为容器网络内的 `http://api:8000`，由 Next.js rewrite 代理浏览器的 `/api/*` 请求。生产部署必须显式设置 `OFFERPILOT_DEBUG=false`、HTTPS 的 `OFFERPILOT_CORS_ORIGINS`，并在 Web 前配置 TLS 终端。

## 当前业务边界

- 支持：练习对话、追问、历史报告、已批准记忆读取和题库推荐。
- 支持：单题对标正式诊断，用户提供“面试题 + 自己的回答”。
- 支持：音频上传、ASR 权限确认、transcript 回填回答框。
- 支持：FTS5 + Embedding 双通道题库检索、RRF 合并。
- 支持：Permission / Session / Audit / Memory candidates / Trace / Eval。
- 不支持：开放域知识问答。
- 不支持：多 Agent、模拟面试、TTS、JD/简历分析。

## Coach Agent

后端主链路运行在 `src/api/offerpilot/agent/`：

- `coach_loop.py`：原生 Function Calling Coach Loop，最多 4 轮、8 次工具调用和 45 秒活跃执行预算。
- `registry.py`：按 Profile 限制的工具注册表，使用严格 Pydantic 参数 schema。
- `coaching/state.py`：跨审批、跨进程恢复的 Agent 轨迹与审批状态。

正式诊断链路：

```text
`POST /api/coach { mode: "diagnosis", diagnosis: { question, answer } }`
-> 直接进入 DiagnosisWorkflow（不调用 Coach 模型）
-> FTS5 + Embedding + RRF + 一次结构化评分 + output_check
-> 报告持久化 / 记忆审批 / Trace
-> report_ready / final_response / run_complete
```

普通 `mode="coach"`（或兼容的未传 `mode` 请求）才进入原生 `tool_calls` 循环，最多 4 轮、8 次工具调用和 45 秒活跃执行预算。普通对话请求评分时只返回“切换到正式诊断”的引导，绝不生成非正式分数。

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
- 业务知识检索仅由 Coach 和 Diagnosis 在进程内调用，没有公开 HTTP 检索接口。
- 显式重建：`POST /api/admin/knowledge/reindex`，必须携带 `X-OfferPilot-Admin-Key`。
- 索引状态：`GET /api/admin/knowledge/status`，必须携带 `X-OfferPilot-Admin-Key`。
- CLI 重建：`python -m offerpilot.knowledge.reindex`。

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

- SSE 入口：`POST /api/coach`，请求必须符合以下一种模式：
  - `mode="coach"`：提供 `message`；未传 `mode` 兼容为该模式。
  - `mode="diagnosis"`：只提供 `diagnosis.question` 和 `diagnosis.answer`。
- 每个 SSE 数据事件都有 `type`、`session_id`、`trace_id`、`sequence`；流以 `run_complete` 收尾，正式报告额外发送 `report_ready`。
- `POST /api/coach/cancel` 以 `{ session_id, trace_id }` 取消当前轮，不终止整个 Session。
- Coach 通过原生 Function Calling 选择受限工具；正式评分不会出现在 Coach 工具表中。
- 权限路径：`/api/permission/*`
- 知识库管理：`GET /api/admin/knowledge/status`、`POST /api/admin/knowledge/reindex`，均需 Admin Key；业务检索不提供 HTTP 接口。
- Coach 恢复：`POST /api/coach/resume`
- 恢复状态：`GET /api/coach/state?session_id=...`
- 报告导出：`POST /api/coach/reports/export`，请求体为 `{ session_id, report_id }`。
- Profile 使用 `offerpilot_profile` HttpOnly 签名 Cookie（30 天、`SameSite=Lax`）。`POST /api/profile/bootstrap` 无需既有身份，但与其他写接口一样必须携带可信 Origin。业务接口永久拒绝旧 `X-OfferPilot-Profile-Id` Header；迁移仅限 bootstrap 的受控开关。

## 测试

```powershell
& .\.venv\Scripts\python.exe -m pytest
```

```powershell
Push-Location src\web
npm.cmd exec tsc -- --noEmit
npm.cmd run build
npm.cmd run test:e2e
Pop-Location
```

```powershell
Push-Location src\api
& ..\..\.venv\Scripts\python.exe -m offerpilot.llm.provider_probe
Pop-Location
```

```powershell
docker compose config
& .\scripts\verification\verify-compose-stack.ps1
```

```powershell
& .\scripts\verification\verify-live-workflow.ps1
```

真实工作流验证使用隔离 SQLite 数据库和独立 API 端口，验证真实 Provider、Profile Cookie、原生工具调用、Embedding/RRF、正式诊断、导出审批、音频审批重启恢复、备份完整性和应用日志脱敏。它不输出密钥、Cookie、候选人答案或 Provider 原始响应；只有全部命令以退出码 `0` 结束才可作为交付证据。

生产环境启动前，`OFFERPILOT_PROFILE_SIGNING_KEY` 缺失或少于 32 字节会使 API 拒绝启动。将 `OFFERPILOT_CORS_ORIGINS` 设置为实际 Web 的显式 HTTPS 来源；空值、`*`、`null`、路径和非 HTTP(S) 值均会被拒绝。由于 Profile Cookie 在生产环境使用 `Secure`，必须在 Web 前提供 TLS 终端。

## 目录结构

```text
lite-offerpilot/
├── src/
│   ├── api/offerpilot/  FastAPI 后端 + Python Agent Loop
│   └── web/             Next.js 前端及依赖清单
├── tests/               API 与 Web 测试
├── infra/docker/        服务镜像定义
├── scripts/             备份与验证脚本
├── harness/
│   ├── rules/           行为规则
├── knowledge/           题库型 RAG Markdown
├── docs/                架构与设计文档
├── requirements.txt     后端运行依赖
├── requirements-dev.txt 后端测试依赖
├── docker-compose.yml
├── .env.example
└── README.md
```

### 后端模块

`src/api/offerpilot/` 按业务领域组织，避免把路由、模型调用和存储逻辑混在一起：

- `agent/`：固定诊断 Loop、工具注册与运行时类型。
- `diagnosis/`：单次结构化诊断、Context、报告持久化与 HTTP 入口。
- `knowledge/`：题库解析、FTS5、Embedding、RRF 与 reload。
- `audio/`：MiMo ASR 上传和转写。
- `session/`、`permission/`、`trace/`：会话、审批审计和可观测性。
- `harness/`、`eval/`、`reports/`：输出约束、回归评估和报告导出。
- `core/`、`llm/`、`chat/`：共享配置/数据库/错误、模型适配和 SSE 入口。
