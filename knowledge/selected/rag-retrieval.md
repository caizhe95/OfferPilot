# RAG 与 FTS5 检索

## Q：为什么轻量版知识库可以用 SQLite FTS5，而不是向量库？

> 来源：OfferPilot 原项目知识库精选改写

**新手答**："FTS5 简单，向量库复杂。"

**高手答**：

技术选择要服务项目阶段。OfferPilot Lite 的知识库规模较小，主题集中在 Agent 工程面试。这个场景下 FTS5 有三个优势：

1. **部署简单**：SQLite 单文件，不依赖外部向量数据库。
2. **可解释**：命中的 source、dimension、content 都能直接展示。
3. **稳定可测**：mock 模式下检索结果可控，适合 eval。

向量库更适合大规模语义检索，但它会引入 embedding 模型、索引构建、召回评估和部署依赖。轻量版第一阶段不一定划算。

## 检索设计

知识条目建议包含：

- `title`
- `content`
- `source`
- `dimension`
- `score`

检索时要做基本清洗：

- 去掉 FTS5 特殊字符。
- 控制 query 长度。
- 对多词 query 使用 OR 提升召回。
- 控制 limit，避免上下文爆炸。

## 和 Context 的关系

RAG 不是越多越好。检索结果需要进入 context budget，与 rules、skill、history、memory 共同竞争窗口。

推荐做法：

```text
query -> FTS5 topK -> format source/title/content -> 截断长内容 -> 注入 <!-- KNOWLEDGE -->
```

## 面试表达

FTS5 是一个务实取舍：轻量、可部署、可解释。项目后续可以演进到 hybrid retrieval，但第一版先把知识驱动诊断闭环跑通。
