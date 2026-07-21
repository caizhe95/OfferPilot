"""Diagnosis business tools: score_answer, analyze_voice_text, generate_followup, save_memory, report saving."""

import uuid
import json
from datetime import datetime, timezone
from app.core.database import get_db
from app.llm.llm_client import structured_json_completion


CONTENT_DIMENSION_KEYS = [
    "concept_accuracy",
    "structure_completeness",
    "engineering_depth",
    "example_quality",
    "question_alignment",
]

VOICE_DIMENSION_KEYS = [
    "fluency",
    "filler_words",
    "redundancy",
    "spoken_clarity",
    "answer_pacing",
]


def _truncate_text(text: str, limit: int = 4000) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + "...(truncated)"


def _summarize_knowledge_context(knowledge_context: list[dict] | None) -> list[dict]:
    summaries = []
    for item in (knowledge_context or [])[:5]:
        summaries.append({
            "title": item.get("title", ""),
            "dimension": item.get("dimension", ""),
            "source": item.get("source", item.get("source_file", "")),
            "content": _truncate_text(str(item.get("content", "")), 500),
        })
    return summaries


def _normalize_dimension_result(raw: dict, expected_keys: list[str]) -> dict:
    dims = raw.get("dimensions")
    if not isinstance(dims, dict):
        raise ValueError("missing dimensions")

    normalized = {}
    for key in expected_keys:
        value = dims.get(key)
        if not isinstance(value, dict):
            raise ValueError(f"missing dimension: {key}")
        score = value.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError(f"invalid score for {key}")
        score_int = max(1, min(10, int(round(score))))
        explanation = str(value.get("explanation", "")).strip()
        if not explanation:
            raise ValueError(f"missing explanation for {key}")
        normalized[key] = {
            "score": score_int,
            "explanation": explanation,
        }

    result = dict(raw)
    result["dimensions"] = normalized
    result["total"] = sum(v["score"] for v in normalized.values())
    result["max_total"] = len(expected_keys) * 10
    return result


def score_answer(
    question: str,
    answer: str,
    knowledge_context: list[dict] | None = None,
) -> dict:
    """Score an interview answer on content dimensions."""
    fallback = lambda: _score_answer_heuristic(question, answer, knowledge_context)
    system_prompt = (
        "你是 OfferPilot Lite 的面试回答内容诊断器。"
        "请基于问题、候选人回答和知识库上下文，对五个内容维度分别给出 1-10 的整数分数和中文解释。"
        "不要输出置信度，不要输出额外评分层。"
        "只输出 JSON，格式为 {\"dimensions\": {维度名: {\"score\": number, \"explanation\": string}}}。"
        f"必须包含这些维度：{', '.join(CONTENT_DIMENSION_KEYS)}。"
    )
    payload = {
        "question": question,
        "answer": _truncate_text(answer, 7000),
        "knowledge_context": _summarize_knowledge_context(knowledge_context),
    }

    result = structured_json_completion(
        task_name="content_scoring",
        system_prompt=system_prompt,
        user_payload=payload,
        fallback_factory=fallback,
        validator=lambda data: _can_normalize_dimensions(data, CONTENT_DIMENSION_KEYS),
        temperature=0.1,
    )
    try:
        normalized = _normalize_dimension_result(result.data, CONTENT_DIMENSION_KEYS)
    except Exception:
        normalized = fallback()
        result.source = "fallback"
    normalized["source"] = result.source
    return normalized


def _can_normalize_dimensions(raw: dict, expected_keys: list[str]) -> bool:
    try:
        _normalize_dimension_result(raw, expected_keys)
        return True
    except Exception:
        return False


def _score_answer_heuristic(
    question: str,
    answer: str,
    knowledge_context: list[dict] | None = None,
) -> dict:
    """Deterministic fallback scoring for content dimensions."""
    scores = {}

    # concept_accuracy: based on answer length and knowledge match
    knowledge_text = " ".join(k.get("content", "") for k in (knowledge_context or []))
    concept_score = _score_concept_accuracy(answer, knowledge_text)
    scores["concept_accuracy"] = concept_score

    # structure_completeness: check for opening, body, conclusion structure
    structure_score = _score_structure(answer)
    scores["structure_completeness"] = structure_score

    # engineering_depth: check for technical terms, trade-offs, production mentions
    depth_score = _score_engineering_depth(answer)
    scores["engineering_depth"] = depth_score

    # example_quality: check for concrete examples
    example_score = _score_examples(answer)
    scores["example_quality"] = example_score

    # question_alignment: check if answer addresses the question
    alignment_score = _score_alignment(question, answer)
    scores["question_alignment"] = alignment_score

    # Calculate total
    total = sum(s["score"] for s in scores.values())
    return {
        "dimensions": scores,
        "total": total,
        "max_total": 50,
    }


def _score_concept_accuracy(answer: str, knowledge_text: str) -> dict:
    """Score concept accuracy based on answer characteristics."""
    answer_lower = answer.lower()

    score = 5  # default neutral

    # Short answer penalty
    if len(answer) < 100:
        score = 3
        return {"score": score, "explanation": "回答过短，无法充分评估概念准确性", "flag": "insufficient_content"}

    # Keyword overlap with knowledge
    tech_terms = [
        "context window", "react", "tool calling", "function calling",
        "embedding", "token", "llm", "agent", "prompt", "hallucination",
        "rag", "retrieval", "chunking", "streaming", "sse",
        "上下文窗口", "工具调用", "函数调用", "向量", "检索", "分块",
    ]
    matches = sum(1 for t in tech_terms if t in answer_lower)
    score = min(10, max(3, 5 + matches))

    return {"score": score, "explanation": f"概念准确性评分 {score}/10"}


def _score_structure(answer: str) -> dict:
    """Score structure completeness."""
    has_opening = any(kw in answer for kw in ["首先", "first", "概述", "overview"])
    has_closing = any(kw in answer for kw in ["总之", "总结", "综上", "in summary", "conclusion"])
    has_bullets = any(c in answer for c in ["\n- ", "\n1.", "\n* "])
    has_sections = answer.count("\n##") >= 1

    score = 3
    if has_opening:
        score += 2
    if has_closing:
        score += 2
    if has_bullets:
        score += 1
    if has_sections:
        score += 2
    score = min(10, score)

    return {"score": score, "explanation": f"结构完整性评分 {score}/10"}


def _score_engineering_depth(answer: str) -> dict:
    """Score engineering depth."""
    depth_keywords = [
        "trade-off", "tradeoff", "权衡", "取舍",
        "production", "生产", "生产环境",
        "latency", "延迟", "throughput", "吞吐",
        "scale", "扩展", "扩容", "并发", "concurrent",
        "monitoring", "监控", "logging", "日志",
        "fallback", "降级", "timeout", "超时",
        "cache", "缓存", "retry", "重试",
    ]
    matches = sum(1 for kw in depth_keywords if kw in answer.lower())
    score = min(10, max(1, matches * 2))
    return {"score": score, "explanation": f"工程深度评分 {score}/10 (关键词匹配 {matches} 个)"}


def _score_examples(answer: str) -> dict:
    """Score example quality."""
    example_indicators = [
        "例如", "比如", "for example", "for instance",
        "场景", "scenario", "case", "案例",
    ]
    has_example = any(ind in answer.lower() for ind in example_indicators)
    if has_example:
        # Count numbered items that might be examples
        count = answer.count("\n1.") + answer.count("\n2.") + answer.count("\n3.")
        score = min(10, 5 + count * 2)
        return {"score": score, "explanation": f"示例质量评分 {score}/10"}
    return {"score": 2, "explanation": "回答中未发现具体示例"}


def _score_alignment(question: str, answer: str) -> dict:
    """Score question-answer alignment."""
    # Extract keywords from question
    import re
    q_keywords = set(re.findall(r"[\u4e00-\u9fff\w]+", question.lower()))
    # Filter out common words
    stopwords = {"是", "的", "什么", "what", "is", "the", "a", "an", "of", "in", "to"}
    q_keywords -= stopwords

    if not q_keywords:
        return {"score": 5, "explanation": "无法提取问题关键词"}

    a_lower = answer.lower()
    matched = sum(1 for kw in q_keywords if kw in a_lower)
    ratio = matched / len(q_keywords) if q_keywords else 0
    score = min(10, int(ratio * 10) + 1)
    return {"score": score, "explanation": f"问题契合度评分 {score}/10 (关键词匹配 {matched}/{len(q_keywords)})"}


def analyze_voice_text(transcript: str) -> dict:
    """Analyze voice dimensions from transcript text."""
    features = _extract_voice_features(transcript)
    fallback = lambda: _analyze_voice_text_heuristic(transcript, features)
    system_prompt = (
        "你是 OfferPilot Lite 的语音转写文本诊断器。"
        "请基于 transcript 和程序提取的客观特征，对五个语音维度分别给出 1-10 的整数分数和中文解释。"
        "程序特征只能作为证据，最终语义判断由你完成。"
        "不要输出置信度，不要输出额外评分层。"
        "只输出 JSON，格式为 {\"dimensions\": {维度名: {\"score\": number, \"explanation\": string}}}。"
        f"必须包含这些维度：{', '.join(VOICE_DIMENSION_KEYS)}。"
    )
    payload = {
        "transcript": _truncate_text(transcript, 7000),
        "features": features,
    }

    result = structured_json_completion(
        task_name="voice_scoring",
        system_prompt=system_prompt,
        user_payload=payload,
        fallback_factory=fallback,
        validator=lambda data: _can_normalize_dimensions(data, VOICE_DIMENSION_KEYS),
        temperature=0.1,
    )
    try:
        normalized = _normalize_dimension_result(result.data, VOICE_DIMENSION_KEYS)
    except Exception:
        normalized = fallback()
        result.source = "fallback"
    normalized["note"] = "Voice scores based on transcript semantic analysis (*)"
    normalized["features"] = features
    normalized["source"] = result.source
    return normalized


def _extract_voice_features(transcript: str) -> dict:
    """Extract objective transcript features for the voice scoring prompt."""
    text = transcript or ""
    text_lower = text.lower()
    fillers_cn = ["嗯", "呃", "啊", "就是", "就是说", "那个", "这个", "对吧", "对不对", "反正", "基本上"]
    fillers_en = ["um", "uh", "like", "you know", "i mean", "actually", "basically"]
    filler_count = sum(text_lower.count(f) for f in fillers_cn + fillers_en)
    sentence_count = max(1, len([s for s in text.replace("！", "。").replace("？", "。").split("。") if s.strip()]))
    paragraph_count = len([p for p in text.split("\n\n") if p.strip()])
    return {
        "char_count": len(text),
        "sentence_count": sentence_count,
        "paragraph_count": paragraph_count,
        "filler_count": filler_count,
        "filler_per_sentence": round(filler_count / sentence_count, 2),
    }


def _analyze_voice_text_heuristic(transcript: str, features: dict | None = None) -> dict:
    """Deterministic fallback voice analysis."""
    scores = {}

    # fluency: sentence completion and transition quality
    fluency = _score_fluency(transcript)
    scores["fluency"] = fluency

    # filler_words: count Chinese and English fillers
    filler = _score_filler_words(transcript)
    scores["filler_words"] = filler

    # redundancy: detect repeated phrases
    redundancy = _score_redundancy(transcript)
    scores["redundancy"] = redundancy

    # spoken_clarity: thesis clarity and logical flow
    clarity = _score_spoken_clarity(transcript)
    scores["spoken_clarity"] = clarity

    # answer_pacing: balanced coverage
    pacing = _score_pacing(transcript)
    scores["answer_pacing"] = pacing

    total = sum(s["score"] for s in scores.values())
    return {
        "dimensions": scores,
        "total": total,
        "max_total": 50,
        "note": "Voice scores based on text transcript analysis (*)",
        "features": features or _extract_voice_features(transcript),
    }


def _score_fluency(text: str) -> dict:
    """Score fluency from text indicators."""
    sentences = [s.strip() for s in text.replace("!", ".").replace("?", ".").split(".") if s.strip()]
    if len(sentences) < 2:
        return {"score": 3, "explanation": "句子太少，无法评估流畅度"}

    avg_len = sum(len(s) for s in sentences) / len(sentences)
    # Very short sentences suggest choppiness, very long ones suggest run-ons
    score = 7
    if avg_len < 15:
        score = 4
    elif avg_len > 80:
        score = 5
    return {"score": score, "explanation": f"流畅度评分 {score}/10 (平均句长: {avg_len:.0f} 字符)"}


def _score_filler_words(text: str) -> dict:
    """Score filler word usage (inverted: fewer fillers = higher score)."""
    fillers_cn = ["嗯", "呃", "啊", "就是", "就是说", "那个", "这个", "对吧", "对不对", "反正", "基本上"]
    fillers_en = ["um", "uh", "like", "you know", "i mean", "actually", "basically"]

    count = 0
    text_lower = text.lower()
    for f in fillers_cn:
        count += text_lower.count(f)
    for f in fillers_en:
        count += text_lower.count(f)

    sentences = max(1, len(text.split("。")))
    filler_rate = count / sentences

    if filler_rate < 0.5:
        score = 9
    elif filler_rate < 1:
        score = 7
    elif filler_rate < 2:
        score = 5
    elif filler_rate < 3:
        score = 3
    else:
        score = 1

    return {"score": score, "explanation": f"口头禅控制评分 {score}/10 (检测到 {count} 个口头禅)"}


def _score_redundancy(text: str) -> dict:
    """Score redundancy."""
    # Detect repeated phrases
    import re
    words = re.findall(r"[\u4e00-\u9fff]+", text)
    if len(words) < 10:
        return {"score": 7, "explanation": "文本太短，冗余度评分默认 7/10"}

    # Check for phrase repetition (3+ word sequences)
    seen = {}
    redundancies = 0
    for i in range(len(words) - 3):
        phrase = "".join(words[i:i + 3])
        if phrase in seen and i - seen[phrase] < 50:
            redundancies += 1
        seen[phrase] = i

    rate = redundancies / max(1, len(words))
    score = max(1, min(10, 10 - int(rate * 100)))
    return {"score": score, "explanation": f"冗余度评分 {score}/10"}


def _score_spoken_clarity(text: str) -> dict:
    """Score spoken clarity."""
    has_thesis = any(kw in text for kw in ["核心", "关键", "key", "核心是", "关键是"])
    has_list = any(c in text for c in ["\n- ", "\n1.", "第一", "第二", "首先", "其次"])
    score = 5
    if has_thesis:
        score += 2
    if has_list:
        score += 3
    return {"score": min(10, score), "explanation": f"口语清晰度评分 {score}/10"}


def _score_pacing(text: str) -> dict:
    """Score answer pacing."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if len(paragraphs) < 2:
        return {"score": 4, "explanation": "段落太少，无法评估节奏"}

    lengths = [len(p) for p in paragraphs]
    avg = sum(lengths) / len(lengths)
    # Check if paragraphs have similar lengths (good pacing) or very uneven (poor)
    variance = sum((l - avg) ** 2 for l in lengths) / len(lengths)
    if variance < avg * avg * 0.5:
        score = 8
    elif variance < avg * avg:
        score = 6
    else:
        score = 4
    return {"score": min(10, score), "explanation": f"回答节奏评分 {score}/10"}


def generate_followup(
    question: str,
    answer: str,
    weaknesses: list[str] | None = None,
) -> list[dict]:
    """Generate likely follow-up questions based on weaknesses."""
    if weaknesses is None:
        weaknesses = []

    fallback = lambda: {"followups": _generate_followup_heuristic(question, answer, weaknesses)}
    system_prompt = (
        "你是 OfferPilot Lite 的面试追问生成器。"
        "请根据面试题、候选回答和薄弱点，生成最多 5 个高质量追问。"
        "每个追问必须有 question 和 why。"
        "不要输出置信度，不要输出评分。"
        "只输出 JSON：{\"followups\": [{\"question\": string, \"why\": string}]}。"
    )
    payload = {
        "question": question,
        "answer": _truncate_text(answer, 5000),
        "weaknesses": weaknesses,
    }

    result = structured_json_completion(
        task_name="followup_generation",
        system_prompt=system_prompt,
        user_payload=payload,
        fallback_factory=fallback,
        validator=lambda data: _can_normalize_followups(data),
        temperature=0.3,
    )
    try:
        return _normalize_followups(result.data)
    except Exception:
        return _generate_followup_heuristic(question, answer, weaknesses)


def _can_normalize_followups(raw: dict) -> bool:
    try:
        _normalize_followups(raw)
        return True
    except Exception:
        return False


def _normalize_followups(raw: dict) -> list[dict]:
    items = raw.get("followups")
    if not isinstance(items, list):
        raise ValueError("missing followups")
    normalized = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        why = str(item.get("why", "")).strip()
        if not question or not why or question in seen:
            continue
        normalized.append({"question": question, "why": why})
        seen.add(question)
        if len(normalized) >= 5:
            break
    if not normalized:
        raise ValueError("empty followups")
    return normalized


def _generate_followup_heuristic(
    question: str,
    answer: str,
    weaknesses: list[str] | None = None,
) -> list[dict]:
    """Deterministic fallback follow-up generation."""
    followups = []

    if weaknesses is None:
        weaknesses = []

    # Pattern-based follow-up generation
    patterns = {
        "concept_accuracy": [
            {"question": "你能具体解释一下这个概念的边界条件吗？", "why": "考察概念理解的深度"},
            {"question": "这个技术有什么常见的误区？", "why": "考察是否理解常见错误用法"},
        ],
        "structure_completeness": [
            {"question": "能否用 STAR 原则重新组织你的回答？", "why": "考察结构化表达能力"},
        ],
        "engineering_depth": [
            {"question": "这个方案在生产环境部署时遇到过什么问题？", "why": "考察工程实践经验"},
            {"question": "如果要扩展到 10 倍规模，需要做哪些改动？", "why": "考察系统设计能力"},
            {"question": "有没有什么场景是不适合用这个方案的？", "why": "考察技术评估能力"},
        ],
        "example_quality": [
            {"question": "能分享一个具体的项目案例吗？", "why": "考察实战经验"},
        ],
        "question_alignment": [
            {"question": "能否重新陈述一下你对问题的理解？", "why": "确认是否理解面试题"},
        ],
        "filler_words": [],
        "redundancy": [],
        "spoken_clarity": [],
    }

    # Pick follow-ups based on weaknesses
    used_questions = set()
    for weakness in weaknesses:
        dim = weakness.split(":")[0].strip() if ":" in weakness else weakness
        options = patterns.get(dim, [])
        for opt in options:
            if opt["question"] not in used_questions and len(followups) < 5:
                followups.append(opt)
                used_questions.add(opt["question"])

    # If no specific weaknesses, add generic follow-ups
    if not followups:
        followups = [
            {"question": "你觉得在回答这个问题时，最有信心的是哪个部分？", "why": "考察自我认知"},
            {"question": "如果重新回答一次，你会在哪些地方改进？", "why": "考察反思能力"},
            {"question": "这个问题在实际工作中最常见的应用场景是什么？", "why": "考察实践联系"},
        ]

    return followups[:5]


ALLOWED_MEMORY_KEYS = {
    "target_role",
    "weakness",
    "strength",
    "preference",
    "diagnosis_summary",
}


def extract_memory_candidates(
    session_id: str,
    content_scores: dict,
    voice_scores: dict,
    report: str = "",
) -> list[dict]:
    """Extract lightweight memory candidates from diagnosis results.

    This does not persist anything. Callers must route candidates through
    the high-risk save_memory permission flow.
    """
    fallback = lambda: _extract_memory_candidates_heuristic(session_id, content_scores, voice_scores, report)
    system_prompt = (
        "你是 OfferPilot Lite 的记忆候选提取器。"
        "请根据诊断结果判断哪些信息值得保存为长期记忆。"
        "只允许输出这些 key：target_role, weakness, strength, preference, diagnosis_summary。"
        "输出必须是 JSON，格式为 {\"candidates\": [{\"key\": string, \"value\": string, \"category\": string}]}。"
        "如果没有值得保存的信息，返回空数组。"
    )
    payload = {
        "session_id": session_id,
        "content_scores": content_scores,
        "voice_scores": voice_scores,
        "report": _truncate_text(report, 1200),
    }

    result = structured_json_completion(
        task_name="memory_extraction",
        system_prompt=system_prompt,
        user_payload=payload,
        fallback_factory=lambda: {"candidates": fallback()},
        validator=lambda data: _can_normalize_memory_candidates(data),
        temperature=0.2,
    )
    if result.source != "llm":
        return fallback()
    try:
        return _normalize_memory_candidates(session_id, result.data)
    except Exception:
        return fallback()


def _can_normalize_memory_candidates(raw: dict) -> bool:
    try:
        _normalize_memory_candidates("", raw)
        return True
    except Exception:
        return False


def _normalize_memory_candidates(session_id: str, raw: dict) -> list[dict]:
    items = raw.get("candidates")
    if not isinstance(items, list):
        raise ValueError("missing candidates")

    cleaned = []
    seen = set()
    for candidate in items:
        if not isinstance(candidate, dict):
            continue
        key = str(candidate.get("key", "")).strip()
        value = str(candidate.get("value", "")).strip()
        category = str(candidate.get("category", "general")).strip() or "general"
        if key not in ALLOWED_MEMORY_KEYS or not value:
            continue
        dedupe_key = (key, value)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        cleaned.append({
            "session_id": session_id,
            "key": key,
            "value": value[:300],
            "category": category,
            "source": "llm",
        })

    return cleaned[:6]


def _extract_memory_candidates_heuristic(
    session_id: str,
    content_scores: dict,
    voice_scores: dict,
    report: str = "",
) -> list[dict]:
    """Deterministic fallback memory extraction."""
    candidates: list[dict] = []

    for dim_name, dim_data in content_scores.get("dimensions", {}).items():
        score = dim_data.get("score", 0)
        explanation = dim_data.get("explanation", "")
        if score < 5:
            candidates.append({
                "session_id": session_id,
                "key": "weakness",
                "value": f"{dim_name}: {explanation}",
                "category": "diagnosis",
                "source": "content_scores",
            })
        elif score >= 8:
            candidates.append({
                "session_id": session_id,
                "key": "strength",
                "value": f"{dim_name}: {explanation}",
                "category": "diagnosis",
                "source": "content_scores",
            })

    for dim_name, dim_data in voice_scores.get("dimensions", {}).items():
        score = dim_data.get("score", 0)
        explanation = dim_data.get("explanation", "")
        if score < 5:
            candidates.append({
                "session_id": session_id,
                "key": "weakness",
                "value": f"{dim_name}: {explanation}",
                "category": "voice",
                "source": "voice_scores",
            })
        elif score >= 8:
            candidates.append({
                "session_id": session_id,
                "key": "strength",
                "value": f"{dim_name}: {explanation}",
                "category": "voice",
                "source": "voice_scores",
            })

    if report:
        summary = report.strip().replace("\n", " ")[:300]
        if summary:
            candidates.append({
                "session_id": session_id,
                "key": "diagnosis_summary",
                "value": summary,
                "category": "diagnosis",
                "source": "report",
            })

    cleaned = []
    seen = set()
    for candidate in candidates:
        if candidate["key"] not in ALLOWED_MEMORY_KEYS:
            continue
        dedupe_key = (candidate["key"], candidate["value"])
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        cleaned.append(candidate)
    return cleaned[:6]


def save_memory(
    session_id: str,
    key: str,
    value: str,
    category: str = "general",
) -> dict:
    """Save a memory entry for future sessions."""
    conn = get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            "INSERT INTO memories (session_id, key, value, category, created_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, key, value, category, now),
        )
        conn.commit()
        return {
            "id": cursor.lastrowid,
            "session_id": session_id,
            "key": key,
            "value": value,
            "category": category,
            "created_at": now,
        }
    finally:
        conn.close()


def get_memories(session_id: str | None = None, key: str | None = None) -> list[dict]:
    """Retrieve memories."""
    conn = get_db()
    try:
        conditions = []
        params = []
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if key:
            conditions.append("key = ?")
            params.append(key)

        where = " AND ".join(conditions) if conditions else "1=1"
        rows = conn.execute(
            f"SELECT id, session_id, key, value, category, created_at FROM memories WHERE {where} ORDER BY created_at DESC",
            params,
        ).fetchall()
        return [
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "key": row["key"],
                "value": row["value"],
                "category": row["category"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    finally:
        conn.close()


def save_diagnosis_report(
    session_id: str,
    question: str,
    answer: str,
    content_scores: dict,
    voice_scores: dict,
    overall_score: float,
    report_markdown: str,
) -> dict:
    """Save a diagnosis report."""
    conn = get_db()
    try:
        report_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO diagnosis_reports (id, session_id, question, answer, content_scores, voice_scores, overall_score, report_markdown, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                report_id,
                session_id,
                question,
                answer,
                json.dumps(content_scores, ensure_ascii=False),
                json.dumps(voice_scores, ensure_ascii=False),
                overall_score,
                report_markdown,
                now,
            ),
        )
        conn.commit()
        return {
            "id": report_id,
            "session_id": session_id,
            "overall_score": overall_score,
            "created_at": now,
        }
    finally:
        conn.close()


def get_diagnosis_report(report_id: str) -> dict | None:
    """Retrieve a diagnosis report."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM diagnosis_reports WHERE id = ?",
            (report_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "question": row["question"],
            "answer": row["answer"],
            "content_scores": json.loads(row["content_scores"]),
            "voice_scores": json.loads(row["voice_scores"]),
            "overall_score": row["overall_score"],
            "report_markdown": row["report_markdown"],
            "created_at": row["created_at"],
        }
    finally:
        conn.close()
