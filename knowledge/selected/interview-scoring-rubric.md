# 面试诊断评分 Rubric

## Q：AI Agent 工程面试回答应该怎么评分？

> 来源：OfferPilot 原项目知识库精选改写

**新手答**："看回答对不对。"

**高手答**：

工程面试评分不能只看关键词，而要拆成多个维度。OfferPilot Lite 保留内容维度和语音文本维度，去掉表达维度，避免过度复杂。

## 内容维度

1. **concept_accuracy**：概念是否正确，有没有把 framework、harness、agent loop 混为一谈。
2. **structure_completeness**：回答是否有层次，是否先定义、再讲机制、最后讲 trade-off。
3. **engineering_depth**：是否能讲到生产问题，例如权限、预算、trace、fallback。
4. **example_quality**：是否有具体例子，而不是泛泛而谈。
5. **question_alignment**：是否回答了题目，没有跑题。

## 语音文本维度

1. **fluency**：句子是否连贯。
2. **filler_words**：口头禅是否过多。
3. **redundancy**：是否反复说同一件事。
4. **spoken_clarity**：是否容易听懂。
5. **answer_pacing**：回答长度是否合适。

## 典型扣分规则

- 短回答：structure_completeness 和 engineering_depth 降低。
- 跑题回答：question_alignment 降低。
- 只有概念没有工程落地：engineering_depth 降低。
- 没有例子：example_quality 降低。
- 口头禅密集：filler_words 降低。

## 面试表达

Rubric 的价值在于稳定性。没有 rubric 的诊断容易变成模型自由发挥；有 rubric 后，评分、建议、eval 都可以对齐同一套标准。
