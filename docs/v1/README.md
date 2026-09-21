# OfferPilot v1

状态：已归档，不定义当前行为。

## 目标

v1 建立了受限技术面试训练产品的最初边界：服务技术面试练习、题库检索、单题诊断、音频转写和报告，不扩展为开放域聊天、多 Agent、简历或 JD 分析平台。

## 主要设计

- Next.js Web、FastAPI API 与 SQLite 单实例部署。
- Python 手写 Agent Loop 和受控工具注册表。
- Session 承载连续练习，消息和记忆提供上下文。
- Permission Gate 为音频、记忆和导出等副作用操作提供审批。
- FTS5 知识检索和固定诊断 Workflow。
- Trace、Checkpoint 和 Progress Event 用于早期运行过程记录。

## 被后续版本取代的内容

- `/api/coach`、独立 Trace/Checkpoint/Progress 接口和旧 Session 状态机已经删除。
- 旧 `coach_runs`、`approval_requests`、`audit_log` 等表名不再存在。
- 诊断、审批、恢复和事件传输已经统一到后续 Run Kernel。

保留本摘要只为解释项目起点；当前实现请阅读 [v4](../v4/README.md)。
