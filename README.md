# OfferPilot Lite

OfferPilot 是一个受限的技术面试训练应用。它支持练习对话、单题正式诊断、音频转写、报告导出、题库检索和回归 Eval；不提供开放域问答、简历/JD 分析、多 Agent、TTS 或模拟面试。

## v4 架构

```text
Next.js Web
  -> FastAPI API
       -> Session + Run Kernel + persistent RunEvent
       -> Coach / Diagnosis / ASR / Export workers
       -> SQLite + FTS5 + Embedding + managed audio files
```

`Session` 只保存历史归属和标题。每次练习、正式诊断、音频转写、报告导出都是一个 `Run`；每个事件先写入 SQLite，再通过 SSE 推送。一个 Session 同时只允许一个活动 Run。

Profile 使用 `offerpilot_profile` HttpOnly 签名 Cookie，不引入登录账户。Docker Compose 面向单实例 SQLite 部署；公网部署时需要在 Web 前提供 TLS，并将 CORS 设置为实际 HTTPS 来源。

## 快速启动

```powershell
Copy-Item .env.example .env
& .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Set-Location src\api
& ..\..\.venv\Scripts\python.exe -m uvicorn offerpilot.main:app --host 0.0.0.0 --port 8000
```

另开 PowerShell：

```powershell
Set-Location src\web
npm.cmd install
npm.cmd run dev
```

访问 `http://localhost:3000`。Compose 部署使用：

```powershell
docker compose up --build
```

## v4 API

- `POST /api/sessions`、`GET /api/sessions`：创建和分页读取会话。
- `POST /api/sessions/{session_id}/runs`：创建 `coach`、`diagnosis` 或 `audio_transcription` Run；必须发送 `Idempotency-Key`。
- `POST /api/coach/reports/export`：校验报告归属后创建 `report_export` Run。
- `GET /api/runs/{run_id}`、`GET /api/runs/{run_id}/events`、`GET /api/runs/{run_id}/stream`：查询、重放和订阅运行事件。
- `POST /api/runs/{run_id}/cancel`：请求取消。
- `POST /api/approvals/{approval_id}/decision`：作出审批决定，服务端自动恢复或终止 Run。
- `POST /api/sessions/{session_id}/audio-uploads`：上传受管 WAV/MP3，随后创建音频转写 Run。
- `GET /api/sessions/{session_id}/summary`、`followups`、`reports`：读取结构化历史。
- `PATCH` / `DELETE /api/sessions/{session_id}`：重命名、归档或物理删除。
- `POST /api/profile/bootstrap`、`GET /api/profile/growth`、`POST /api/profile/reset`：建立匿名身份、读取成长快照或清除当前匿名身份的全部业务数据。

SSE 数据事件固定包含 `type`、`session_id`、`trace_id`、`run_id`、`sequence`、`created_at` 与 `data`。客户端按 `(trace_id, sequence)` 去重，可使用 `Last-Event-ID` 或 `after` 重放。`GET /api/coach/state?session_id=...` 用于刷新和服务重启后的 Coach 状态恢复。

旧 Coach、Trace、Progress、Checkpoint 与 Resume 路由已移除。

## 知识与诊断

知识 Markdown 的每个 `考察点` 使用 `- [stable-id] 标签` 格式。导入器会拒绝缺失、无效或重复的 ID；正式诊断将结果保存到 `diagnosis_point_results`，并以稳定考点 ID 关联跨报告成长分析。

诊断 Context 的预算为 24,000 字符，Coach 为 12,000 字符；优先级为规则、当前输入、知识证据、Session Summary、已批准 Memory、最近消息。Summary 由确定性 Reducer 构造，只提供历史背景，不能作为正式评分证据。

## 验证

```powershell
& .\.venv\Scripts\python.exe -m pytest
Push-Location src\web
npm.cmd run build
npm.cmd run test:e2e
Pop-Location
docker compose config --quiet
```

知识管理和 Eval 使用 Admin Key：`GET /api/admin/knowledge/status`、`POST /api/admin/knowledge/reindex`、`POST /api/admin/evals/run-all`。Eval 直接返回本次结果，不保存运行记录。运行和操作审计查询为 `GET /api/admin/runs`、`GET /api/admin/operation-logs`，均需 `X-OfferPilot-Admin-Key`。

当前 OpenAPI 固定为 26 条路径。`GET /api/profile`、`GET /api/capabilities/audio`、`GET /api/admin/evals/cases` 和单案例 Eval 路由均未注册。Session 详情只返回 Session 基础对象，前端并行读取消息、Run、Summary、Follow-up 和报告。medium 与 high 风险操作每次都需要新的审批。

## 数据库

数据库结构集中在 `src/api/offerpilot/database/schema.sql`。应用启动时执行幂等的建表和索引语句；不会执行版本迁移，也不会自动删除已有数据。

开发期修改表结构时，先停止服务并手动删除 `data/offerpilot.db`、`data/offerpilot.db-wal` 和 `data/offerpilot.db-shm`，再启动应用按当前 Schema 重新创建。需要保留的数据请先由开发者自行导出。

当前架构、API、数据库运维、部署和验收统一位于 [`docs/v4/README.md`](docs/v4/README.md)。下一版本的已确认优化范围位于 [`docs/v5/README.md`](docs/v5/README.md)，历史版本只保留摘要。

第一次阅读代码时，先看 [`docs/README.md`](docs/README.md) 和 [`docs/v4/README.md`](docs/v4/README.md)，再进入 `src/api/offerpilot/`、`src/web/src/lib/` 和 `src/web/src/app/`。

## 日志与部署

`OFFERPILOT_DEBUG=true` 只用于本地 HTTP 调试：Cookie 不带 `Secure` 属性，应用日志为易读文本。部署到公网时必须设置 `OFFERPILOT_DEBUG=false`、配置 HTTPS CORS 来源和至少 32 字节的 `OFFERPILOT_PROFILE_SIGNING_KEY`；应用日志会输出 JSON。`OFFERPILOT_LOG_LEVEL` 只接受 `DEBUG`、`INFO`、`WARNING`、`ERROR`，低于 WARNING 的日志写入 stdout，其余写入 stderr。

每个 HTTP 响应返回 `X-Request-ID`，可传入安全格式的同名请求头以关联网关日志。日志只记录路由、状态、耗时、Run/审批/Provider 的稳定元数据和脱敏堆栈；不会写入题目、回答、转写文本、Memory 值、工具私有参数、Cookie、密钥、音频内容、Provider 原文、服务器路径或 SQL 参数。Compose 使用 Docker `json-file` 驱动并保留最多 5 个 10MB 日志文件。
