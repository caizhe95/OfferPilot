# Context、Memory 与 RAG 协同

## Q：Context、Memory、Knowledge 三者怎么分工？

> 来源：OfferPilot 原项目知识库精选改写

**新手答**："都是放进 prompt 里的上下文。"

**高手答**：

三者都进入 prompt，但语义不同：

- **Context** 是最终组装结果。
- **Memory** 是和用户长期相关的信息。
- **Knowledge** 是外部知识库检索结果。

推荐上下文层级：

```text
Rules
Skill
Recent Messages
Memory Summary
Retrieved Knowledge
Current Input
```

## 为什么要分层？

不分层会导致 prompt 越写越乱。分层后可以明确优先级：

1. Rules 和 Skill 是行为约束。
2. Current Input 是本轮任务。
3. Recent Messages 保持对话连续。
4. Memory 提供用户长期画像。
5. Knowledge 提供可参考内容。

## 长度控制

上下文窗口有限，必须做预算：

- recent messages 只取最近 N 条。
- memory summary 控制在约 800 中文字。
- knowledge result 控制 topK 和单条长度。
- 超长时优先截断 knowledge 和 memory。

## 面试表达

Context Builder 是 Agent Harness 的核心模块。它把静态规则、动态历史、长期记忆和检索知识合成一个可控 prompt，而不是把所有东西无脑塞给模型。
