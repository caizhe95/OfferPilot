# 架构选型 - 单 Agent 诊断系统如何设计？

## Q：单 Agent 面试诊断系统应该如何做架构选型？

> 来源：OfferPilot Lite 项目经验

**新手答**："用一个大模型接收用户输入，然后直接生成诊断报告。"

**高手答**：

在 OfferPilot Lite 中，单 Agent 架构不是裸调 LLM，而是由 FastAPI 承载业务编排和 Python 手写 Agent Loop，Harness 负责输入、工具、预算、权限和输出边界。主链路是面试题和候选回答进入后端，先做输入校验，再检索题库型 RAG，随后把参考高手答、新手答、考察点和常见缺失注入上下文，最后由 DeepSeek 完成内容评分、追问和报告生成。单 Agent 的优势是链路短、易部署、易解释；复杂度通过 Harness、Permission、Trace、Memory 分层控制，而不是引入多 Agent 协作。

## 考察点

- [arch-agent-tradeoffs] 是否能解释单 Agent 和多 Agent 的取舍
- [arch-component-boundary] 是否能说明 FastAPI、Python Agent Loop、Harness 的职责边界
- [arch-end-to-end] 是否能讲清端到端链路
- [arch-no-bare-model] 是否能说明为什么不是裸调模型

## 常见缺失

- 只说用了大模型，没有讲工程编排
- 没有说明权限、Session、Trace 的作用
- 没有说明为什么当前不做多 Agent

## 追问

- 如果后续要支持多轮面试，你会如何扩展？
- Python Agent Loop 和 FastAPI 业务模块的边界为什么这样划分？
