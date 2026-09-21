# 记忆与上下文 - Memory 和 Context Window 如何设计？

## Q：Agent 面试诊断系统如何设计 Memory 和上下文注入？

> 来源：OfferPilot Lite 项目经验

**新手答**："把历史聊天都放进 prompt，模型自然能记住。"

**高手答**：

Memory 不能等同于把全部历史塞进 Context Window。OfferPilot Lite 把 Memory 分为可审批保存的长期记忆和当前 Session 的短期消息。诊断完成后，LLM 抽取 weakness、strength、diagnosis_summary 等候选记忆，但保存必须经过 high-risk Permission。下一次构建上下文时，Context Builder 只注入摘要化的 weakness 和 strength，并限制长度，避免污染 prompt 和超过上下文预算。这样既能利用历史弱点，又能保护用户隐私。

## 考察点

- [memory-message-boundary] Memory 与 Session messages 的区别
- [memory-context-budget] Context Window 预算
- [memory-approval] Memory 保存审批
- [memory-summary-injection] 摘要化注入

## 常见缺失

- 把所有历史直接塞给模型
- 没有权限审批
- 没有限制上下文长度

## 追问

- 如果 memory 过多，如何压缩？
- 为什么 weakness 比完整报告更适合长期记忆？
