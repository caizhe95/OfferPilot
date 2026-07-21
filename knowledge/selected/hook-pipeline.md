# Hook Pipeline 治理管线

## Q：Agent Harness 里的 Hook 有什么用？

> 来源：OfferPilot 原项目知识库精选改写

**新手答**："Hook 就是在前后加一些回调。"

**高手答**：

Hook 是 Agent Harness 的治理入口。它不负责业务能力本身，而负责在关键边界上做清洗、校验、限制、修复和记录。

典型 Hook 点：

1. **pre-input**：清洗用户输入，拒绝空输入，识别 question/answer。
2. **pre-tool**：校验工具名、参数、重复调用和预算。
3. **post-tool**：统一工具错误格式，截断过长结果，补充 source。
4. **post-output**：检查最终输出结构，不合格时触发 fallback。

## 为什么不放在 Agent Prompt 里？

Prompt 约束是软约束，Hook 是硬约束。比如"最多调用 3 次工具"不能只写在 prompt 里，因为模型可能不遵守。正确做法是在 pre-tool 中检查预算，超限后直接返回结构化错误。

## 工程原则

- Hook 要轻量，不能塞太多业务逻辑。
- Hook 要可测试，输入输出要结构化。
- Hook 要在主链路中执行，不能只存在于单测。
- Hook 失败要产生可解释错误，写入 trace。

## 面试表达

Hook Pipeline 的亮点是把 Agent 的不确定行为约束在可控边界内。模型负责推理，Harness 负责治理。
