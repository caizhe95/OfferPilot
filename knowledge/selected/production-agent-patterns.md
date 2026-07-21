# 生产级 Agent 模式

## Q：一个 Agent Demo 和生产级 Agent 最大区别是什么？

> 来源：OfferPilot 原项目知识库精选改写

**新手答**："生产级 Agent 用更好的模型。"

**高手答**：

模型只是生产级 Agent 的一部分。真正的差距在工程外壳，也就是 Harness：

- 输入是否被清洗和校验。
- 工具是否有 schema、权限和审计。
- 上下文是否分层和限长。
- 记忆是否可审批、可解释。
- 输出是否检查和 fallback。
- 过程是否可 trace。
- 能力是否可 eval 回归。

## 常见生产模式

1. **Tool Contract**：工具输入输出必须结构化。
2. **Permission Boundary**：所有副作用工具必须经过权限门。
3. **Context Layering**：Rules、Skill、History、Memory、Knowledge 分层注入。
4. **Budget Guardrail**：限制 step、tool call、retrieval、output。
5. **Fallback First**：外部服务失败时提供降级路径。
6. **Trace Everything**：关键步骤可追踪。
7. **Eval Regression**：每次修改后跑关键用例。

## 单 Agent 也可以高工程化

多 Agent 不是工程复杂度的唯一来源。单 Agent 如果把 Harness 做完整，也可以体现足够的系统设计能力。

轻量版推荐取舍：

- 不做多 Agent。
- 不手写底层 agent loop，使用 pi-mono runtime。
- 把复杂度放在 FastAPI Harness：Session、Permission、Memory、Hooks、Budget、Trace、Eval。

## 面试表达

这个项目的核心竞争力不是"我调了一个 LLM"，而是"我围绕 LLM 建了一套可运行、可审计、可恢复、可回归的 Agent Harness"。
