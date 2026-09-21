# 简历项目拷打 - OfferPilot Lite 如何被深挖？

## Q：如果面试官深挖 OfferPilot Lite，你应该如何讲项目核心设计？

> 来源：OfferPilot Lite 项目经验

**新手答**："这是一个 AI 面试诊断项目，用大模型给回答打分。"

**高手答**：

可以从端到端链路讲：用户输入面试题和回答，系统先做输入边界校验，再用题库型 RAG 检索参考高手答。检索采用 FTS5 和 Embedding 双通道，RRF 合并结果。随后 Python 手写 Agent Loop 在 FastAPI 进程内按工具计划执行检索、评分、语音分析和追问，DeepSeek 完成语义判断。高风险 memory 保存必须经过 Permission，Trace 记录每一步。这体现了 Harness Engineering，而不是简单调 API。

## 考察点

- [project-main-flow] 项目主链路
- [project-rag-design] RAG 检索设计
- [project-harness-engineering] Harness Engineering
- [project-permission-events] 权限和 RunEvent

## 常见缺失

- 只讲模型打分
- 讲不清 Agent 和后端分工
- 讲不清 RAG 结果如何进入诊断

## 追问

- 这个项目和普通 Chatbot 的区别是什么？
- 为什么选择单 Agent？
