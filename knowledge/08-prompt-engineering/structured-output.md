# Prompt 工程 - 如何让 LLM 稳定输出结构化诊断？

## Q：面试诊断系统如何设计 Prompt，让模型稳定输出结构化结果？

> 来源：OfferPilot Lite 项目经验

**新手答**："告诉模型按 JSON 输出就行。"

**高手答**：

稳定结构化输出需要 Prompt、schema 校验和错误处理配合。OfferPilot Lite 中，内容评分、语音评分、追问生成和记忆候选都要求模型输出 JSON，并由后端 validator 检查字段是否齐全。Prompt 要明确任务边界：这是单题诊断，不是知识问答；Reference Answers 用于对标，不允许编造来源。模型输出非法时返回结构化错误，而不是静默生成假成功结果。

## 考察点

- JSON 输出约束
- Schema validator
- 任务边界
- 非法输出处理

## 常见缺失

- 只依赖 prompt，不做校验
- 输出字段缺失仍继续保存
- 没有说明失败策略

## 追问

- 为什么不要让模型自由决定字段？
- validator 失败时应该 fallback 还是报错？
