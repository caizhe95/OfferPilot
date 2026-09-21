# 后端代码导航

后端按业务域组织。先看 `main.py`，再看目标业务目录的 `api.py`；需要理解执行过程时看 `service.py`、`workflow.py` 或 `loop.py`；需要理解数据库时看对应的 `repository.py`。

| 目录 | 作用 |
|---|---|
| `core` | 配置、安全、错误、日志、超时 |
| `database` | SQLite Schema、连接和初始化 |
| `profiles` | 匿名 Profile、Memory、Growth、Reset |
| `sessions` | Session、消息、Summary、Follow-up |
| `runs` | Run 状态机、事件、SSE、恢复、操作日志 |
| `approvals` | 风险策略和审批 |
| `coach` | 普通练习和工具调用 |
| `diagnosis` | 正式诊断和报告 |
| `audio` | 上传和语音转写 |
| `knowledge` | 题库解析、索引和检索 |
| `llm` | Provider、Chat、结构化 JSON、Embedding |
| `evaluations` | 即时回归 Eval |

不要在已经删除的旧目录 `agent`、`coaching`、`harness`、`permission`、`runtime`、`session`、`trace` 中新增代码。
# Backend Layout

`api.py` files expose HTTP routes. `service.py`, `lifecycle.py`, and `workflow.py` coordinate business operations. `repository.py` files own database persistence. `policy.py` contains rules; adapters such as `audio/storage.py` isolate external files and providers.

Domain directories are `sessions`, `profiles`, `approvals`, `runs`, `audio`, `knowledge`, and `diagnosis`. Shared infrastructure is under `core`, `database`, `llm`, `coach`, and `evaluations`.
