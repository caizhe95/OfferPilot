# 业务 AI 工程 - 如何把 AI 诊断功能做成可落地系统？

## Q：一个 AI 面试诊断功能如何从 Demo 做到可部署可落地？

> 来源：OfferPilot Lite 项目经验

**新手答**："把模型 API 接到网页上，能返回结果就可以。"

**高手答**：

可落地的 AI 功能需要清晰边界、稳定 API、权限和可观测性。OfferPilot Lite 把产品边界限定为单题对标诊断，避免开放域问答带来不可控范围。后端用 FastAPI 和 Python 手写 Agent Loop 编排 Session、Permission、Memory、Trace 和知识检索；模型服务分别配置 DeepSeek、Embedding 和 FunASR。部署时每个外部依赖都要有明确配置和失败提示。

## 考察点

- [business-product-boundary] 产品边界
- [business-api-stability] API 稳定性
- [business-provider-dependency] 外部模型依赖
- [business-observability-permission] 可观测与权限

## 常见缺失

- Demo 能跑但边界不清
- 模型失败没有错误处理
- 不能解释部署依赖

## 追问

- 如何向面试官证明项目不是 toy demo？
- 为什么要拆分 LLM、Embedding、ASR 配置？
