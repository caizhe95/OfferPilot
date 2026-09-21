# 评估与全局观 - Trace 和 Eval 在 Agent 项目中有什么价值？

## Q：为什么 Agent 项目需要 Trace 和 Eval？

> 来源：OfferPilot Lite 项目经验

**新手答**："Trace 用来看日志，Eval 用来测模型回答好不好。"

**高手答**：

Trace 和 Eval 是 Agent 工程化闭环。Trace 记录请求、输入校验、知识检索、工具调用、权限审批、输出检查和最终响应，方便定位失败阶段。Eval 不应该只评估主观分数，而应验证关键链路是否完整，例如是否检索到参考答案、是否触发 memory permission、是否记录 output_check、是否在 embedding 不可用时降级。这样项目的稳定性可以回归验证，而不是依赖人工体验。

## 考察点

- [eval-run-events] RunEvent 事件设计
- [eval-regression-location] Eval 的工程回归定位
- [eval-harness-loop] Harness 闭环验证
- [eval-failure-code] 失败原因可定位

## 常见缺失

- 只把 Trace 当日志
- Eval 只写主观分数阈值
- 没有验证权限和检索链路

## 追问

- Eval 如何验证 Permission 流程？
- Trace 里哪些事件最关键？
