# 容错与鲁棒性 - LLM 和 ASR 不可用时如何处理？

## Q：面试诊断系统中 LLM 或 ASR 服务不可用时应该如何保证鲁棒性？

> 来源：OfferPilot Lite 项目经验

**新手答**："报错给用户，让用户稍后重试。"

**高手答**：

鲁棒性要区分硬失败和可降级路径。OfferPilot Lite 中，DeepSeek 文本 LLM 是评分、追问和记忆候选的必要语义判断来源，如果模型不可用，应返回结构化 503，不生成假成功报告。ASR 使用 FunASR，转写失败时不影响文本诊断，用户可以手动粘贴 transcript。Embedding 不可用时，知识检索可以降级为 FTS5-only，并在 Trace 中记录 `embedding_unavailable`。所有失败都应写 trace，避免静默吞错。

## 考察点

- [fault-required-versus-degrade] 必需模型和可降级服务的区别
- [fault-structured-errors] 结构化错误
- [fault-manual-transcript] manual transcript fallback
- [fault-run-events] RunEvent 可观测性

## 常见缺失

- 把所有失败都 fallback 成假成功
- 没有区分 LLM、ASR、Embedding 的重要性
- 没有记录失败阶段

## 追问

- 为什么 LLM 失败不应该生成假报告？
- Embedding 失败为什么可以 FTS5-only？
