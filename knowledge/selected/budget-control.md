# Budget Control 预算控制

## Q：为什么 Agent 需要预算控制？

> 来源：OfferPilot 原项目知识库精选改写

**新手答**："为了省 token。"

**高手答**：

预算控制不只是省钱，而是防止 Agent 进入不可控循环。Agent 可能重复检索、重复调用工具、输出过长报告，最终造成延迟、费用和用户体验问题。

预算可以分几类：

- **step budget**：一次 run 最多执行多少步骤。
- **tool budget**：最多调用多少次工具。
- **retrieval budget**：知识检索 topK 限制。
- **output budget**：最终报告字符数限制。
- **time budget**：外部服务超时限制。

## 面试诊断场景的推荐值

轻量单 Agent 可以使用保守预算：

- 最大 step：6
- 最大 tool call：3
- 知识检索 topK：5
- 输出长度：2500 中文字以内

这个预算足够完成：

```text
skill selection -> knowledge retrieval -> content score -> voice score -> followup -> final report
```

## 超预算怎么处理？

超预算不能让系统静默失败，应该返回结构化结果：

```json
{
  "error": true,
  "message": "Tool call budget exceeded",
  "tool_name": "search_knowledge"
}
```

同时写 trace event，让 eval 能发现预算是否被遵守。

## 面试表达

预算控制体现的是生产级 Agent 的边界意识。Demo 只关心能不能调用工具，工程项目要关心它会不会无限调用、会不会成本失控、会不会输出失控。
