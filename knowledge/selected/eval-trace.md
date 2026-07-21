# Trace 与 Eval

## Q：Agent 项目怎么证明它真的可靠？

> 来源：OfferPilot 原项目知识库精选改写

**新手答**："多测几个问题，看回答还行。"

**高手答**：

Agent 可靠性不能只看最终回答，还要看执行过程是否符合预期。Trace 和 Eval 是两个互补系统：

- **Trace**：记录一次 run 发生了什么。
- **Eval**：批量验证多种输入下是否稳定通过。

## Trace 应记录什么？

关键事件包括：

- request_received
- pre_input
- skill_selected
- knowledge_retrieved
- pre_tool
- permission_required
- permission_decision
- tool_result
- post_tool
- checkpoint_saved
- output_check
- final_response
- error

Trace 的价值是 debug。当用户说"为什么这次没保存记忆"，可以查看是否触发 permission_required、是否 approve、是否 execute。

## Eval 应覆盖什么？

不要只测评分高低，还要测 Harness 能力：

- high risk 工具必须触发 permission。
- 重复工具调用必须被拦截。
- 输出缺失评分必须 fallback。
- ASR 未批准前不能调用外部服务。
- Memory 保存后能注入下一次 context。

## 面试表达

Trace + Eval 是从 Demo 到工程项目的分界线。Demo 只展示一次成功路径，工程项目要能回归验证关键链路。
