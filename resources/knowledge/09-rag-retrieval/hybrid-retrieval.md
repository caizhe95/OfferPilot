# RAG 与检索 - 为什么使用 FTS5 和 Embedding 双通道？

## Q：题库型 RAG 为什么要同时使用 FTS5 和 Embedding？

> 来源：OfferPilot Lite 项目经验

**新手答**："用向量检索就可以找到相关内容。"

**高手答**：

FTS5 和 Embedding 解决的问题不同。FTS5 适合精确命中技术词，例如 Tool Calling、FTS5、SSE、Permission；Embedding 适合召回语义相近但字面不同的问题，例如“Agent 怎么记住用户偏好”和“Memory 写入与召回机制”。OfferPilot Lite 的题库型 RAG 先用面试题分别做 FTS5 topK 和 embedding topK，再用 RRF 合并去重，返回高手答、新手答、考察点和追问。这样既保留关键词精确性，又补足语义召回。

## 考察点

- [rag-fts-embedding] FTS5 和 Embedding 的互补关系
- [rag-rrf-fusion] RRF 合并
- [rag-structured-retrieval] 题库型 RAG 返回结构
- [rag-no-manual-glossary] 为什么不用人工词表

## 常见缺失

- 只说用了 RAG，没有说明检索策略
- 直接混合不同分数
- 把候选回答整段拿去检索导致污染

## 追问

- RRF 为什么比直接加权更稳？
- Embedding 不可用时如何降级？
