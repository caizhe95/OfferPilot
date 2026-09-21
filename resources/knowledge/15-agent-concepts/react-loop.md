# Agent 概念 - ReAct Loop 和手写 Agent Loop 有什么关系？

## Q：ReAct Loop 在 Agent 项目里如何理解？为什么 OfferPilot Lite 选择 Python 手写 Agent Loop？

> 来源：OfferPilot Lite 项目经验

**新手答**："ReAct 就是让模型一边思考一边调用工具。"

**高手答**：

ReAct Loop 描述的是模型在推理、行动和观察之间迭代的模式。OfferPilot Lite 的产品边界是单题诊断，所以选择 Python 手写 Agent Loop：用固定诊断工具计划执行检索、评分、语音分析、追问和输出校验，而不是引入通用 runtime。项目把工程控制放在 FastAPI Harness 层，例如 pre_tool、PermissionGate、Budget 和 Trace。这样既保留 Agent 的工具调用能力，又让调试和部署保持在一个 Python 后端内。

## 考察点

- [react-core-concept] ReAct 基本概念
- [react-runtime-boundary] Runtime 和业务编排的边界
- [react-handwritten-loop] 为什么当前选择手写 loop
- [react-harness-constraint] Harness 对工具调用的约束

## 常见缺失

- 把 ReAct 当成 prompt 技巧
- 没有说明 loop 的边界
- 没有说明后端 Harness 如何介入

## 追问

- 手写 loop 和后端 pre_tool 的边界是什么？
- 如果工具重复调用，应该在哪层拦截？
