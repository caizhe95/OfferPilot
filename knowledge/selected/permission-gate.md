# Permission Gate 权限门控

## Q：Agent 工具为什么要做权限分级？

> 来源：OfferPilot 原项目知识库精选改写

**新手答**："危险工具执行前让用户点确认。"

**高手答**：

Agent 工具调用不是普通 API 调用，因为调用决策可能来自模型。只要模型能选择工具，就必须在模型和真实副作用之间加一层 Permission Gate。

风险分级可以这样设计：

1. **low**：只读或纯计算工具，例如知识检索、评分、生成追问。
2. **medium**：可能调用外部服务或处理隐私数据，例如音频 ASR。
3. **high**：会持久化用户信息或导出数据，例如保存记忆、导出报告。
4. **critical**：默认拒绝，例如删除数据、发邮件、支付、联网执行命令。

## 标准流程

```text
tool call -> PermissionGate.check -> permission_required -> session waiting_approval -> approve/deny -> resume/stop
```

权限事件应该是统一结构，而不是每个工具返回自己的格式。至少包含：

- `type`
- `session_id`
- `request_id`
- `tool_name`
- `risk_level`
- `params`
- `message`

## Audit 设计

权限系统必须配套审计日志：

- `request`：工具请求了权限
- `approve`：用户批准
- `deny`：用户拒绝
- `execute`：批准后工具真正执行

审计参数不应该保存完整敏感数据。音频场景中保存临时文件路径、安全文件名、content type、size 即可，不应该把音频 bytes 放进权限对象。

## 面试表达

Permission Gate 的价值是把"模型想做什么"和"系统允许做什么"分离。模型可以提出工具调用，但后端 Harness 才是最终执行者。
