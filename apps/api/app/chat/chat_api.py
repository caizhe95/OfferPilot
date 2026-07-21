"""Chat API: POST /api/chat with SSE streaming.

Orchestrates the full diagnosis pipeline:
  session → skill matching → context building → agent call → SSE streaming
"""

import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.session.session import (
    create_session,
    get_session,
    add_message,
    get_recent_messages,
    add_progress_event,
    save_checkpoint,
    transition_session,
    VALID_TRANSITIONS,
    mark_waiting_approval,
)
from app.skills.skills_loader import match_skill
from app.diagnosis.context_builder import build_context
from app.knowledge.knowledge_importer import search_knowledge_safe
from app.trace.trace_eval import create_trace, add_trace_event, complete_trace
from app.harness.harness import HarnessRunner, generate_fallback_report
from app.llm.agent_client import agent_client
from app.core.api_helpers import record_stage

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str


@router.post("/chat")
async def chat(req: ChatRequest):
    """Chat endpoint with SSE streaming.

    Accepts a message and optional session_id.
    Creates a new session if none provided.
    Streams Agent SSE events back to the client.
    """
    session_id = req.session_id

    # Create session if needed
    if not session_id:
        session_id = create_session()["id"]
    else:
        # Validate existing session
        session = get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        # If session is already completed/failed, create a new one
        if session["status"] in ("completed", "failed"):
            session_id = create_session()["id"]

    # Transition to running
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session["status"] != "running":
        if "running" not in VALID_TRANSITIONS.get(session["status"], set()):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot transition from {session['status']} to running",
            )
        transition_session(session_id, "running")

    # Create trace
    trace_id = create_trace(session_id)["id"]
    runner = HarnessRunner(session_id=session_id)
    try:
        user_message, qa_info = runner.pre_input(req.message)
    except ValueError as exc:
        add_trace_event(trace_id, "pre_input", 0, {"error": str(exc)})
        complete_trace(trace_id, "failed")
        raise HTTPException(status_code=422, detail=str(exc))

    add_trace_event(trace_id, "pre_input", 0, {"qa_extracted": qa_info, "budget": runner.budget.get_status()})

    # Save user message
    add_message(session_id, "user", user_message)
    add_progress_event(session_id, "input_received", {"message": user_message})
    add_progress_event(session_id, "qa_extracted", qa_info)

    add_trace_event(trace_id, "request_received", 0, {
        "session_id": session_id, "message_len": len(user_message),
    })

    # Match skill (for context selection)
    runner.record_step()
    skill_match = match_skill(user_message)
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

    # Search knowledge using user input as query
    runner.record_step()
    knowledge_results = search_knowledge_safe(
        query=user_message,
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

    # Build context
    context = build_context(
        session_id=session_id,
        user_input=user_message,
        skill_name=skill_name,
        knowledge_results=knowledge_results,
    )
    add_trace_event(trace_id, "context_built", 3, {"context_len": len(context)})

    async def event_generator():
        assistant_content = ""
        final_output = ""

        try:
            runner.record_step()
            add_trace_event(trace_id, "agent_call_start", 4, {})
            add_progress_event(session_id, "report_generated", {"status": "started"})

            async for event in agent_client.run_stream(context, session_id):
                event_type = event.get("type", "")

                if event_type == "tool_call":
                    tool_name = str(event.get("tool_name") or "")
                    raw_params = event.get("params")
                    tool_params = raw_params if isinstance(raw_params, dict) else {}
                    try:
                        runner.pre_tool(tool_name, tool_params)
                        add_trace_event(
                            trace_id, "pre_tool", 9,
                            {"tool_name": tool_name, "budget": runner.budget.get_status()},
                        )
                    except Exception as exc:
                        error_event = {
                            "type": "tool_result",
                            "tool_name": tool_name,
                            "result": {"error": True, "message": str(exc), "tool_name": tool_name},
                        }
                        add_trace_event(trace_id, "pre_tool", 9, {"tool_name": tool_name, "error": str(exc)})
                        yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"
                        continue
                    add_trace_event(
                        trace_id, "tool_call", 10,
                        {"tool_name": tool_name, "params": tool_params},
                    )
                elif event_type == "tool_result":
                    result = event.get("result")
                    if isinstance(result, dict) and result.get("permission_required"):
                        add_trace_event(trace_id, "permission_required", 21, result)
                        try:
                            mark_waiting_approval(
                                session_id,
                                result.get("request_id", ""),
                                result.get("tool_name", event.get("tool_name", "")),
                            )
                        except Exception as exc:
                            add_trace_event(trace_id, "permission_required", 21, {"session_error": str(exc)})
                        add_progress_event(session_id, "permission_checked", {
                            "status": "waiting_approval",
                            "request_id": result.get("request_id"),
                            "tool_name": result.get("tool_name"),
                        })
                        yield f"data: {json.dumps(result, ensure_ascii=False)}\n\n"
                        yield f"data: {json.dumps({'type': 'run_complete', 'session_id': session_id, 'trace_id': trace_id, 'success': False, 'waiting_approval': True}, ensure_ascii=False)}\n\n"
                        return
                    normalized = runner.post_tool(event.get("tool_name", ""), result)
                    event["result"] = normalized
                    add_trace_event(
                        trace_id, "tool_result", 20,
                        {"tool_name": event.get("tool_name"),
                         "result_summary": str(event.get("result"))[:200],
                         "budget": runner.budget.get_status()},
                    )
                    add_trace_event(trace_id, "post_tool", 22, {"tool_name": event.get("tool_name")})
                elif event_type == "text_delta":
                    assistant_content += event.get("content", "")
                elif event_type == "done":
                    final_output = event.get("final_output", "")
                    assistant_content = final_output
                elif event_type == "error":
                    add_trace_event(trace_id, "agent_error", 50, {"error": event.get("message")})

                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            # Output check
            final_text = final_output or assistant_content
            final_text, output_check_result = runner.post_output(final_text)
            output_valid = output_check_result.get("valid", True)

            if not output_valid:
                fallback = generate_fallback_report(user_message, final_text)
                final_output = fallback
                final_text = fallback
                add_trace_event(trace_id, "output_check", 70, {"result": "fallback_generated", "issues": output_check_result.get("issues", [])})
                yield f"data: {json.dumps({'type': 'text_delta', 'content': fallback}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'final_output': fallback}, ensure_ascii=False)}\n\n"
            else:
                add_trace_event(trace_id, "output_check", 70, {"result": "passed", "budget": runner.budget.get_status()})

            # Save checkpoint
            save_checkpoint(
                session_id=session_id,
                state="running",
                progress=[
                    "input_received", "skill_selected", "knowledge_retrieved",
                    "report_generated", "output_checked",
                ],
                messages=get_recent_messages(session_id, n=20),
                knowledge=[k.get("title", "") for k in knowledge_results],
                memory_keys=[],
            )
            add_trace_event(trace_id, "checkpoint_saved", 60, {"budget": runner.budget.get_status()})

            # Save assistant message
            add_message(session_id, "assistant", final_text)
            add_progress_event(session_id, "output_checked", {"valid": output_valid})

            # Complete trace
            complete_trace(trace_id, "completed")
            add_trace_event(trace_id, "final_response", 80, {"output_len": len(final_text)})

            # Transition session to completed
            transition_session(session_id, "completed")
            add_progress_event(session_id, "completed", {"trace_id": trace_id})

            yield f"data: {json.dumps({'type': 'run_complete', 'session_id': session_id, 'trace_id': trace_id, 'success': True}, ensure_ascii=False)}\n\n"

        except Exception as e:
            error_msg = str(e)
            add_trace_event(trace_id, "fatal_error", 99, {"error": error_msg})
            add_message(session_id, "assistant", f"Error: {error_msg}")
            complete_trace(trace_id, "failed")

            try:
                transition_session(session_id, "failed")
            except Exception:
                pass

            yield f"data: {json.dumps({'type': 'error', 'message': error_msg}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'run_complete', 'session_id': session_id, 'trace_id': trace_id, 'success': False, 'error': error_msg}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
