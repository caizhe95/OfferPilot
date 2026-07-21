# Session 与 Checkpoint

## Q：为什么 Agent 应用需要 Session 状态机？

> 来源：OfferPilot 原项目知识库精选改写

**新手答**："为了保存聊天记录。"

**高手答**：

Session 不只是消息容器，而是一次 Agent 运行的状态边界。它要描述当前任务处在什么阶段，能否继续，是否等待用户授权，失败后能否恢复。

常见状态：

- `created`：会话刚创建
- `running`：正在执行
- `waiting_approval`：等待用户确认工具调用
- `paused`：用户主动暂停
- `completed`：正常结束
- `failed`：失败终态
- `cancelled`：取消终态

状态机的核心不是状态多，而是非法流转必须被拒绝。例如 completed 之后不能回到 running，否则审计和 UI 都会混乱。

## Checkpoint 的价值

Checkpoint 保存的是某个执行阶段的快照：

- 当前 state
- 已完成 progress
- 最近 messages
- 已检索 knowledge
- 使用到的 memory keys
- budget 状态

它解决两个问题：

1. **恢复**：长流程中断后可以知道卡在哪一步。
2. **解释**：前端刷新后能恢复过程视图，而不是只看到最终消息。

## Permission 联动

当 high/medium 工具触发权限时，Session 应进入 `waiting_approval`。approve 后 resume，deny 后进入 failed 或 fallback。

这个设计让 UI、后端和审计保持一致：

```text
permission_required event
  -> session.status = waiting_approval
  -> progress 写 permission_checked
  -> audit 写 request
```

## 面试表达

Session 状态机体现的是工程可靠性。没有状态机的 Agent Demo 可以跑，但一旦引入权限、音频、记忆和长流程，就必须显式建模状态。
