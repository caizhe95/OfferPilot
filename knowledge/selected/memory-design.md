# Memory 记忆设计

## Q：Agent 为什么需要 Memory？和普通对话历史有什么区别？

> 来源：OfferPilot 原项目知识库精选改写

**新手答**："Memory 就是把聊天记录保存起来，下次再读。"

**高手答**：

Memory 不是简单保存聊天记录，而是把对未来任务有复用价值的信息结构化沉淀下来。对话历史强调"刚刚发生了什么"，Memory 强调"这个用户长期有什么模式"。

在面试诊断场景中，Memory 最有价值的信息包括：

1. **弱点**：用户经常答得浅、结构乱、缺少案例、偏题。
2. **优势**：用户擅长工程化表达、案例完整、能讲清 trade-off。
3. **偏好**：用户希望回答更口语化、简洁、偏实习项目表达。
4. **目标角色**：后端、Agent 工程、LLM 应用、AI Infra。
5. **诊断摘要**：上次诊断中最关键的改进方向。

Memory 和 history 的区别：

- History 是完整消息，适合短期上下文。
- Memory 是筛选后的长期事实，适合跨轮诊断。
- History 可以被窗口截断，Memory 应该被摘要后保留。
- History 默认保存，Memory 应该经过用户确认。

## 工程实现要点

Memory 保存要有权限控制，因为它会长期影响后续回答。比较稳的流程是：

```text
诊断完成 -> 抽取 candidates -> 触发 save_memory 权限 -> approve/resume -> 写入 memories -> 下次 build_context 注入
```

Memory candidate 不应该直接落库。它只是一个待确认建议，字段保持轻量：

- `key`：weakness / strength / preference / diagnosis_summary / target_role
- `value`：可读的诊断结论
- `category`：diagnosis / voice / general
- `source`：content_scores / voice_scores / report

## 面试表达

这个设计的亮点不是"我保存了记忆"，而是"我把长期记忆纳入了 Harness 的审批和上下文预算体系"。这能体现生产意识：记忆会影响未来行为，所以必须可解释、可审计、可拒绝。
