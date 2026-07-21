# OfferPilot Lite

基于 **pi-mono** + **FastAPI** + **Next.js** 的 AI Agent / LLM 工程面试诊断系统。

业务聚焦：单 Agent 文本诊断 + 音频诊断，复杂度放在 Harness Engineering，不做多 Agent 和模拟面试。

## 架构

```
Web UI (Next.js)
  └─→ FastAPI Backend (Python)
        ├─→ SQLite / FTS5
        ├─→ Session / Permission / Progress / Checkpoint
        ├─→ Memory / Trace / Eval
        └─→ pi-mono Agent Service (TypeScript)
              ├─→ Rules
              ├─→ Skills
              ├─→ Tools
              ├─→ Hooks
              ├─→ Budget
              └─→ Output Checker
```

## 快速启动

### 真实 OpenAI 兼容模式

```powershell
# 复制并编辑 .env
copy .env.example .env
# 编辑 .env 设置 OPENAI_API_KEY
```

```powershell
# 1. 启动 FastAPI 后端
cd apps/api
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 2. 启动 Agent 服务（新终端）
cd apps/agent-ts
npm install
npx tsx src/server.ts

# 3. 启动 Web 前端（新终端）
cd apps/web
npm install
npm run dev
```

打开 http://localhost:3000

### Docker Compose 一键启动

```powershell
docker compose up --build
```

## 三服务关系

| 服务 | 端口 | 技术栈 | 说明 |
|------|------|--------|------|
| **api** | 8000 | Python FastAPI | 业务编排、数据库、工具、权限 |
| **agent** | 3001 | TypeScript pi-mono | Agent 推理、工具调用 |
| **web** | 3000 | Next.js | 前端 UI、SSE 流式交互 |

## 当前业务边界

- ✅ 单 Agent 文本诊断
- ✅ 音频上传 + ASR + 诊断
- ✅ 后端 Harness 闭环：Permission / Session / Audit / Memory candidates / Trace / Evals
- ❌ 不做多 Agent
- ❌ 不做模拟面试
- ❌ 不做 TTS（文字转语音）
- ❌ 不做 JD/简历分析
- ❌ 不做向量库

## 测试

```powershell
# API 测试
cd apps/api
python -m pytest tests

# Agent 测试
cd apps/agent-ts
npm test

# Web 构建检查
cd apps/web
npm run build

# 全量检查
docker compose up --build
```

## 后端 Harness 能力

- medium/high 风险工具统一返回 `permission_required` 事件。
- `save_memory`、`export_report`、`transcribe_audio` 走 PermissionGate、audit_log 和 resume 流程。
- Session 会在权限请求时进入 `waiting_approval`，approve/resume 后回到 `running`，deny 后进入 `failed`。
- `/api/diagnose` 只生成 memory candidates，不直接绕过权限写入 memory。
- FastAPI 编排层执行 pre-input、pre-tool、post-tool、post-output、Budget 和 Output Checker。
- Eval 已包含诊断质量和 Harness 工程闭环回归用例。

## 前端端到端能力

- 会话页刷新后会恢复 session 状态、历史消息、progress、latest checkpoint 和 trace id。
- SSE 诊断过程中展示 process step、tool call、tool result、permission card 和最终 Markdown 报告。
- 权限卡片点击“允许并继续”会先 approve，再调用 resume，确保 `save_memory`、`transcribe_audio`、`export_report` 真正执行。
- 权限卡片点击“拒绝”会调用 deny，刷新 session 状态，并对 ASR 场景提示手动 transcript fallback。
- 音频上传支持 wav/mp3，ASR 前请求权限；approve + resume 后 transcript 自动填入诊断输入框。
- ASR 失败或用户拒绝后，可手动粘贴 transcript 并调用 `/api/audio/transcript/manual` 保存。
- Trace id 可在会话页打开轻量 Trace Events 面板，用于查看 Harness 执行链路。

## API 兼容路径

- 权限主路径：`/api/permission/*`
- 权限兼容路径：`/api/permissions/*`
- 知识检索：`GET /api/tools/search-knowledge` 或 `POST /api/tools/search-knowledge`
- 报告导出兼容路径：`GET /api/reports/export`

## PowerShell 手动验收

```powershell
cd D:\项目\OfferPilot\lite-offerpilot\apps\api
python -m pytest tests
```

```powershell
cd D:\项目\OfferPilot\lite-offerpilot\apps\agent-ts
npm.cmd run build
npm.cmd test
```

```powershell
cd D:\项目\OfferPilot\lite-offerpilot\apps\web
npm.cmd run build
```

Docker Compose 验证需要本机安装 Docker Desktop：

```powershell
cd D:\项目\OfferPilot\lite-offerpilot
docker compose config
docker compose build
```

## 目录结构

```
lite-offerpilot/
├── apps/
│   ├── api/             FastAPI 后端
│   ├── agent-ts/        pi-mono Agent 服务
│   └── web/             Next.js 前端
├── harness/
│   ├── rules/           Agent 行为规则
│   └── skills/          诊断 Skills
├── knowledge/
│   └── selected/        精选知识库
├── docs/                架构与设计文档
├── docker-compose.yml
├── .env.example
└── README.md
```
