# Harness Engineering 设计

## 概述

Harness 是 Agent 的"工程约束层"，负责控制 Agent 的行为边界、工具调用权限、输出质量和可观测性。

## 组件

### Rules

定义 Agent 的全局行为约束和领域规则。

**规则文件：**
- `global.rules.md` — 全局约束（语言、格式、安全）
- `diagnosis.rules.md` — 诊断领域规则（评分标准、报告结构）
- `audio.rules.md` — 音频诊断规则（transcript 处理、语音维度）

**加载策略：**
- Global Rules 始终加载
- 领域 Rules 按 Skill 触发动态加载

### Skills

Skills 是 AI Agent 的可迁移能力模块，遵循 skill-creator 规范。

**设计原则：**
- 每个 Skill 独立文件夹，`hyphen-case` 命名
- `SKILL.md` frontmatter 只含 `name` 和 `description`
- `description` 必须包含触发语义
- 主体保持精简，长内容放入 `references/`
- 不硬编码 FastAPI / 数据库内部实现
- 只依赖抽象工具名和输入输出契约

**Skills 列表：**
| Skill | 用途 | 触发条件 |
|---|---|---|
| interview-diagnosis | 核心诊断 | 面试题 + 候选回答 |
| answer-rewrite | 回答优化 | 优化/改写回答 |
| followup-coaching | 追问辅导 | 追问/面试官可能问什么 |
| audio-diagnosis | 音频诊断 | 音频上传 / transcript |

### Tools

Tools 是 Agent 可调用的能力，由 FastAPI 提供实现，TS Agent 注册 schema。

**风险分级：**
| 风险等级 | 行为 | 示例 |
|---|---|---|
| low | 自动允许 | `search_knowledge`, `score_answer` |
| medium | 首次确认可记住 | `transcribe_audio` |
| high | 每次确认或用户开启 | `save_memory`, `export_report` |
| critical | 默认拒绝 | (保留) |

**工具列表：**
| 工具 | 风险 | 说明 |
|---|---|---|
| search_knowledge | low | FTS5 知识检索 |
| score_answer | low | 内容维度评分 |
| analyze_voice_text | low | 语音维度评分 |
| generate_followup | low | 生成追问 |
| save_memory | high | 保存记忆 |
| transcribe_audio | medium | ASR 转写 |
| export_report | high | 导出报告 |

### Hooks

生命周期钩子，在 Agent 执行各阶段插入工程控制。

| Hook | 触发点 | 用途 |
|---|---|---|
| pre-input | 用户输入进入 Agent 前 | 输入校验、注入 Context |
| pre-tool | 工具调用前 | 参数校验、权限检查 |
| post-tool | 工具调用后 | 结果校验、日志记录 |
| post-output | Agent 输出后 | 输出校验、格式检查 |

### Budget

Agent 执行预算，防止无限循环和过度消耗。

| 预算项 | 默认值 |
|---|---|
| 最大 Agent 步数 | 6 |
| 最大工具调用 | 3 |
| 输出默认 | 2500 中文字以内 |
| 检索 top-k | 5 |

### Output Checker

输出检查器，确保 Agent 输出符合契约。

**检查项：**
- 必须包含内容维度评分
- 必须包含语音维度评分
- 必须保留原始面试题
- 不能编造知识来源
- 报告结构符合 `output-contract.md`

**Fallback：**
- 缺少评分 → 触发补评分
- 超预算 → 返回现有结果 + 标记 `partial`
- 输出检查失败 → 返回 fallback report

## FastAPI Harness Runner

本项目将复杂 Harness 治理放在 FastAPI 编排层，TS Agent 继续保持 pi-mono runtime，不回退到手写 Agent Loop。

主链路执行点：

- `/api/chat` 在保存消息前执行 `pre-input`，记录 `qa_extracted` 与 `pre_input` trace。
- Agent 工具事件进入后端后执行 `pre-tool`，校验工具白名单、必要参数、重复调用和工具预算。
- 工具结果返回后执行 `post-tool`，统一错误结构、截断过长结果并补齐 source。
- 最终报告保存前执行 `post-output` 和 Output Checker，失败时生成 fallback report。
- BudgetTracker 默认约束最大步骤 6、最大工具调用 3、输出 2500 字符、检索 top 5，并把预算状态写入 trace/checkpoint。

Trace 中应能看到：

```text
pre_input
skill_selected
knowledge_retrieved
pre_tool
tool_result
post_tool
permission_required
output_check
checkpoint_saved
final_response
```

Eval 中包含诊断质量用例和 Harness 工程用例，用于回归验证权限、重复工具拦截、输出检查和 memory 注入。
