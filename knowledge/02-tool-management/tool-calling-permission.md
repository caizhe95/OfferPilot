# 工具管理 - Tool Calling 如何和权限系统结合？

## Q：Agent 项目中的 Tool Calling 如何设计，为什么要接权限审批？

> 来源：OfferPilot Lite 项目经验

**新手答**："模型决定调用哪个函数，后端执行函数并把结果返回。"

**高手答**：

Tool Calling 的关键不是函数调用本身，而是工具注册、参数 schema、风险分级、执行前校验、结果回传和审计。OfferPilot Lite 中，Python Tool Registry 注册 `search_knowledge`、`score_answer`、`save_memory` 等工具，Python Agent Loop 在 FastAPI 进程内执行这些工具。低风险工具自动执行，高风险工具如 `save_memory` 会触发 PermissionGate，用户 approve 后再 resume 执行。Trace 会记录 `tool_call`、`permission_required`、`tool_result`，这样工具调用既可控又可解释。

## 考察点

- 工具 schema 和参数校验
- 风险分级与 PermissionGate
- approve / deny / resume 闭环
- Trace 和 audit log

## 常见缺失

- 只讲函数调用，没有讲权限边界
- 没有说明工具失败和恢复
- 没有说明 audit 和 trace 的工程价值

## 追问

- save_memory 为什么是 high risk？
- 如果用户 deny 工具调用，Session 应该如何变化？
