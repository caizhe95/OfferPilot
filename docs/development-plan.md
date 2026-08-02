# OfferPilot Lite 开发计划

## Phase 0：清理、骨架与文档

- 清理误创建的空 lite-offerpilot
- 创建正式目录结构
- 编写 README、.env.example、架构文档、Harness 文档、Permission/Session 文档
- 明确不做多 Agent、模拟面试、TTS、JD/简历、向量库第一版
- 建立 UTF-8 无 BOM 和 PowerShell 开发规范

**通过标准：** 骨架和文档完整。中文无乱码。未开始业务代码实现。

## Phase 1：FastAPI 与 SQLite 基础

- 初始化 FastAPI
- 实现配置、数据库连接、schema 初始化
- 建表：sessions、messages、memories、knowledge、knowledge_fts、traces、trace_events、audit_log、checkpoints、progress_events、diagnosis_reports、eval_runs
- 实现 /health 和统一错误响应

**通过标准：** /health 返回 ok。数据库重复初始化不报错。所有表存在。pytest 通过。

## Phase 2：Knowledge FTS5

- 从原项目精选知识 Markdown
- 实现 Markdown parser
- 写入 SQLite 和 FTS5
- 实现 /api/tools/search-knowledge
- 支持 query、dimension、limit、source、score

**通过标准：** ReAct、Tool Calling、Context Window、Harness 可召回。空 query 被拒绝。limit 超限被限制。pytest 通过。

## Phase 3：Session / Progress / Checkpoint

- 实现 session 创建、消息保存、状态流转
- 实现 progress event
- 实现 checkpoint
- 实现最近 N 条消息窗口
- 实现 session 查询接口

**通过标准：** 合法状态流转通过。非法状态流转拒绝。checkpoint 可保存和读取。最近消息窗口截断正确。

## Phase 4：Permission / Approval / Audit

- 实现 PermissionGate
- 实现工具风险分级
- 实现 approval / deny
- 实现 waiting_approval
- 实现 audit_log
- 实现拒绝后返回可继续处理的工具结果

**通过标准：** low 自动允许。medium/high 触发确认。critical 拒绝。approve 后恢复 running。deny 后记录 audit。pytest 通过。

## Phase 5：Python 手写 Agent Loop

- 初始化 `src/api/offerpilot/agent`
- 实现 Python Tool Registry
- 接入现有知识检索、评分、语音分析、追问和 memory permission
- `/api/coach` 是唯一产品入口；Coach Loop 只使用原生 tool_calls，正式评分由显式 `mode="diagnosis"` 的确定性 Workflow 执行。
- 输出标准 SSE 事件流

**通过标准：** Agent 可调用 search_knowledge。工具失败返回结构化错误。流式事件包含 tool_call、tool_result、text_delta、done。pytest 通过。

## Phase 6：固定诊断规则收敛

- 评分 rubric、考点证据约束和报告结构收敛到 `harness/rules/`。
- 不实现 Skills loader、意图匹配或模型自主工具选择。
- Python Loop 固定执行检索、诊断、校验与审批流程。

**通过标准：** 规则可审阅，正式链路没有动态 Skill 路由。

## Phase 7：Rules / Hooks / Budget / Output Checker

- 实现 Rules loader
- 编写 global、diagnosis、audio rules
- 实现 pre-input、pre-tool、post-tool、post-output hooks
- 实现预算控制
- 实现 output checker 和结构化失败返回

**通过标准：** 重复工具调用被拦截。超预算终止。缺少评分返回结构化错误。输出必须包含内容维度和语音维度。

## Phase 8：诊断业务工具

- 实现一次结构化 `diagnose_interview` 调用，返回考点证据、内容/表达评分、建议、追问和记忆候选
- 实现 save_memory
- 实现 report 保存
- 调整 Markdown 报告模板

**通过标准：** 短回答识别内容不足。跑题回答降低 question_alignment。口头禅多降低 voice score。冗余回答降低 redundancy。优质回答得分更高。报告结构完整。

## Phase 9：Context / Memory

- 实现 memory store / summary / extraction
- 实现 context builder
- 注入固定 Rules、同 Profile 的已审批 Memory、Recent Messages、Knowledge
- 实现上下文长度控制

**通过标准：** 第一次诊断保存 weakness。第二次诊断读取 weakness。save_memory 需要权限确认。context 超长可压缩或截断。

## Phase 10：Trace / Evals

- 完成 trace event 全链路记录
- 实现 /api/traces/{trace_id}
- 编写至少 12 条 eval case
- 实现 eval runner
- 支持 eval 返回结构化结果

**通过标准：** trace 能记录完整链路。eval runner 可运行。失败原因清晰。

## Phase 11：Web UI

- 实现会话列表、聊天页、SSE 流式读取
- 展示 process step、tool call、permission card、trace id
- Markdown 渲染报告
- 支持复制和下载 Markdown
- 预留音频上传入口

**通过标准：** 文本诊断端到端可用。permission approve/deny 可用。刷新后恢复历史。前端构建通过。

## Phase 12：真实音频上传与 ASR

- 实现音频上传（wav/mp3）
- 实现 transcribe_audio
- ASR 调用前触发 Permission
- transcript 保存
- 进入 MiMo ASR 转写与文本表达诊断流程
- ASR 失败时允许用户手动粘贴 transcript 作为产品兜底

**通过标准：** 支持格式上传成功。非支持格式拒绝。ASR 前触发确认。deny 后不调用外部服务。approve 后执行 ASR。transcript 诊断完整。

## Phase 13：部署与项目包装

- 写 Dockerfile 和 docker-compose
- 配置 SQLite volume
- 写 PowerShell 启动命令
- 写部署文档
- 写架构图、Harness 文档、Permission/Session 文档
- 写简历描述和面试讲解稿
- 补充截图

**通过标准：** docker compose up --build 可启动。三服务互通。文本诊断和音频诊断可跑。eval 可运行。UTF-8 检查通过。

## 后端 Harness 闭环优化状态

本轮补齐重点放在 FastAPI 后端与 Agent Harness 闭环，前端只保持最小兼容。

- Permission Event 已标准化，`save_memory`、`export_report`、`transcribe_audio` 等 medium/high 风险工具统一返回 `permission_required`。
- Session 与 Permission 已联动，高风险工具触发后进入 `waiting_approval`，approve 或 deny 都有持久化决策；Coach 收到拒绝结果后继续生成答复，不把 Session 标记为失败。
- Memory 保存改为审批闭环，诊断阶段只生成 candidates，不直接写入；approve + resume 后才保存，并可在下一次 context 中注入。
- Hooks / Budget 已接入 `chat_api`、`diagnose_api` 与工具执行路径，覆盖 `pre_input`、`pre_tool`、`post_tool`、`post_output`、预算记录与输出检查。
- 音频 ASR 已改为上传后先保存临时文件、再请求转写权限；批准前不调用 ASR，拒绝后清理临时文件。
- Trace / Eval 已覆盖权限、Hook、Budget、Memory 注入等关键工程链路。
- 配置兼容已增强，测试不再依赖手动设置系统级 `DEBUG=false`。
- API 已统一路径，权限使用 `/api/permission/*`。

**本轮验收标准：** API 全量测试、Agent build/test、Web build 通过；中文文件 UTF-8 扫描无乱码；Docker 若本机可用则额外验证 compose。

## 前端 Phase 11/12 补齐状态

本轮补齐重点放在 Web UI 消费后端 Harness 能力，让总计划书中的前端端到端要求闭环。

- 会话页已支持刷新恢复历史消息、session 状态、progress、latest checkpoint 和 trace id。
- 诊断流式过程中持续展示 process step、tool call、tool result、permission card 和最终 Markdown 报告。
- Permission card 已从单纯 approve 改为 `approve -> resume`，确保 `save_memory`、`transcribe_audio`、`export_report` 执行到位。
- deny 流程会刷新 session 状态，并在 ASR 场景提示手动 transcript 兜底。
- 音频上传已支持 `approval_required -> approve/resume -> transcript 填入输入框`，拒绝或失败时可手动保存 transcript。
- Trace id 可打开轻量 Trace Events 面板，便于查看后端 Harness 执行链路。
- Progress 不再只在 loading 时显示，诊断完成、刷新、等待授权状态下都可查看。

**Phase 11 状态：** 会话列表、聊天页、SSE、process step、tool call、permission card、trace id、Markdown 报告、复制/下载、刷新恢复已补齐。

**Phase 12 状态：** 前端音频上传、ASR 权限确认、approve/resume 转写、deny/ASR 失败手动 transcript 兜底已补齐。

**Phase 13 状态：** Dockerfile 和 docker-compose 配置存在；Docker 需要本机安装 Docker Desktop 后再执行 compose config/build 验证。
