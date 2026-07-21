# Permission / Session 设计

## Session 状态机

```
created ──> running ──> completed
              │    │
              │    └──> failed
              │
              └──> waiting_approval ──> running
                    │
                    ├──> paused
                    │
                    └──> cancelled
```

### 状态说明

| 状态 | 说明 |
|---|---|
| created | Session 已创建，等待输入 |
| running | Agent 正在执行 |
| waiting_approval | 等待用户审批工具调用 |
| paused | 用户手动暂停 |
| completed | 执行成功完成 |
| failed | 执行失败 |
| cancelled | 用户取消 |

### 状态流转规则

- `created` → `running`：用户提交输入
- `running` → `waiting_approval`：Agent 请求 medium/high 风险工具
- `waiting_approval` → `running`：用户 approve
- `waiting_approval` → `failed`：用户 deny + 无 fallback
- `running` → `completed`：Agent 成功完成
- `running` → `failed`：Agent 执行异常
- `running` → `paused`：用户暂停
- `paused` → `running`：用户恢复
- `running` / `waiting_approval` → `cancelled`：用户取消

## Progress 阶段

```
input_received
  -> qa_extracted
  -> skill_selected
  -> permission_checked
  -> knowledge_retrieved
  -> content_scored
  -> voice_scored
  -> memory_updated
  -> report_generated
  -> output_checked
  -> completed
```

每个阶段写入 `progress_events` 表，包含时间戳和可选 metadata。

## Permission 机制

### 流程

```
Agent 请求 medium/high 风险工具
    -> PermissionGate 检查风险等级
    -> 返回 permission_required
    -> Session 进入 waiting_approval
    -> Web UI 展示确认卡片（工具名、风险等级、参数预览）
    -> 用户选择 approve / deny
    -> 写入 audit_log
    -> approve: Agent 执行工具调用
    -> deny: Agent 收到拒绝，尝试 fallback
```

### 风险分级

```
low:       自动允许，不触发确认
medium:    首次确认，可记住（session 级别）
high:      每次确认，或用户在设置中显式开启后自动允许
critical:  默认拒绝，需用户手动在设置中开启
```

### audit_log 记录

- session_id
- tool_name
- risk_level
- action（request / approve / deny / execute）
- params（JSON）
- timestamp
- result（approve 后记录工具返回摘要）

## Permission 与 Session 联动

1. `running` 状态下 Agent 提出 tool call
2. PermissionGate 拦截 medium/high 工具
3. 状态流转到 `waiting_approval`
4. `audit_log` 记录 `request`
5. 用户 approve → 回到 `running`，`audit_log` 记录 `approve`
6. 用户 deny → `audit_log` 记录 `deny`，Agent 收到拒绝信号
7. Agent 可尝试替代方案（fallback）或结束执行

## Checkpoint

支持 Session 的检查点保存和恢复。

**保存时机：**
- 进入 `waiting_approval` 前
- 用户手动暂停
- 每完成一个 progress 阶段

**包含内容：**
- 当前状态
- 已完成 progress
- 最近消息
- 已检索 knowledge
- 已保存 memory keys

## 标准 Permission Event

medium/high 风险工具统一返回：

```json
{
  "type": "permission_required",
  "permission_required": true,
  "session_id": "session-id",
  "request_id": "request-id",
  "tool_name": "save_memory",
  "risk_level": "high",
  "params": {},
  "message": "Tool call requires user approval"
}
```

后端约定：

- 工具 API 返回该结构时必须写 `audit_log action=request`。
- Session 从 `running` 进入 `waiting_approval`。
- `approve` 只确认用户决策并写 `approve`。
- `resume` 消费已批准参数、执行原工具并写 `execute`。
- `deny` 写 `deny`，并将当前 session 标记为 `failed`，避免无状态继续。

## Memory 与音频权限

- `/api/diagnose` 只生成 `memory_candidates` 和 `memory_permission`，不直接写 memory。
- `save_memory` 是 high 风险，必须 approve + resume 后才落库。
- `/api/audio/upload` 会先保存临时音频，再触发 `transcribe_audio` medium 风险审批。
- 用户 approve + resume 后才调用 ASR；deny 后不会调用外部 ASR，并清理可恢复参数中的临时文件。
