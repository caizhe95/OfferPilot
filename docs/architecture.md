# OfferPilot Lite 架构设计

## 产品边界

OfferPilot 是受限技术面试 Coach，不是开放域 Chatbot。它只处理技术面试练习、追问、题库推荐、正式单题诊断和已批准记忆；不处理简历、JD、泛求职问题、多 Agent、TTS 或开放域问答。

系统采用混合编排：练习对话是受限 ReAct，正式评分是显式确定性 Workflow。二者共享会话、Trace、Profile 隔离、审批和 SQLite，但没有互相绕过入口。

## 服务拓扑

```text
Browser / Next.js
  -> HttpOnly signed offerpilot_profile cookie
  -> FastAPI HTTP + SSE
       -> CoachLoop (mode=coach)
          -> native OpenAI-compatible tool_calls
          -> restricted Tool Registry
       -> DiagnosisWorkflow (mode=diagnosis)
          -> FTS5 + Embedding + RRF
          -> one structured score request + output validation
       -> SQLite: sessions / traces / coach_runs / approval_requests
       -> OpenAI-compatible Provider / MiMo ASR
```

## 入口与编排

`POST /api/coach` 是唯一交互入口，并以请求体确定性分流：

| mode | 输入 | 编排 | 评分 |
|---|---|---|---|
| `coach` | `message` | 最多 4 轮、8 次工具调用、45 秒预算的受限 CoachLoop | 禁止 |
| `diagnosis` | `diagnosis.question` 与 `diagnosis.answer` | 一条固定 DiagnosisWorkflow | 允许 |

未提供 `mode` 的旧请求按 `coach` 处理。普通对话中的评分请求只返回模式引导，Coach 的工具注册表没有 `run_diagnosis`。

正式诊断直接写入用户输入、报告、来源、Trace 和 run 状态，不会先调用 Coach 模型。成功流会发送 `report_ready`，随后发送 `final_response` 和 `run_complete`。

## Provider 与运行治理

所有文本、流式 native tool calling 和 Embedding 都使用同一 `AsyncOpenAI` 适配层，SDK 强制 `max_retries=0`。适配层统一处理：

- 认证、Schema、参数、上下文错误不重试。
- 限流、网络、超时和 5xx 最多总计 3 次尝试，退避为 0.5、1 秒，并遵守 `Retry-After` 与剩余总预算。
- 单次 Provider 请求不超过 4 秒；取消信号传播至模型、Embedding、检索和诊断 Workflow。
- Coach 首次请求采用流式；仅在尚未向用户发送可见 `text_delta` 时，才可以回退为非流式重试。已经发送可见文本的流失败时绝不自动重试，避免重复输出。
- 工具调用与普通对话均有显式输出上限，避免 Provider 的默认输出预算耗尽单次请求时限；SSE 层仍额外执行 5000 字符上限。
- 记忆写入、报告保存和 ASR 等有副作用操作不做自动重试。

CoachLoop 使用有界异步队列生成 SSE。每个数据事件都带 `type`、`session_id`、`trace_id`、`sequence`；静默超过 15 秒发送 SSE 注释心跳 `: ping`。模型可见输出以 `text_delta` 发送，不记录或返回隐藏推理。

`POST /api/coach/cancel` 以 `{ session_id, trace_id }` 条件更新当前运行并设置进程内取消信号。取消或临时 Provider 失败后 Session 返回 `ready`，可重新提交。

## 持久化恢复与审批

`coach_runs` 保存当前 Session 的活动运行，`trace_id` 是该轮 run identity。启动时扫描超过 90 秒的 `running` 或 `cancel_requested` 记录，标记为 `interrupted`，关闭 Trace、写入 `run_recovered` 事件，并把 Session 恢复为 `ready`。`waiting_approval` 不参与清理，重启后可继续同一 Trace。

所有权限请求统一保存在 `approval_requests`，并携带 `flow_kind`、`trace_id` 和脱敏公开参数。状态为 `pending`、`approved`、`denied`、`executing`、`executed`、`failed` 或 `expired`。执行前的条件更新保证同一审批不能重复产生记忆写入或 ASR 调用；Coach、Audio 和 Export 只能由匹配的 Resume 入口消费。

- low：直接执行。
- medium：首次确认后可写入同一 Session 的 `permission_grants`。
- high：每次确认，包含记忆和报告导出。
- critical：策略直接拒绝。

拒绝 Coach 工具调用会把 `permission_denied` 作为工具结果返回给 Coach，让它完成本轮答复；不会把整个 Session 标记为失败。成功、失败、拒绝、取消或过期的待转写音频都会删除临时文件。

## 身份与网络边界

浏览器通过 `POST /api/profile/bootstrap` 获得 `offerpilot_profile` Cookie。Cookie 的 Profile UUID 和过期时间共同使用 HMAC-SHA256 签名，配置为 HttpOnly、`SameSite=Lax`、`Path=/`、30 天；生产环境强制 `Secure`。`GET /api/profile` 仅用于确认当前 Profile。

生产环境必须提供至少 32 字节的 `OFFERPILOT_PROFILE_SIGNING_KEY`。业务 API 只认 Cookie，永久拒绝旧 `X-OfferPilot-Profile-Id` Header。旧 LocalStorage UUID 迁移只能在 `OFFERPILOT_ALLOW_LEGACY_PROFILE_BOOTSTRAP=true` 且 Origin 可信时通过 bootstrap 签发一次 Cookie，之后前端删除旧值。

CORS 来源取自 `OFFERPILOT_CORS_ORIGINS`，默认仅 `http://localhost:3000`。配置必须是显式 HTTP(S) Origin，拒绝空值、`*`、`null`、路径、查询串和片段。所有写接口（包括匿名的 bootstrap）均要求可信 Origin；`/health` 是只读健康检查。管理接口仍要求 Admin Key。生产环境要求 HTTPS Origin 和 TLS 终端，因为 Profile Cookie 使用 `Secure`。

应用日志只记录稳定错误分类和脱敏摘要，不记录密钥、签名 Cookie、音频 Base64、服务器路径、完整候选人回答或 Provider 原始响应。

## 部署

Docker Compose 为 SQLite 提供持久化 volume，并将签名密钥、CORS 和迁移开关注入 API。`API_PORT` 和 `WEB_PORT` 控制宿主机端口；Web 以构建期 `API_BASE_URL`（默认 `http://api:8000`）生成 Next.js API rewrite。API 健康检查访问 `/health`，Web 在 API 健康后才启动。Compose 的本地默认值启用 debug HTTP；正式部署必须显式关闭 debug，使用 HTTPS CORS 白名单和 TLS 终端。正式部署前必须执行完整后端测试、前端生产构建、真实 Provider Probe 和 Docker 重启恢复验证。
