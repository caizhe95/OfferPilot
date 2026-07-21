# 知识库设计

## 定位

OfferPilot Lite 的知识库是**诊断流程内部的参考答案检索与对标依据**，不是用户可访问的搜索问答产品。

- `/api/tools/search-knowledge` 是 **internal tool API**，仅供后端诊断链路使用
- 前端不提供知识库搜索入口
- 用户不能直接对知识库提问"Agent 是什么""RAG 怎么实现"

## 题库型 Markdown 格式

每道面试题一个 `.md` 文件，格式如下：

```markdown
# [分类] - [题目标题]

## Q：[面试问题]

> 来源：自建 / 整理 / 项目经验 / 参考主题

**新手答**："..."

**高手答**：

...

## 考察点

- ...

## 常见缺失

- ...

## 追问

- ...
```

### 字段映射

| 字段 | 数据库列 | 说明 |
|------|---------|------|
| `# 标题` | `title` | 题目标题 |
| `## Q` | `question` | 面试问题文本 |
| `> 来源` | — | 题目来源说明，写入 content |
| `**新手答**` | `novice_answer` | 初级回答示例 |
| `**高手答**` | `expert_answer` | 专家回答（对标基准） |
| `## 考察点` | `exam_points` (JSON array) | 考察要点列表 |
| `## 常见缺失` | `common_gaps` (JSON array) | 常见不足与缺失 |
| `## 追问` | `followups` (JSON array) | 可能的追问 |
| 目录名 | `category` | 例如 `02-tool-management` |

`coaching-methodology` 目录下的文件设 `kind=coaching_doc`，作为辅导方法论补充。

## 纳入目录

### 第一优先级（13 个目录）

```text
01-architecture-design       架构选型
02-tool-management           工具管理
03-fault-tolerance           容错与鲁棒性
04-memory-context            记忆与上下文
05-eval-and-vision           评估与全局观
07-engineering-pitfalls      工程化踩坑
08-prompt-engineering        Prompt 工程
09-rag-retrieval             RAG 与检索
13-project-deep-dive         简历项目拷打
15-agent-concepts            Agent 概念
10-training-and-data         训练与模型
11-ai-code-testing           AI 代码测试
12-business-ai-engineering   业务 AI 工程
coaching-methodology/        辅导方法论（2 个文件）
```

### 排除目录

```text
06-multi-agent-collab        (当前不做多 Agent)
14-company-preferences       (不做公司偏好)
resume-rewriting.md           (不做简历)
job-hunting-strategy.md       (不做求职策略)
salary-negotiation.md         (不做薪资)
ats-scoring-rubric.md         (不做 ATS)
anti-ai-detection.md          (不做反检测)
```

## 导入器

### Markdown 解析

后端 `app/knowledge/knowledge_parser.py` 负责解析上述格式。

### JSON 导入

支持外部 JSON 数组导入：

```json
[
  {
    "id": "unique-id",
    "title": "题目标题",
    "category": "architecture-design",
    "content": "完整内容",
    "question": "面试问题",
    "expertAnswer": "专家答案",
    "noviceAnswer": "新手答案",
    "tags": ["agent"]
  }
]
```

### Reload

```powershell
curl -X POST http://localhost:8000/api/tools/knowledge/reload
```

返回：

```json
{
  "status": "ok",
  "imported": 15,
  "embedded": 14,
  "reused": 0,
  "embedding_unavailable": false,
  "errors": []
}
```

Reload 会清空并重建 `knowledge`、`knowledge_fts`、`knowledge_embeddings` 三张表。

Embedding 向量生成使用 `content_hash` 去重——同一文档内容不变时不重复生成。

## 检索设计

### FTS5 + Embedding 双通道

```
面试题 question
  ├─→ FTS5 关键词检索 (top 10)
  │     查 knowledge_fts 虚拟表
  │     过滤 kind=interview_qa
  │
  ├─→ Embedding 语义检索 (top 10)
  │     对 question 生成 query embedding
  │     从 knowledge_embeddings 加载当前模型向量
  │     Python 内存 cosine similarity
  │
  └─→ RRF 合并去重
        rrf_score = 1/(60+fts_rank) + 1/(60+vector_rank)
        按 knowledge_id 去重
        返回 top 5
```

### Embedding 配置

独立于文本模型配置：

```env
OFFERPILOT_EMBEDDING_PROVIDER=openai-compatible
OFFERPILOT_EMBEDDING_API_KEY=
OFFERPILOT_EMBEDDING_BASE_URL=https://your-embedding-gateway.example/v1
OFFERPILOT_EMBEDDING_MODEL=
OFFERPILOT_EMBEDDING_TIMEOUT_SECONDS=30
OFFERPILOT_REQUIRE_EMBEDDING=true
```

- 正式环境中 embedding 是必需依赖；服务未配置、调用失败或当前模型尚未 reload 向量时会阻断诊断。
- reload 以 `(embedding_model, content_hash)` 复用不变内容的旧 `vector_json`；`embedded` 仅统计新生成数，`reused` 统计复用数。

### RRF 排序

使用 Reciprocal Rank Fusion 避免直接混合异构分数：

```text
rrf_score = 1 / (60 + fts_rank) + 1 / (60 + vector_rank)
```

RRF 仅用于排序，不作为"置信度"展示给用户。

### 结果过滤

- 优先返回 `kind=interview_qa`
- 如数量不足，补少量 `kind=coaching_doc`
- 不把候选人回答文本拼进 FTS5 查询（避免噪声）

## 内容策略

高手答内容适配 OfferPilot Lite 的实现边界：

- 单 Agent、Python 手写 Agent Loop、FastAPI
- Permission / Session / Memory / Trace
- FTS5 + Embedding 双通道
- OpenAI-compatible Text / Embedding、MiMo ASR
- 不写多 Agent、模拟面试、开放域问答作为本项目能力
