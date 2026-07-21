"""Diagnose API: POST /api/diagnose for non-streaming text diagnosis.

Orchestrates the full diagnosis pipeline and returns a structured JSON report.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import app.diagnosis.diagnosis as diagnosis_tools
from app.session.session import (
    create_session,
    get_session,
    add_message,
    add_progress_event,
    save_checkpoint,
    transition_session,
    get_recent_messages,
    mark_waiting_approval,
)
from app.skills.skills_loader import match_skill
from app.diagnosis.context_builder import build_context
from app.knowledge.knowledge_importer import search_knowledge_safe
from app.trace.trace_eval import create_trace, add_trace_event, complete_trace
from app.harness.harness import HarnessRunner, generate_fallback_report
from app.core.api_helpers import record_stage
from app.diagnosis.diagnosis import (
    score_answer,
    analyze_voice_text,
    generate_followup,
    save_diagnosis_report,
)
from app.permission.permission import build_permission_required_event, permission_gate, write_audit_log
from app.llm.agent_client import agent_client

build_memory_candidates = getattr(diagnosis_tools, "extract_memory_candidates")

router = APIRouter(prefix="/api", tags=["diagnose"])


class DiagnoseRequest(BaseModel):
    question: str
    answer: str
    session_id: str | None = None


class DiagnoseResponse(BaseModel):
    session_id: str
    trace_id: str
    overall_score: float
    content_scores: dict
    voice_scores: dict
    report: str
    followups: list[dict]
    sources: list[str]
    report_id: str | None = None


@router.post("/diagnose")
async def diagnose(req: DiagnoseRequest):
    """Diagnose an interview answer (non-streaming).

    Receives a question and answer, optionally associated with a session.
    Returns a structured diagnosis report with scores, followups, and sources.
    """
    # Create or use session
    session_id = req.session_id or create_session()["id"]
    add_progress_event(session_id, "input_received", {"question": req.question})

    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session["status"] == "created":
        transition_session(session_id, "running")

    # Create trace
    trace_id = create_trace(session_id)["id"]
    runner = HarnessRunner(session_id=session_id, skill_name="interview-diagnosis")
    raw_user_input = f"Question: {req.question}\n\nAnswer: {req.answer}"
    try:
        cleaned_input, qa_info = runner.pre_input(raw_user_input)
    except ValueError as exc:
        add_trace_event(trace_id, "pre_input", 0, {"error": str(exc)})
        complete_trace(trace_id, "failed")
        raise HTTPException(status_code=422, detail=str(exc))

    add_trace_event(trace_id, "pre_input", 0, {"qa_extracted": qa_info, "budget": runner.budget.get_status()})

    # Save user's answer as a message
    add_message(session_id, "user", cleaned_input)
    add_trace_event(trace_id, "request_received", 0, {
        "session_id": session_id,
        "question_len": len(req.question),
        "answer_len": len(req.answer),
    })

    # Match skill (based on question + answer)
    runner.record_step()
    combined_input = f"{req.question}\n{req.answer}"
    skill_match = match_skill(combined_input)
    skill_name = skill_match["name"] if skill_match else "interview-diagnosis"
    runner.skill_name = skill_name
    record_stage(
        session_id=session_id,
        trace_id=trace_id,
        stage="skill_selected",
        metadata={"skill": skill_name},
        trace_event_type="skill_selected",
        step_index=1,
        trace_data={
            "skill": skill_name,
            "selection_source": skill_match.get("selection_source") if skill_match else None,
            "budget": runner.budget.get_status(),
        },
    )

    # Search knowledge using question + answer as query
    runner.record_step()
    knowledge_results = search_knowledge_safe(
        query=combined_input,
        limit=5,
        trace_id=trace_id,
    )

    record_stage(
        session_id=session_id,
        trace_id=trace_id,
        stage="knowledge_retrieved",
        metadata={"count": len(knowledge_results)},
        trace_event_type="knowledge_retrieved",
        step_index=2,
        trace_data={"count": len(knowledge_results), "budget": runner.budget.get_status()},
    )

    # Build context for agent
    user_input = f"请诊断以下面试回答：\n\n**面试题：**{req.question}\n\n**回答：**{req.answer}"
    context = build_context(
        session_id=session_id,
        user_input=user_input,
        skill_name=skill_name,
        knowledge_results=knowledge_results,
    )
    add_trace_event(trace_id, "context_built", 3, {"context_len": len(context)})

    # Call agent (non-streaming)
    agent_output = ""
    agent_error = None
    try:
        runner.record_step()
        result = await agent_client.run(context, session_id)
        agent_output = result.get("final_output", "")
        add_trace_event(trace_id, "agent_call_complete", 4, {
            "success": result.get("success"),
            "output_len": len(agent_output),
        })
    except Exception as e:
        agent_error = str(e)
        add_trace_event(trace_id, "agent_error", 4, {"error": agent_error})

    # Score answer (heuristic, always available)
    content_scores = score_answer(req.question, req.answer, knowledge_results)
    add_trace_event(trace_id, "content_scored", 5, {
        "total": content_scores.get("total"),
        "source": content_scores.get("source"),
    })
    add_progress_event(session_id, "content_scored", {"total": content_scores.get("total")})

    # Voice analysis (text-based)
    voice_scores = analyze_voice_text(req.answer)
    add_trace_event(trace_id, "voice_scored", 6, {
        "total": voice_scores.get("total"),
        "source": voice_scores.get("source"),
    })
    add_progress_event(session_id, "voice_scored", {"total": voice_scores.get("total")})

    # Calculate overall score (average of normalized scores)
    content_total = content_scores.get("total", 0)
    content_max = content_scores.get("max_total", 50)
    voice_total = voice_scores.get("total", 0)
    voice_max = voice_scores.get("max_total", 50)
    overall_score = round(
        ((content_total / content_max) * 5 + (voice_total / voice_max) * 5),
        1,
    )

    # Extract weaknesses for follow-up generation
    weaknesses = []
    for dim_name, dim_data in content_scores.get("dimensions", {}).items():
        if dim_data.get("score", 10) < 5:
            weaknesses.append(f"{dim_name}: {dim_data.get('explanation', '')}")
    for dim_name, dim_data in voice_scores.get("dimensions", {}).items():
        if dim_data.get("score", 10) < 5:
            weaknesses.append(f"{dim_name}: {dim_data.get('explanation', '')}")

    # Generate follow-ups
    followups = generate_followup(req.question, req.answer, weaknesses)
    add_trace_event(trace_id, "followups_generated", 7, {"count": len(followups)})

    # Use agent output or generate report
    report = agent_output or _generate_report_markdown(
        req.question, req.answer, content_scores, voice_scores, overall_score, followups, knowledge_results
    )

    # Output check
    report, output_check_result = runner.post_output(report)
    if not output_check_result.get("valid", True):
        report = generate_fallback_report(req.question, req.answer)
        add_trace_event(trace_id, "output_check", 8, {"result": "fallback", "issues": output_check_result.get("issues", [])})
    else:
        add_trace_event(trace_id, "output_check", 8, {"result": "passed", "budget": runner.budget.get_status()})

    add_progress_event(session_id, "output_checked", {"valid": output_check_result.get("valid", True)})

    # Save diagnosis report
    try:
        report_result = save_diagnosis_report(
            session_id=session_id,
            question=req.question,
            answer=req.answer,
            content_scores=content_scores,
            voice_scores=voice_scores,
            overall_score=overall_score,
            report_markdown=report,
        )
        report_id = report_result["id"]
    except Exception:
        report_id = None

    memory_candidates = build_memory_candidates(session_id, content_scores, voice_scores, report)
    memory_permission = None
    if memory_candidates:
        first_candidate = memory_candidates[0]
        permission = permission_gate.check(session_id, "save_memory", first_candidate)
        if not permission.get("allowed"):
            memory_permission = build_permission_required_event(
                session_id=session_id,
                tool_name="save_memory",
                permission_result=permission,
                params=first_candidate,
                message="保存诊断记忆需要用户确认",
            )
            write_audit_log(
                session_id=session_id,
                tool_name="save_memory",
                risk_level=memory_permission["risk_level"],
                action="request",
                params=first_candidate,
            )
            mark_waiting_approval(session_id, memory_permission["request_id"], "save_memory")
            add_trace_event(trace_id, "permission_required", 8, memory_permission)
            add_progress_event(session_id, "memory_updated", {
                "pending_permission": True,
                "candidate_count": len(memory_candidates),
                "request_id": memory_permission["request_id"],
            })
        else:
            add_progress_event(session_id, "memory_updated", {"candidate_count": len(memory_candidates), "auto_allowed": True})
    else:
        add_progress_event(session_id, "memory_updated", {"candidate_count": 0})

    # Save checkpoint
    try:
        save_checkpoint(
            session_id=session_id,
            state="completed",
            progress=[
                "input_received", "skill_selected", "knowledge_retrieved",
                "content_scored", "voice_scored", "report_generated",
                "output_checked",
            ],
            messages=get_recent_messages(session_id, n=20),
            knowledge=[k.get("title", "") for k in knowledge_results],
            memory_keys=[w.split(":")[0] for w in weaknesses],
        )
        add_trace_event(trace_id, "checkpoint_saved", 9, {"budget": runner.budget.get_status()})
    except Exception:
        pass

    # Save assistant message
    add_message(session_id, "assistant", report)
    add_progress_event(session_id, "report_generated", {"report_id": report_id})

    # Complete trace
    complete_trace(trace_id, "completed")
    add_trace_event(trace_id, "final_response", 10, {"output_len": len(report)})

    # Transition session
    try:
        transition_session(session_id, "completed")
        add_progress_event(session_id, "completed", {"trace_id": trace_id})
    except Exception:
        pass

    # Sources from knowledge
    sources = [k.get("source", k.get("title", "")) for k in knowledge_results]

    return {
        "session_id": session_id,
        "trace_id": trace_id,
        "overall_score": overall_score,
        "content_scores": content_scores,
        "voice_scores": voice_scores,
        "report": report,
        "followups": followups,
        "sources": sources,
        "report_id": report_id,
        "memory_candidates": memory_candidates,
        "memory_permission": memory_permission,
    }


def _generate_report_markdown(
    question: str,
    answer: str,
    content_scores: dict,
    voice_scores: dict,
    overall_score: float,
    followups: list[dict],
    knowledge_results: list[dict] | None = None,
) -> str:
    """Generate a Markdown diagnosis report from scores."""
    lines = [
        "# 面试诊断报告",
        "",
        f"**总分：{overall_score}/10**",
        "",
        "## 面试题",
        question,
        "",
        "## 回答摘要",
        answer[:300] + ("..." if len(answer) > 300 else ""),
        "",
        "## 内容维度评分",
        "",
        "| 维度 | 评分 | 说明 |",
        "|------|------|------|",
    ]

    for dim_name, dim_data in content_scores.get("dimensions", {}).items():
        dim_labels = {
            "concept_accuracy": "概念准确性",
            "structure_completeness": "结构完整性",
            "engineering_depth": "工程深度",
            "example_quality": "示例质量",
            "question_alignment": "问题契合度",
        }
        label = dim_labels.get(dim_name, dim_name)
        lines.append(f"| {label} | {dim_data['score']}/10 | {dim_data.get('explanation', '')} |")

    lines.append(f"| **内容总分** | **{content_scores.get('total', 0)}/{content_scores.get('max_total', 50)}** | |")
    lines.append("")
    lines.append("## 语音维度评分")
    lines.append("")
    lines.append("| 维度 | 评分 | 说明 |")
    lines.append("|------|------|------|")

    for dim_name, dim_data in voice_scores.get("dimensions", {}).items():
        dim_labels = {
            "fluency": "流畅度",
            "filler_words": "口头禅控制",
            "redundancy": "冗余度",
            "spoken_clarity": "口语清晰度",
            "answer_pacing": "回答节奏",
        }
        label = dim_labels.get(dim_name, dim_name)
        lines.append(f"| {label} | {dim_data['score']}/10 | {dim_data.get('explanation', '')} |")

    lines.append(f"| **语音总分** | **{voice_scores.get('total', 0)}/{voice_scores.get('max_total', 50)}** | |")
    lines.append("")

    # Highlights and gaps
    lines.append("## 亮点")
    strengths = [
        d for d in content_scores.get("dimensions", {}).values() if d.get("score", 0) >= 7
    ] + [
        d for d in voice_scores.get("dimensions", {}).values() if d.get("score", 0) >= 7
    ]
    if strengths:
        for s in strengths[:3]:
            lines.append(f"- {s.get('explanation', '表现良好')}")
    else:
        lines.append("- 未见明显亮点")

    lines.append("")
    lines.append("## 主要差距")
    weaknesses = [
        d for d in content_scores.get("dimensions", {}).values() if d.get("score", 0) < 5
    ] + [
        d for d in voice_scores.get("dimensions", {}).values() if d.get("score", 0) < 5
    ]
    if weaknesses:
        for w in weaknesses:
            lines.append(f"- {w.get('explanation', '需要改进')}")
    else:
        lines.append("- 各维度表现均衡")

    lines.append("")
    lines.append("## 改进建议")
    lines.append("1. 针对薄弱维度进行专项练习")
    lines.append("2. 使用 STAR 原则组织回答结构")
    lines.append("3. 增加具体案例和工程实践经验分享")
    lines.append("4. 注意控制口头禅和冗余表述")

    # Follow-ups
    if followups:
        lines.append("")
        lines.append("## 可能追问")
        for i, fq in enumerate(followups[:5], 1):
            lines.append(f"{i}. {fq['question']}（{fq.get('why', '')}）")
        lines.append("")
        lines.append("## 参考回答")
        lines.append("(*请参考知识库中的相关内容*)")

    if knowledge_results:
        lines.append("")
        lines.append("## 知识来源")
        for k in knowledge_results[:5]:
            lines.append(f"- {k.get('title', '')} ({k.get('dimension', '')}, score: {k.get('score', 0)})")

    return "\n".join(lines)
