# Output Checker 输出检查

## Q：为什么 Agent 最终输出还要检查？

> 来源：OfferPilot 原项目知识库精选改写

**新手答**："模型一般会按 prompt 输出。"

**高手答**：

模型输出不是强类型对象，即使 prompt 写了格式要求，也可能漏字段、漏维度、输出过长或偏离业务目标。Output Checker 是 final response 之前的最后一道质量门。

面试诊断报告至少要检查：

- 是否包含内容维度评分。
- 是否包含语音维度评分。
- 是否包含改进建议。
- 是否包含参考回答。
- 是否包含追问问题。
- 是否超出长度预算。

## Fallback 设计

如果输出缺少核心结构，不应该把坏结果直接给用户。可以触发 fallback report：

```text
post_output -> output_checker invalid -> generate_fallback_report -> save report -> trace output_check
```

Fallback 不追求完美，但要保证报告结构完整、可读、可解释。

## 和 Eval 的关系

Output Checker 是运行时质量门，Eval 是离线回归。两者应该共享相同的关键约束，例如必须包含内容维度和语音维度。

## 面试表达

Output Checker 体现的是"LLM 输出也要验收"。这比单纯依赖 prompt 更工程化，能降低模型偶发失控对用户体验的影响。
