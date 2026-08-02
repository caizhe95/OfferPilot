# Permission / Session 设计

## Session 状态机

```text
ready -> running -> ready
             |
             -> waiting_approval -> running -> ready
                                  -> ready (non-Coach approved operation)
             -> failed -> ready (transient run failure)
```

Session 用于承载连续练习，不把单轮成功、取消或临时 Provider 故障作为终态。`cancelled` 只保留给明确关闭整个 Session 的管理动作；当前轮取消体现在 `coach_runs.status` 和 SSE 的 `run_complete.status`。`run_complete.status` 只能为 `completed`、`waiting_approval`、`failed` 或 `cancelled`。

`waiting_approval` 是可恢复状态：应用重启不会清理它。启动恢复仅处理超过 90 秒的 `running` 和 `cancel_requested` 运行，将其对应 Session 恢复为 `ready`。

## 统一审批记录

Coach、直接记忆接口、ASR 与报告导出都使用 `approval_requests` 表，而不是进程内 Map。审批记录带有 Profile、Session、工具、`flow_kind`、`trace_id`、私有参数、脱敏公开参数、过期时间和以下状态：

```text
pending -> approved -> executing -> executed
       -> denied
       -> expired
approved/executing -> failed
```

审批默认 24 小时过期，`pending` 与尚未执行的 `approved` 都会失效。`approved -> executing` 使用条件更新，因此重复 Resume 不能重复写记忆、调用 ASR 或导出报告。Coach 审批只能由 Coach Resume 消费，Audio 审批只能由音频 Resume 消费，Export 审批只能由报告导出 Resume 消费。

## 流程

```text
tool call
  -> risk policy
  -> approval_requests.pending
  -> SSE/API permission_required
  -> approve or deny
  -> Coach resume or one-time side-effect execution
```

- low 风险工具自动执行。
- medium 风险工具同一 Session 首次确认后可写入 `permission_grants`。
- high 风险工具每次确认。
- critical 风险工具默认拒绝。

音频仅保存服务端生成的上传标识，审批不保存或公开服务器路径。无论成功、失败、拒绝、取消或过期，音频临时文件都会删除。普通独立工具回到 `ready`；Coach 工具会将 `permission_denied` 放回同一 Agent Trace，让模型改用无需权限的路径或向用户说明，而不是让 Session 失败。

## 标准事件与审计

`permission_required` 是扁平 SSE 事件，至少包含：

```json
{
  "type": "permission_required",
  "session_id": "session-id",
  "trace_id": "trace-id",
  "sequence": 3,
  "request_id": "request-id",
  "tool_name": "save_memory",
  "risk_level": "high",
  "params": {}
}
```

`audit_log` 记录 request、approve、deny 和 execute 的工具、风险等级、参数摘要和结果摘要。日志不得记录密钥、Cookie、完整音频或完整候选回答。
