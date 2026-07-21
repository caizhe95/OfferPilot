# AI 代码测试 - Agent 项目如何做回归测试？

## Q：AI Agent 项目应该如何设计自动化测试？

> 来源：OfferPilot Lite 项目经验

**新手答**："主要看模型输出是否符合预期。"

**高手答**：

Agent 项目测试不能只看最终文本。OfferPilot Lite 应该覆盖输入边界、知识库导入、FTS5/Embedding 检索、RRF 合并、诊断 JSON 结构、Permission 审批、Trace 事件和 API 返回。模型输出可以波动，因此 Eval 更适合验证结构和工程链路，而不是固定某个分数阈值。测试应能证明系统不会把开放域问题当成诊断，也不会绕过权限保存 Memory。

## 考察点

- 结构化测试
- 检索回归
- Permission 测试
- Trace/Eval 测试

## 常见缺失

- 只测 final output
- 没有测边界输入
- 没有测权限和检索链路

## 追问

- 如何测试 embedding 不可用的降级？
- 如何让 LLM 测试稳定？
