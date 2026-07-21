# ASR 与音频诊断

## Q：音频上传为什么要接入权限流程？

> 来源：OfferPilot 原项目知识库精选改写

**新手答**："因为调用 ASR 要花钱。"

**高手答**：

ASR 权限不只是成本问题，更涉及隐私和外部服务调用。音频文件通常包含真实声音、个人经历和面试内容，必须在调用外部转写服务前让用户明确确认。

推荐流程：

```text
上传音频 -> 校验格式和大小 -> 保存临时文件 -> transcribe_audio permission_required -> approve/resume -> 调用 ASR -> 保存 transcript
```

注意：不要把音频 bytes 存进权限对象。权限 params 只需要：

- 临时文件路径
- 安全文件名
- content_type
- size

## Fallback 设计

ASR 失败或用户拒绝时，系统仍应允许手动 transcript：

```text
deny/asr_failed -> manual transcript -> save_transcript_to_session -> 文本诊断
```

这样系统不会因为外部服务失败而完全不可用。

## 语音维度怎么评分？

轻量版可以先做 transcript-based voice scoring：

- filler_words：嗯、呃、就是、那个、然后等口头禅。
- redundancy：重复表达和绕圈。
- fluency：句子是否顺畅。
- spoken_clarity：口语表达是否清楚。
- answer_pacing：回答是否过短或过长。

## 面试表达

这个设计展示的是"真实音频能力 + 隐私权限 + 失败降级"。即使不做复杂声学特征，也已经具备可落地的音频诊断闭环。
