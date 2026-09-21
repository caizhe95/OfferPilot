# Stage 2 Provider 价格审查

审查日期：2026-09-21

`run_calls` 只使用代码库中经过审查并冻结的价格快照。当前 `PRICE_SNAPSHOTS` 为空，因此所有 Provider 调用的预估费用保持 `unknown`；不按字符数估算 Token，也不进行跨币种换算。

| 调用 | 当前配置 | 审查来源 | 结论 |
| --- | --- | --- | --- |
| LLM | `deepseek-v4-flash`，DeepSeek OpenAI 兼容端点 | [DeepSeek 官方价格页](https://api-docs.deepseek.com/quick_start/pricing) | 官方页面没有与当前配置名称完全匹配、可冻结的价格，暂不登记快照。 |
| Embedding | SiliconFlow `BAAI/bge-m3` | [SiliconFlow 官方价格页](https://siliconflow.cn/pricing) | 未在仓库中冻结可核验的模型单价和生效时间，暂不登记快照。 |
| ASR | `mimo-v2.5-asr`，`https://api.xiaomimimo.com/v1` | [MiMo API 站点](https://api.xiaomimimo.com/) | 未取得可核验的公开单价和生效时间，暂不登记快照。 |

新增价格快照时必须同时提交 Provider、精确模型名、币种、输入/输出单价、来源 URL、版本和生效时间，并用离线测试验证 Decimal 计算。未满足这些字段时继续返回 `price_unknown`。
