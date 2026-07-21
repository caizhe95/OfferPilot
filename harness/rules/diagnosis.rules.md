# 固定诊断规则

## 评分与证据
- 必须评分五个内容维度：concept_accuracy、structure_completeness、engineering_depth、example_quality、question_alignment。
- 必须评分五个表达维度：fluency、filler_words、redundancy、spoken_clarity、answer_pacing；每项为 1-10 整数并附简短说明。
- covered 与 partial 考点必须引用候选人原回答中的连续文字作为 evidence；missing 的 evidence 必须为空。
- 不得根据未提供的内容补全候选人的能力、经历或实现细节。
- 回答不足 100 字时，应明确体现结构完整性和工程深度受限；答非所问时，应降低问题契合度。

## 对标范围
- Reference Answers、考察点和常见缺失只用于诊断对标，不用于直接回答知识问题。
- 保留原始题目，不以抽取的关键词替换题目。
- 未命中参考题时，只依据已提供的题目、回答和规则判断，不得虚构来源。

## 报告契约
- 最终报告必须包含：参考答案对标、用户已覆盖、用户缺失、内容维度评分、语音维度评分、改进建议、可能追问、知识来源。
- 用户已覆盖只来自 covered 考点；用户缺失只来自 partial 和 missing 考点。
- 改进建议和追问必须服务于当前题目的缺失考点。
