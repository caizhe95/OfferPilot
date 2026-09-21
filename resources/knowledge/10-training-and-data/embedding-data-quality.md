# 训练与模型 - Embedding 数据质量为什么重要？

## Q：题库型 RAG 中，Embedding 的数据质量会如何影响诊断？

> 来源：OfferPilot Lite 项目经验

**新手答**："Embedding 模型好就行，数据多一点更好。"

**高手答**：

Embedding 检索质量不仅取决于模型，也取决于入库文本。OfferPilot Lite 的向量文本应该由 title、question、expert_answer、exam_points、common_gaps 和 tags 组成，而不是把所有杂乱内容直接拼进去。题库答案必须和项目真实边界一致，否则检索会召回不支持的能力，比如多 Agent 或开放域问答，导致诊断建议跑偏。数据质量优先于数量。

## 考察点

- [embedding-text-construction] Embedding text 构造
- [embedding-boundary-consistency] 数据边界一致性
- [embedding-noise-recall] 噪声对召回的影响
- [embedding-quality-first] 质量优先于数量

## 常见缺失

- 以为题库越多越好
- 向量化内容没有清洗
- 不区分当前项目支持和不支持的能力

## 追问

- 为什么不直接复制所有题库原文？
- 如何判断一条知识是否应该入库？
