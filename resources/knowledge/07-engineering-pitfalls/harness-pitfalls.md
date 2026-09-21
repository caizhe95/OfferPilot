# 工程化踩坑 - Harness Engineering 最容易踩哪些坑？

## Q：Agent Harness Engineering 的常见工程坑有哪些？

> 来源：OfferPilot Lite 项目经验

**新手答**："加一些规则和 prompt，让模型按规则输出。"

**高手答**：

Harness Engineering 不能只写 prompt。常见坑包括：输入没有结构校验，导致开放域问题进入诊断链路；工具没有风险分级，导致高风险写入绕过用户确认；预算只写配置但不接入主路径；输出检查只靠字符串匹配，容易保存假报告；Trace 只记录 final response，无法定位工具和权限问题。OfferPilot Lite 的 Harness 应该把 pre_input、pre_tool、post_tool、post_output 和 Permission、Session、Trace 连接起来。

## 考察点

- [harness-prompt-versus-code] Prompt 规则和程序约束的区别
- [harness-hook-budget-permission] Hook / Budget / Permission
- [harness-output-checker] Output checker
- [harness-run-event-integrity] RunEvent 完整性

## 常见缺失

- 把 Harness 等同于 prompt
- 工具预算没有实际执行
- 输出错误仍保存报告

## 追问

- pre_tool 应该检查哪些内容？
- output checker 失败时应该怎么处理？
