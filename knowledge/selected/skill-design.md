# Skill 设计规范

## Q：Tools 和 Skills 有什么区别？

> 来源：OfferPilot 原项目知识库精选改写

**新手答**："Skill 就是高级一点的 tool。"

**高手答**：

Tool 是原子能力，Skill 是任务级工作流说明。Tool 解决"怎么执行一个动作"，Skill 解决"面对某类用户意图时应该如何组织工具和输出"。

例如：

- `search_knowledge` 是 tool。
- `score_answer` 是 tool。
- `analyze_voice_text` 是 tool。
- `interview-diagnosis` 是 skill，它规定了诊断流程、评分维度、输出格式和参考材料。

## Skill 文件规范

一个可迁移的 Skill 应该保持轻量：

- 独立 hyphen-case 文件夹。
- `SKILL.md` 只放 name、description、触发条件和简短 workflow。
- 复杂 rubric、输出格式、示例放到 `references/`。
- 不硬编码项目内部路径。
- 不把所有业务逻辑塞进 Skill。

## 为什么不只靠 Prompt？

Prompt 是一次性指令，Skill 是可复用能力包。它可以被 loader 校验、被 intent matcher 选择、被上下文 builder 注入。

## 面试表达

Skill 的价值是让 Agent 能力模块化。相比把所有规则写进一个巨大 system prompt，Skill 更轻、更可迁移，也更容易测试。
