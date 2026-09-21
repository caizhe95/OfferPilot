# OfferPilot v2

状态：已归档，不定义当前行为。

## 目标

v2 将早期功能重构为可持久化、可取消、可审批和可重放的运行时，并完成前后端主要交互闭环。

## 主要演进

- 引入统一 Run Kernel、幂等键、终态事件和 SSE 重放。
- 将 Coach、Diagnosis、Audio 与 Export 统一为持久化运行。
- 使用稳定考点 ID 保存诊断证据、追问、Session Summary 和 Profile Growth。
- 建立 FTS5、Embedding 与 RRF 混合检索。
- 完成签名 Profile Cookie、可信 Origin、Admin Key 和脱敏日志边界。
- 完成 Session 历史、审批、音频、报告、成长页和响应式前端。
- 增加 Provider 重试、首 Token、Token Usage、阶段耗时和故障回归。

## 归档说明

v2 曾维护多份架构图、计划、进度、缺陷修复和专项性能文档。这些文档包含不同阶段的 Schema、路径数量和测试结果，后期还混入了 v3/v4 迁移记录，继续保留会产生相互矛盾的“当前事实”。它们已压缩为本摘要，具体过程可从 Git 历史查看。

当前实现请阅读 [v4](../v4/README.md)。
