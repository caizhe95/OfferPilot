"""Trace and evaluation system.

Records full execution traces for observability and runs
evaluation cases to validate diagnosis quality.
"""

import uuid
import json
from datetime import datetime, timezone
from app.core.database import get_db


# ---------------------------------------------------------------------------
# Trace System
# ---------------------------------------------------------------------------

def create_trace(session_id: str) -> dict:
    """Create a new trace for a session."""
    conn = get_db()
    try:
        trace_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO traces (id, session_id, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (trace_id, session_id, "running", now, now),
        )
        conn.commit()
        return {"id": trace_id, "session_id": session_id, "status": "running"}
    finally:
        conn.close()


def add_trace_event(
    trace_id: str,
    event_type: str,
    step_index: int | None = None,
    data: dict | None = None,
) -> dict:
    """Add an event to a trace."""
    conn = get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        data_json = json.dumps(data or {}, ensure_ascii=False)
        cursor = conn.execute(
            "INSERT INTO trace_events (trace_id, event_type, step_index, data, created_at) VALUES (?, ?, ?, ?, ?)",
            (trace_id, event_type, step_index, data_json, now),
        )
        conn.commit()
        return {
            "id": cursor.lastrowid,
            "trace_id": trace_id,
            "event_type": event_type,
            "step_index": step_index,
            "data": data or {},
            "created_at": now,
        }
    finally:
        conn.close()


def complete_trace(trace_id: str, status: str = "completed") -> None:
    """Mark a trace as completed or failed."""
    conn = get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE traces SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, trace_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_trace(trace_id: str) -> dict | None:
    """Get a trace with all its events."""
    conn = get_db()
    try:
        trace = conn.execute(
            "SELECT * FROM traces WHERE id = ?",
            (trace_id,),
        ).fetchone()
        if trace is None:
            return None

        events = conn.execute(
            "SELECT * FROM trace_events WHERE trace_id = ? ORDER BY id ASC",
            (trace_id,),
        ).fetchall()

        return {
            "id": trace["id"],
            "session_id": trace["session_id"],
            "status": trace["status"],
            "created_at": trace["created_at"],
            "updated_at": trace["updated_at"],
            "events": [
                {
                    "id": e["id"],
                    "event_type": e["event_type"],
                    "step_index": e["step_index"],
                    "data": json.loads(e["data"]),
                    "created_at": e["created_at"],
                }
                for e in events
            ],
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Eval System
# ---------------------------------------------------------------------------

EVAL_CASES = [
    {
        "name": "short_answer",
        "question": "什么是 Context Window 管理？",
        "answer": "就是管理对话窗口的长度。",
        "expect": {
            "low_score": True,
            "reason": "短回答应识别内容不足",
        },
    },
    {
        "name": "off_topic",
        "question": "什么是 ReAct 模式？",
        "answer": "Context Window 管理是指在有限的 token 预算内保持对话的连贯性。首先需要精确的 token 计数...",
        "expect": {
            "low_alignment": True,
            "reason": "跑题回答降低 question_alignment",
        },
    },
    {
        "name": "filler_words_heavy",
        "question": "介绍一下 Agent 的 Tool Calling 机制",
        "answer": "嗯，就是那个，就是说，Tool Calling 的话，对吧，就是让模型，嗯，调用函数的机制，对吧...",
        "expect": {
            "low_filler_score": True,
            "reason": "口头禅多降低 filler_words 分数",
        },
    },
    {
        "name": "redundant_answer",
        "question": "什么是 Harness Engineering？",
        "answer": "Harness 很重要。Harness 非常重要。Harness 是 Agent 的基础设施。Harness 工程是很关键的。Harness 是重要的基础设施...",
        "expect": {
            "low_redundancy_score": True,
            "reason": "冗余回答降低 redundancy 分数",
        },
    },
    {
        "name": "good_answer",
        "question": "什么是 Context Window 管理？",
        "answer": "Context Window 管理是 Agent 工程中的核心问题。\n\n首先需要精确的 Token 计数，不能用字符数估算。\n\n常用策略包括 Sliding Window、Summarization 和分层上下文预算分配。\n\n在生产环境中，比如我们曾经处理过搜索结果过长导致 context 爆炸的问题。\n\n总之，关键是在有限窗口内保持对话连贯性。",
        "expect": {
            "high_overall_score": True,
            "reason": "优质回答得分应更高",
        },
    },
    {
        "name": "diagnosis_question",
        "question": "什么是 ReAct 模式？在工程实现中需要注意什么？",
        "answer": "ReAct 是一种结合推理和行动的模式。需要注意循环终止、错误恢复、context 管理和可观测性。",
        "expect": {
            "reasonable_score": True,
            "reason": "正确回答应有合理分数",
        },
    },
    {
        "name": "empty_answer",
        "question": "请解释 Token 计数的重要性",
        "answer": "",
        "expect": {
            "low_score": True,
            "reason": "空回答无法评分",
        },
    },
    {
        "name": "partial_answer",
        "question": "什么是 FTS5？",
        "answer": "FTS5 是全文搜索",
        "expect": {
            "low_score": True,
            "reason": "极短回答结构不完整",
        },
    },
    {
        "name": "knowledge_recall",
        "question": "Agent 对话越来越长时，如何管理 Context Window？",
        "answer": "需要考虑 token 计数、使用 tiktoken 精确计算；采用 sliding window 保留最近 N 轮；对旧消息做 summarization 压缩；还要做分层上下文预算分配。同时要注意工具调用返回结果可能很长必须截断。",
        "expect": {
            "covers_expected_points": True,
            "reason": "应覆盖多个 context window 管理策略",
        },
    },
    {
        "name": "english_answer_chinese_question",
        "question": "什么是 Tool Calling？",
        "answer": "Tool Calling is when the LLM outputs structured function call intent instead of just text. The application parses it and executes the actual function.",
        "expect": {
            "reasonable_score": True,
            "reason": "英文回答中文问题仍可评分",
        },
    },
    {
        "name": "very_long_answer",
        "question": "介绍一下 AI Agent 的架构设计",
        "answer": "AI Agent 架构设计是一个复杂的话题。" + "我们需要考虑很多方面。" * 50,
        "expect": {
            "low_redundancy_score": True,
            "reason": "大量重复降低冗余度分数",
        },
    },
    {
        "name": "structured_answer",
        "question": "如何选择 LLM Provider？",
        "answer": "选择 LLM Provider 需要考虑几个维度：\n\n第一，成本和延迟。不同 provider 的定价模型差异很大。\n\n第二，模型能力。需要针对具体任务评估 benchmark。\n\n第三，API 稳定性。生产环境需要 SLA 保障。\n\n第四，合规要求。数据安全和 GDPR 合规。\n\n综上所述，选择 provider 需要综合考虑业务需求和成本约束。",
        "expect": {
            "high_structure_score": True,
            "reason": "结构化回答应有高结构完整性分数",
        },
    },
]

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
        "name": "harness_output_missing_score_fallback",
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
])


def run_eval(name: str) -> dict:
    """Run a single evaluation case.

    Returns dict with eval_name, passed, details, and scores.
    """
    case = next((c for c in EVAL_CASES if c["name"] == name), None)
    if case is None:
        return {"eval_name": name, "passed": False, "error": f"Case '{name}' not found"}

    if case.get("kind") == "harness":
        return _run_harness_eval(case)

    from app.diagnosis.diagnosis import score_answer, analyze_voice_text
    from app.knowledge.knowledge_importer import search_knowledge, ensure_knowledge_loaded
    from app.core.database import get_db as _get_db

    conn = _get_db()
    try:
        ensure_knowledge_loaded(conn)
        knowledge = search_knowledge(conn, case["question"], limit=3)
    finally:
        conn.close()

    content_result = score_answer(case["question"], case["answer"], knowledge)
    voice_result = analyze_voice_text(case["answer"])

    overall = (content_result["total"] + voice_result["total"]) / 100
    details = {
        "content_total": content_result["total"],
        "voice_total": voice_result["total"],
        "overall_score": round(overall, 2),
        "content_dims": {
            k: v["score"] for k, v in content_result["dimensions"].items()
        },
        "voice_dims": {
            k: v["score"] for k, v in voice_result["dimensions"].items()
        },
    }

    # Evaluate expectations
    passed = True
    reasons = []
    for expect_key, expect_val in case["expect"].items():
        if expect_key == "reason":
            continue
        if expect_key == "low_score" and expect_val:
            if overall > 0.4:
                passed = False
                reasons.append(f"Expected low score but got {overall:.2f}")
        elif expect_key == "low_alignment" and expect_val:
            align = content_result["dimensions"]["question_alignment"]["score"]
            if align >= 7:
                passed = False
                reasons.append(f"Expected low alignment but got {align}")
        elif expect_key == "low_filler_score" and expect_val:
            filler = voice_result["dimensions"]["filler_words"]["score"]
            if filler >= 7:
                passed = False
                reasons.append(f"Expected low filler score but got {filler}")
        elif expect_key == "low_redundancy_score" and expect_val:
            red = voice_result["dimensions"]["redundancy"]["score"]
            if red >= 8:
                passed = False
                reasons.append(f"Expected low redundancy score but got {red}")
        elif expect_key == "high_overall_score" and expect_val:
            if overall < 0.5:
                passed = False
                reasons.append(f"Expected high overall score but got {overall:.2f}")
        elif expect_key == "reasonable_score" and expect_val:
            if overall < 0.2:
                passed = False
                reasons.append(f"Expected reasonable score but got {overall:.2f}")
        elif expect_key == "high_structure_score" and expect_val:
            struct = content_result["dimensions"]["structure_completeness"]["score"]
            if struct < 6:
                passed = False
                reasons.append(f"Expected high structure score but got {struct}")
        elif expect_key == "covers_expected_points" and expect_val:
            if content_result["total"] < 25:
                passed = False
                reasons.append(f"Expected coverage of points, content total: {content_result['total']}")

    return {
        "eval_name": name,
        "passed": passed,
        "question": case["question"],
        "details": details,
        "expectations": case["expect"],
        "reasons": reasons if reasons else ["All expectations met"],
    }


def _run_harness_eval(case: dict) -> dict:
    """Run engineering evals for Harness behavior."""
    from app.harness.harness import HarnessRunner, check_output
    from app.permission.permission import permission_gate
    from app.session.session import create_session
    from app.diagnosis.diagnosis import save_memory
    from app.diagnosis.context_builder import build_context

    details = {}
    passed = True
    reasons = []
    session = create_session()
    session_id = session["id"]

    if case["name"] == "harness_save_memory_permission":
        result = permission_gate.check(session_id, "save_memory", {
            "session_id": session_id,
            "key": "weakness",
            "value": "eval",
        })
        details["permission"] = result
        passed = result.get("allowed") is False and "request_id" in result
        if not passed:
            reasons.append("save_memory did not require permission")

    elif case["name"] == "harness_repeated_tool_blocked":
        runner = HarnessRunner(session_id)
        runner.pre_tool("search_knowledge", {"query": "ReAct"})
        try:
            runner.pre_tool("search_knowledge", {"query": "ReAct"})
            passed = False
            reasons.append("Repeated tool call was not blocked")
        except ValueError:
            passed = True
        details["budget"] = runner.budget.get_status()

    elif case["name"] == "harness_output_missing_score_fallback":
        result = check_output("只有一段普通文本，没有评分")
        passed = result["valid"] is False
        details["output_check"] = result
        if not passed:
            reasons.append("Invalid output was not detected")

    elif case["name"] == "harness_memory_injected":
        save_memory(session_id, "weakness", "eval weakness", "diagnosis")
        context = build_context(session_id, "新问题", "interview-diagnosis")
        passed = "eval weakness" in context
        details["memory_injected_when_available"] = passed
        if not passed:
            reasons.append("Memory was not injected into context")

    elif case["name"] == "harness_within_budget":
        runner = HarnessRunner(session_id)
        runner.record_step()
        runner.pre_tool("search_knowledge", {"query": "ReAct"})
        details["budget"] = runner.budget.get_status()
        passed = runner.budget.can_continue() and runner.budget.can_call_tool()
        if not passed:
            reasons.append("Budget exhausted too early")

    return {
        "eval_name": case["name"],
        "passed": passed,
        "question": case.get("question", ""),
        "details": details,
        "expectations": case["expect"],
        "reasons": reasons if reasons else ["All expectations met"],
    }


def run_all_evals() -> dict:
    """Run all eval cases and return results."""
    results = []
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


def save_eval_run(results: dict) -> dict:
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
