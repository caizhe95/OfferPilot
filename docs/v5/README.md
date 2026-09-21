# OfferPilot v5 规划

状态：Stage 1、Stage 2 已完成；Stage 3、Stage 4 待实施。完成四个阶段后冻结秋招作品集版本。

## 目标

将现有 v4 收敛为一个可靠、可恢复、可观察的单 Agent 技术面试复盘系统。核心演示路径为：

```text
整场录音
-> 分块转写与断点恢复
-> 可编辑问答草稿
-> 人工确认
-> 单题诊断
-> Ability State
-> Run 成本与性能指标
```

## Stage 1：现有质量收口

- 修复全部 `mypy` 错误，不通过删除检查、放宽类型或忽略异常绕过。
- 固化跨 Profile 隔离矩阵，覆盖 Session、消息、Summary、Follow-up、Report、Run、RunEvent/SSE、取消、Approval、Audio 和 Coach State。
- 为 Session、Profile、Run 及其子资源补数据库组合一致性约束。
- 当前 SQLite 演示数据允许删除重建，不开发旧数据迁移。
- 同步源码、README、v4 当前事实和 v5 实施状态。

完成判据：后端测试、`mypy`、`compileall`、前端构建、浏览器 E2E、隔离矩阵和 `git diff --check` 全部通过。

## Stage 2：Run 成本与性能观测

实施状态：已完成。Provider 价格审查见 [provider-pricing.md](provider-pricing.md)。

每次 LLM、Embedding、ASR 和 Tool 执行保存一条脱敏调用明细，并按 Run 聚合：

- Provider、模型、操作、尝试次数、状态和错误类别
- 输入、输出、总 Token 与 Reasoning Token
- 音频时长、首 Token、Provider/Tool/Run 耗时
- LLM、Tool、重试成功和失败次数
- 价格版本、价格快照、币种和 `Decimal` 预估费用

Provider 未返回 Usage 或没有可靠价格时保存 `unknown`，不得用字符数伪装精确 Token，也不得伪造费用。幂等重放不能重复计量。

前端只扩展现有“任务记录”：展示 Run 汇总并允许展开逐次调用，不开发独立监控大屏。离线测试强校验聚合、幂等、重试和金额精度；真实 Provider 只记录基线，不设置受网络波动影响的硬阈值。

## Stage 3：Memory Harness

长期上下文分为三层：

1. `Session History`：当前 Session 的消息、摘要、追问和报告。
2. `Profile Memory`：用户明确要求长期保存的目标、背景事实、训练偏好和客观约束。
3. `Ability State`：由已完成正式诊断按稳定 `exam_point_id` 确定性聚合的能力状态。

Profile Memory 必须经过审批，保留来源 Session、Run、Report 和 Approval。同一分类与键的新事实取代旧事实；旧版本标记为 `superseded`。用户删除记忆时真正移除内容，只保留不含值的脱敏操作记录。

Ability State 只使用正式诊断，不使用 Coach 闲聊判断。它展示最近状态、尝试次数、覆盖率、趋势和来源报告，不生成缺乏依据的统一 0-100 分；删除来源 Session 或报告后必须可重建。

上下文优先使用当前 Session，再按当前任务有界选择少量已批准 Memory 和 Ability State；其他 Session 原文不得注入。Coach 新增 `get_ability_state`，Agent Tools 从 5 个增加到 6 个。

## Stage 4：最小录音复盘闭环

- 支持 MP3、M4A、WAV，最长 60 分钟、最大 200MB。
- 使用 FFmpeg/FFprobe 校验、标准化为单声道并切成约 5 分钟分块。
- 使用 `interview_import` Run 取代单次 `audio_transcription` 流程。
- 一次审批授权该导入任务的全部 ASR 分块，不逐块弹窗。
- 持久化分块清单、已完成分块、转写结果和当前阶段。
- 服务重启后从首个未完成分块继续；其他不可安全恢复的运行仍按现有规则进入 `interrupted`。
- 相邻转写分块组成有界、重叠窗口，由 DeepSeek 输出结构化问答候选并记录全部 Token、耗时和费用。
- 草稿保留顺序和来源分块，允许编辑、合并、拆分、删除和确认；不承诺专业说话人分离或逐字时间戳。
- 只有已确认的单道问答可以创建 Diagnosis Run，不自动批量评分。
- 生成草稿后 `interview_import` Run 即完成，人工编辑不长期占用活动 Run。
- 成功、取消或过期后删除原始音频和临时分块；失败文件最多保留 24 小时。完整转写、文本分块、草稿和报告保留到 Session 删除。

## Session 恢复

Profile Bootstrap 后读取活动 Session。访问首页时自动进入最近更新的活动 Session，并恢复消息、Run、审批、导入进度和 SSE；新建 Session 仍由明确按钮触发。

## 黄金路径

```text
建立匿名 Profile
-> 创建 Session
-> 上传整场录音并审批
-> 分块转写
-> API 重启并从检查点续跑
-> 生成并人工修正问答草稿
-> 对确认题目执行正式诊断
-> 更新 Ability State
-> 查看 Run Token、Tool、费用与耗时
```

Memory 审批保存作为独立短路径验证，避免主流程过长。

## 非目标与后续版本

v5 保持匿名签名 Profile Cookie、SQLite、单 API 实例和单 Worker。不引入 Redis、消息队列、微服务、多 Agent、独立监控平台、公开部署、域名、说话人分离、TTS 或自动批量诊断。

本地用户名密码和全量 MySQL 8.4 迁移属于后续版本：只有 v5 四个阶段全部验收后才重新评估，不为它们提前增加空接口、双数据库或兼容层。

## 外部验收条件

- MiMo ASR 保持现有 Provider；当前 HTTP 402 必须在额度恢复后重新执行真实录音探针。
- Docker 构建、健康检查、API 重启和 Volume 持久化需要可用的 Docker Engine。
- Provider 价格需在实施时核对来源与生效时间；所有金额明确标注为预估。
- Mock 测试不能替代真实 Provider 和 Compose 证据。
