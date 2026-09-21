# OfferPilot 文档索引

文档按产品版本排列。每个版本只保留一份入口文档，避免历史计划、实施流水账和当前契约混在一起。

| 版本 | 状态 | 用途 |
|---|---|---|
| [v1](v1/README.md) | 已归档 | 初始单 Agent、Session、Permission 与固定诊断链路 |
| [v2](v2/README.md) | 已归档 | Run Kernel、RAG、审批、前端和可观测性重构 |
| [v3](v3/README.md) | 已归档 | 数据库与运行时的过渡版本，没有独立公共契约 |
| [v4](v4/README.md) | 当前实现 | 当前架构、API、数据、运维和验收事实 |
| [v5](v5/README.md) | 已规划 | 秋招作品集优化范围，尚未实现 |

阅读和维护规则：

- 代码行为以 `v4/README.md` 和当前源码为准。
- `v5/README.md` 是已确认的下一版本需求，不代表功能已经存在。
- v1-v3 只解释演进背景，不定义当前 API、Schema 或验收结果。
- 更细的历史实施过程由 Git 历史保存，不再在 `docs/` 维护重复副本。
- 后端源码位于 `src/api/offerpilot/`，前端源码位于 `src/web/src/`，运行数据位于 `data/`。
