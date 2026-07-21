# Harness Engineering 设计

Harness 是固定单题诊断链路的工程约束层，不是开放式 Agent 规划器。

## 固定执行顺序

```text
pre_input
-> search_knowledge
-> FTS5 + Embedding + RRF
-> diagnose_interview（唯一 LLM 调用）
-> output_check
-> report / memory permission / checkpoint
-> final_response
```

Python Loop 决定上述顺序；模型不自主选择工具。Tool Registry 仅用于声明受控能力、参数校验、权限映射和回归测试。

## Rules 与 Tools

- `harness/rules/global.rules.md`：语言、范围、来源与安全约束。
- `harness/rules/diagnosis.rules.md`：评分 rubric、考点证据和固定报告结构。
- `harness/rules/audio.rules.md`：MiMo ASR、人工 transcript 与基于文本的表达评分边界。

可用工具为 `search_knowledge`、`diagnose_interview`、`save_memory`、`transcribe_audio`、`export_report`。低风险检索和诊断自动执行；音频转写与记忆/导出通过 PermissionGate 审批。

## 校验与可观测性

- `pre_input` 校验题目与回答。
- `pre_tool` 校验工具白名单、参数、重复调用与预算。
- `post_tool` 截断和规范化工具结果。
- `post_output` 校验固定报告章节、内容/表达维度、考点状态和来源。
- Trace 必须记录 `knowledge_retrieved`、`vector_retrieved`、`rrf_fused`、`diagnosis_evaluated`、`output_check`、`final_response`。

表达评分只基于 ASR 转写文本及程序化文本特征，不判断音色、发音、真实语速或音高。
