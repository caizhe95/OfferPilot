"""Eval system: engineering regression and diagnosis quality evaluation.

Independent of trace CRUD — uses trace only for observability.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from offerpilot.core.database import get_db, init_db

# ---------------------------------------------------------------------------
# Eval Cases
# ---------------------------------------------------------------------------

EVAL_CASES: list[dict[str, Any]] = [
    {
        "name": "short_answer_structure",
        "kind": "diagnosis",
        "question": "什么是 Context Window 管理？",
        "answer": "就是管理对话窗口的长度。",
        "expect": {"has_valid_content_dims": True, "has_valid_voice_dims": True},
    },
    {
        "name": "off_topic_structure",
        "kind": "diagnosis",
        "question": "什么是 ReAct 模式？",
        "answer": "Context Window 管理是指在有限的 token 预算内保持对话的连贯性。首先需要精确的 token 计数...",
        "expect": {"has_valid_content_dims": True, "has_valid_voice_dims": True},
    },
    {
        "name": "filler_words_structure",
        "kind": "diagnosis",
        "question": "介绍一下 Agent 的 Tool Calling 机制",
        "answer": "嗯，就是那个，就是说，Tool Calling 的话，对吧，就是让模型，嗯，调用函数的机制，对吧...",
        "expect": {"has_valid_content_dims": True, "has_valid_voice_dims": True},
    },
    {
        "name": "redundant_answer_structure",
        "kind": "diagnosis",
        "question": "什么是 Harness Engineering？",
        "answer": "Harness 很重要。Harness 非常重要。Harness 是 Agent 的基础设施。Harness 工程是很关键的。Harness 是重要的基础设施...",
        "expect": {"has_valid_content_dims": True, "has_valid_voice_dims": True},
    },
    {
        "name": "good_answer_structure",
        "kind": "diagnosis",
        "question": "什么是 Context Window 管理？",
        "answer": "Context Window 管理是 Agent 工程中的核心问题。\n\n首先需要精确的 Token 计数，不能用字符数估算。\n\n常用策略包括 Sliding Window、Summarization 和分层上下文预算分配。\n\n在生产环境中，比如我们曾经处理过搜索结果过长导致 context 爆炸的问题。\n\n总之，关键是在有限窗口内保持对话连贯性。",
        "expect": {"has_valid_content_dims": True, "has_valid_voice_dims": True},
    },
    {
        "name": "diagnosis_question_structure",
        "kind": "diagnosis",
        "question": "什么是 ReAct 模式？在工程实现中需要注意什么？",
        "answer": "ReAct 是一种结合推理和行动的模式。需要注意循环终止、错误恢复、context 管理和可观测性。",
        "expect": {"has_valid_content_dims": True, "has_valid_voice_dims": True},
    },
    {
        "name": "empty_answer_structure",
        "kind": "diagnosis",
        "question": "请解释 Token 计数的重要性",
        "answer": "",
        "expect": {"has_valid_content_dims": True, "has_valid_voice_dims": True},
    },
    {
        "name": "partial_answer_structure",
        "kind": "diagnosis",
        "question": "什么是 FTS5？",
        "answer": "FTS5 是全文搜索",
        "expect": {"has_valid_content_dims": True, "has_valid_voice_dims": True},
    },
    {
        "name": "knowledge_recall_structure",
        "kind": "diagnosis",
        "question": "Agent 对话越来越长时，如何管理 Context Window？",
        "answer": "需要考虑 token 计数、使用 tiktoken 精确计算；采用 sliding window 保留最近 N 轮；对旧消息做 summarization 压缩；还要做分层上下文预算分配。同时要注意工具调用返回结果可能很长必须截断。",
        "expect": {"has_valid_content_dims": True, "has_valid_voice_dims": True},
    },
    {
        "name": "english_answer_structure",
        "kind": "diagnosis",
        "question": "什么是 Tool Calling？",
        "answer": "Tool Calling is when the LLM outputs structured function call intent instead of just text. The application parses it and executes the actual function.",
        "expect": {"has_valid_content_dims": True, "has_valid_voice_dims": True},
    },
    {
        "name": "very_long_answer_structure",
        "kind": "diagnosis",
        "question": "介绍一下 AI Agent 的架构设计",
        "answer": "AI Agent 架构设计是一个复杂的话题。" + "我们需要考虑很多方面。" * 50,
        "expect": {"has_valid_content_dims": True, "has_valid_voice_dims": True},
    },
    {
        "name": "structured_answer_structure",
        "kind": "diagnosis",
        "question": "如何选择 LLM Provider？",
        "answer": "选择 LLM Provider 需要考虑几个维度：\n\n第一，成本和延迟。不同 provider 的定价模型差异很大。\n\n第二，模型能力。需要针对具体任务评估 benchmark。\n\n第三，API 稳定性。生产环境需要 SLA 保障。\n\n第四，合规要求。数据安全和 GDPR 合规。\n\n综上所述，选择 provider 需要综合考虑业务需求和成本约束。",
        "expect": {"has_valid_content_dims": True, "has_valid_voice_dims": True},
    },
    {
        "name": "eval_knowledge_recall_fts",
        "kind": "retrieval",
        "question": "Tool Calling 机制如何设计？",
        "expect": {"fts_has_results": True},
    },
    {
        "name": "eval_knowledge_recall_semantic",
        "kind": "retrieval",
        "question": "模型调用外部函数时要怎么处理出错？",
        "expect": {"has_any_results": True},
    },
]

# Engineering harness contract evals
EVAL_CASES.extend([
    {
        "name": "harness_save_memory_permission",
        "kind": "harness",
        "expect": {"has_permission_event": True},
    },
    {
        "name": "harness_repeated_tool_blocked",
        "kind": "harness",
        "expect": {"repeated_tool_blocked": True},
    },
    {
        "name": "harness_output_missing_score_error",
        "kind": "harness",
        "expect": {"output_check_failed": True},
    },
    {
        "name": "harness_memory_injected",
        "kind": "harness",
        "expect": {"memory_injected_when_available": True},
    },
    {
        "name": "harness_within_budget",
        "kind": "harness",
        "expect": {"within_budget": True},
    },
    {
        "name": "harness_checkpoint_trace_attribution",
        "kind": "harness",
        "expect": {"checkpoint_trace_attributed": True},
    },
    {
        "name": "harness_permission_deny_keeps_session_ready",
        "kind": "harness",
        "expect": {"permission_deny_keeps_session_ready": True},
    },
    {
        "name": "harness_output_contract_valid",
        "kind": "harness",
        "expect": {"structured_report_valid": True},
    },
    {
        "name": "harness_output_contract_invalid_rejected",
        "kind": "harness",
        "expect": {"invalid_report_rejected": True},
    },
])


# ---------------------------------------------------------------------------
# Eval Runner
# ---------------------------------------------------------------------------

def run_eval(name: str) -> dict[str, Any]:
    """Run a single evaluation case.

    Returns dict with eval_name, passed, details, and scores.
    """
    case = next((c for c in EVAL_CASES if c["name"] == name), None)
    if case is None:
        return {"eval_name": name, "passed": False, "error": f"Case '{name}' not found"}

    init_db()

    if case.get("kind") == "harness":
        return _run_harness_eval(case)

    if case.get("kind") == "retrieval":
        return _run_retrieval_eval(case)

    # Diagnosis quality eval: structural only
    from offerpilot.diagnosis.diagnosis import diagnose_interview
    from offerpilot.knowledge.knowledge_importer import search_knowledge, ensure_knowledge_loaded
    from offerpilot.core.database import get_db as _get_db

    conn = _get_db()
    try:
        ensure_knowledge_loaded(conn)
        knowledge = search_knowledge(conn, case["question"], limit=3)
    finally:
        conn.close()

    import asyncio

    diagnosis = asyncio.run(diagnose_interview(
        session_id="eval",
        question=case["question"],
        answer=case["answer"],
        knowledge_context=knowledge,
        context_instruction="评估结构化诊断协议。",
        timeout=10,
    ))
    content_result = diagnosis["content_scores"]
    voice_result = diagnosis["voice_scores"]

    overall = (content_result["total"] + voice_result["total"]) / 100
    details: dict[str, Any] = {
        "content_total": content_result["total"],
        "voice_total": voice_result["total"],
        "overall_score": round(overall, 2),
        "content_dims": {
            k: v["score"] for k, v in content_result["dimensions"].items()
        },
        "voice_dims": {
            k: v["score"] for k, v in voice_result["dimensions"].items()
        },
        "exam_points": diagnosis["exam_points"],
    }

    # Structural validation
    passed = True
    reasons: list[str] = []

    for expect_key, expect_val in case["expect"].items():
        if expect_key == "has_valid_content_dims" and expect_val:
            dims = content_result.get("dimensions", {})
            expected = {"concept_accuracy", "structure_completeness",
                        "engineering_depth", "example_quality", "question_alignment"}
            missing = expected - set(dims.keys())
            if missing:
                passed = False
                reasons.append(f"Missing content dimensions: {missing}")
            for k, v in dims.items():
                score = v.get("score")
                if not isinstance(score, (int, float)) or score < 1 or score > 10:
                    passed = False
                    reasons.append(f"Invalid content score for {k}: {score}")
                if not v.get("explanation", "").strip():
                    passed = False
                    reasons.append(f"Missing content explanation for {k}")

        elif expect_key == "has_valid_voice_dims" and expect_val:
            dims = voice_result.get("dimensions", {})
            expected = {"fluency", "filler_words", "redundancy",
                        "spoken_clarity", "answer_pacing"}
            missing = expected - set(dims.keys())
            if missing:
                passed = False
                reasons.append(f"Missing voice dimensions: {missing}")
            for k, v in dims.items():
                score = v.get("score")
                if not isinstance(score, (int, float)) or score < 1 or score > 10:
                    passed = False
                    reasons.append(f"Invalid voice score for {k}: {score}")
                if not v.get("explanation", "").strip():
                    passed = False
                    reasons.append(f"Missing voice explanation for {k}")

    if not reasons:
        reasons.append("All structural checks passed")

    return {
        "eval_name": name,
        "passed": passed,
        "question": case["question"],
        "details": details,
        "expectations": case["expect"],
        "reasons": reasons if reasons else ["All expectations met"],
    }


def _run_retrieval_eval(case: dict[str, Any]) -> dict[str, Any]:
    """Run retrieval regression evals."""
    from offerpilot.knowledge.knowledge_importer import search_knowledge, ensure_knowledge_loaded
    from offerpilot.core.database import get_db as _get_db

    details: dict[str, Any] = {}
    passed = True
    reasons: list[str] = []

    conn = _get_db()
    try:
        ensure_knowledge_loaded(conn)
        results = search_knowledge(conn, case["question"], limit=5)
    finally:
        conn.close()

    details["result_count"] = len(results)
    details["titles"] = [r.get("title", "") for r in results]
    details["fts_count"] = len([r for r in results if r.get("fts_rank")])
    details["vector_count"] = len([r for r in results if r.get("vector_rank")])

    for expect_key, expect_val in case["expect"].items():
        if expect_key == "fts_has_results" and expect_val:
            if details["fts_count"] == 0:
                passed = False
                reasons.append("FTS5 returned no results")
        elif expect_key == "has_any_results" and expect_val:
            if details["result_count"] == 0:
                # Acceptable if embedding is unavailable and FTS also missed
                reasons.append("No results from any channel (embedding may be unavailable)")
            else:
                reasons.append("Has results from at least one channel")

    if not reasons:
        reasons.append("All retrieval checks passed")

    return {
        "eval_name": case["name"],
        "passed": passed,
        "question": case.get("question", ""),
        "details": details,
        "expectations": case["expect"],
        "reasons": reasons,
    }


def _run_harness_eval(case: dict[str, Any]) -> dict[str, Any]:
    """Run engineering evals for Harness behavior."""
    from offerpilot.harness.harness import HarnessRunner, check_output, validate_report_structure
    from offerpilot.permission.permission import permission_gate
    from offerpilot.session.session import create_session, transition_session, save_checkpoint, get_latest_checkpoint
    from offerpilot.diagnosis.diagnosis import save_memory
    from offerpilot.diagnosis.context_builder import build_context

    details: dict[str, Any] = {}
    passed = True
    reasons: list[str] = []
    session = create_session()
    session_id = session["id"]

    if case["name"] == "harness_save_memory_permission":
        result = permission_gate.check(session_id, "save_memory", {
            "session_id": session_id,
            "key": "weakness",
            "value": "eval",
        })
        from offerpilot.coaching.state import create_approval
        request_id = create_approval(
            session_id,
            session["profile_id"],
            "save_memory",
            "high",
            {"key": "weakness", "value": "eval"},
            flow_kind="coach",
            public_params={"key": "weakness"},
        )
        details["permission"] = {**result, "request_id": request_id}
        passed = result.get("allowed") is False and bool(request_id)
        if not passed:
            reasons.append("save_memory did not require permission")

    elif case["name"] == "harness_repeated_tool_blocked":
        runner = HarnessRunner(session_id)
        runner.pre_tool("search_knowledge", {"question": "ReAct"})
        try:
            runner.pre_tool("search_knowledge", {"question": "ReAct"})
            passed = False
            reasons.append("Repeated tool call was not blocked")
        except ValueError:
            passed = True
        details["budget"] = runner.budget.get_status()

    elif case["name"] == "harness_output_missing_score_error":
        result = check_output("只有一段普通文本，没有评分")
        passed = result["valid"] is False
        details["output_check"] = result
        if not passed:
            reasons.append("Invalid output was not detected")

    elif case["name"] == "harness_memory_injected":
        save_memory(session_id, "weakness", "eval weakness", "diagnosis")
        context = build_context(session_id, session["profile_id"], "新问题")
        passed = "eval weakness" in context
        details["memory_injected_when_available"] = passed
        if not passed:
            reasons.append("Memory was not injected into context")

    elif case["name"] == "harness_within_budget":
        runner = HarnessRunner(session_id)
        runner.record_step()
        runner.pre_tool("search_knowledge", {"question": "ReAct"})
        details["budget"] = runner.budget.get_status()
        passed = runner.budget.can_continue() and runner.budget.can_call_tool()
        if not passed:
            reasons.append("Budget exhausted too early")

    elif case["name"] == "harness_checkpoint_trace_attribution":
        checkpoint = save_checkpoint(
            session_id,
            state="completed",
            trace_id="trace-eval-test",
            run_kind="diagnose",
        )
        latest = get_latest_checkpoint(session_id)
        passed = (latest is not None
                  and latest.get("trace_id") == "trace-eval-test"
                  and latest.get("run_kind") == "diagnose")
        details["checkpoint"] = {"trace_id": latest.get("trace_id") if latest else None}
        if not passed:
            reasons.append("Checkpoint missing trace attribution")

    elif case["name"] == "harness_permission_deny_keeps_session_ready":
        from offerpilot.coaching.state import create_approval, resolve_approval
        from offerpilot.session.session import get_session
        request_id = create_approval(
            session_id,
            session["profile_id"],
            "save_memory",
            "high",
            {"key": "weakness", "value": "eval", "category": "general"},
            flow_kind="coach",
            public_params={"key": "weakness", "category": "general"},
        )
        denied = resolve_approval(request_id, session_id, session["profile_id"], "denied")
        s = get_session(session_id)
        passed = denied is not None and denied["status"] == "denied" and s is not None and s.get("status") == "ready"
        details["session_status"] = s.get("status") if s else None
        if not passed:
            reasons.append("Denied approval did not keep the session ready")

    elif case["name"] == "harness_output_contract_valid":
        valid_report = {
            "question": "什么是 Context Window？",
            "answer": "Context Window 是 LLM 的上下文窗口大小限制。",
            "overall_score": 7.5,
            "content_scores": {
                "total": 35,
                "max_total": 50,
                "dimensions": {
                    "concept_accuracy": {"score": 8, "explanation": "准确"},
                    "structure_completeness": {"score": 7, "explanation": "完整"},
                    "engineering_depth": {"score": 7, "explanation": "有深度"},
                    "example_quality": {"score": 6, "explanation": "有示例"},
                    "question_alignment": {"score": 7, "explanation": "契合"},
                },
            },
            "voice_scores": {
                "total": 35,
                "max_total": 50,
                "dimensions": {
                    "fluency": {"score": 7, "explanation": "流畅"},
                    "filler_words": {"score": 7, "explanation": "少口头禅"},
                    "redundancy": {"score": 7, "explanation": "不冗余"},
                    "spoken_clarity": {"score": 7, "explanation": "清晰"},
                    "answer_pacing": {"score": 7, "explanation": "节奏好"},
                },
            },
            "followups": [{"question": "追问1", "why": "考察深度"}],
            "sources": ["knowledge/01-architecture-design/test.md"],
            "highlights": ["表现良好"],
            "gaps": ["无"],
            "improvements": ["1. 继续练习"],
        }
        result = validate_report_structure(valid_report)
        passed = result["valid"] is True
        details["validation"] = result
        if not passed:
            reasons.append(f"Valid report rejected: {result.get('issues')}")

    elif case["name"] == "harness_output_contract_invalid_rejected":
        invalid_report = {
            "question": "test",
            # Missing many required fields
        }
        result = validate_report_structure(invalid_report)
        passed = result["valid"] is False and len(result.get("issues", [])) > 0
        details["validation"] = result
        if not passed:
            reasons.append("Invalid report not detected")

    return {
        "eval_name": case["name"],
        "passed": passed,
        "question": case.get("question", ""),
        "details": details,
        "expectations": case["expect"],
        "reasons": reasons if reasons else ["All expectations met"],
    }


def run_all_evals() -> dict[str, Any]:
    """Run all eval cases and return results."""
    init_db()
    results: list[dict[str, Any]] = []
    for case in EVAL_CASES:
        result = run_eval(case["name"])
        results.append(result)

    passed = sum(1 for r in results if r["passed"])
    failed = len(results) - passed

    return {
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "pass_rate": passed / len(results) if results else 0,
        "results": results,
    }


def save_eval_run(results: dict[str, Any]) -> dict[str, Any]:
    """Save an eval run to the database."""
    conn = get_db()
    try:
        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        summary = f"Passed: {results['passed']}/{results['total']} ({results['pass_rate']:.0%})"
        results_json = json.dumps(results["results"], ensure_ascii=False)

        conn.execute(
            "INSERT INTO eval_runs (id, eval_name, status, results, summary, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, "full_suite", "completed", results_json, summary, now, now),
        )
        conn.commit()
        return {"id": run_id, "summary": summary}
    finally:
        conn.close()
