"""Single-worker Run dispatcher with durable lifecycle transitions."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from offerpilot.coach.loop import CoachLoop
from offerpilot.audio.transcription import transcribe_audio
from offerpilot.audio.storage import begin_audio_transcription, finalize_audio_upload, get_audio_upload
from offerpilot.approvals.repository import create_approval, get_approval
from offerpilot.approvals.service import cancel_open_approvals, claim_approval, finish_approval
from offerpilot.diagnosis.workflow import DIAGNOSIS_RUN_TIMEOUT_SECONDS
from offerpilot.diagnosis.workflow import run_diagnosis
from offerpilot.diagnosis.repository import get_report
from offerpilot.core.logging import bind_context, log_event, log_exception, reset_context
from offerpilot.core.errors import is_database_busy
from offerpilot.core.deadlines import deadline_after
from offerpilot.runs.events import notify
from offerpilot.runs.operation_logs import write_log, write_permission_log
from offerpilot.runs.repository import (
    RUN_TERMINAL,
    append_event,
    begin_run,
    get_run,
    get_run_timing,
    list_active_run_ids,
    transition_run,
    request_cancel,
)
from offerpilot.sessions.followups import create_followups, finish_followup_for_run
from offerpilot.sessions.repository import add_audio_transcript
from offerpilot.sessions.summaries import rebuild_session_summary

logger = logging.getLogger(__name__)


def _event_payload(event: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    event_type = str(event.get("type", "event"))
    payload = event.get("data")
    if isinstance(payload, dict):
        return event_type, payload
    return event_type, {
        key: value
        for key, value in event.items()
        if key not in {"type", "session_id", "run_id", "sequence", "created_at"}
    }


class RunService:
    """Dispatch one durable worker per Run and centralize terminal cleanup."""

    def __init__(self) -> None:
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.cancel_events: dict[str, asyncio.Event] = {}
        self._redispatch: set[str] = set()

    def start(self, run_id: str) -> None:
        task = self.tasks.get(run_id)
        if task is not None and not task.done():
            self._redispatch.add(run_id)
            return
        run = get_run(run_id)
        if run is None or run["status"] in RUN_TERMINAL:
            return
        cancel_event = self.cancel_events.setdefault(run_id, asyncio.Event())
        task = asyncio.create_task(self._execute(run_id, cancel_event), name=f"offerpilot-run-{run_id}")
        self.tasks[run_id] = task
        task.add_done_callback(lambda completed: self._on_task_done(run_id, completed))

    def _on_task_done(self, run_id: str, completed: asyncio.Task[None]) -> None:
        if self.tasks.get(run_id) is completed:
            self.tasks.pop(run_id, None)
        run = get_run(run_id)
        if run is None or run["status"] in RUN_TERMINAL:
            self.cancel_events.pop(run_id, None)
            self._redispatch.discard(run_id)
            return
        if run_id in self._redispatch:
            self._redispatch.discard(run_id)
            self.start(run_id)

    def cancel(self, run_id: str) -> None:
        event = self.cancel_events.get(run_id)
        if event is not None:
            event.set()

    async def cancel_and_wait(self, run_ids: list[str], timeout: float = 10.0) -> bool:
        """Signal running workers and wait as a group, never one timeout per Run."""
        for run_id in run_ids:
            self.cancel(run_id)
        tasks = [task for run_id, task in self.tasks.items() if run_id in run_ids and not task.done()]
        if tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=timeout)
            except TimeoutError:
                return False
        return all((run := get_run(run_id)) is None or run["status"] in RUN_TERMINAL for run_id in run_ids)

    async def cancel_owned_runs(self, profile_id: str, *, session_id: str | None = None, timeout: float = 10.0) -> bool:
        run_ids = list_active_run_ids(profile_id=profile_id, session_id=session_id)
        for run_id in run_ids:
            run = get_run(run_id, profile_id)
            if run is None or not request_cancel(run_id, profile_id):
                continue
            self.cancel(run_id)
            if run["status"] in {"pending", "waiting_approval"}:
                await self._finalize(run_id, "cancelled", error_code="cancelled")
        return await self.cancel_and_wait(run_ids, timeout=timeout)

    async def _execute(self, run_id: str, cancel_event: asyncio.Event) -> None:
        run = get_run(run_id)
        if run is None:
            return
        tokens = bind_context(run_id=run_id, session_id=run["session_id"])
        try:
            await self._execute_bound(run_id, cancel_event, run)
        except asyncio.CancelledError:
            await self._finalize(run_id, "cancelled", error_code="cancelled")
        except Exception as exc:
            code = "database_busy" if is_database_busy(exc) else str(getattr(exc, "category", "") or getattr(exc, "code", "run_failed"))
            if cancel_event.is_set():
                await self._finalize(run_id, "cancelled", error_code="cancelled")
            else:
                log_exception(logger, "run_worker_failed", exc, include_stack=True, run_id=run_id, error_code=code)
                await self._finalize(run_id, "failed", error_code=code)
        finally:
            reset_context(tokens)

    async def _execute_bound(self, run_id: str, cancel_event: asyncio.Event, run: dict[str, Any]) -> None:
        if run["status"] == "pending":
            claimed_run = begin_run(run_id)
            if claimed_run is None:
                return
            run = claimed_run
        if run["cancel_requested_at"] or cancel_event.is_set():
            await self._finalize(run_id, "cancelled", error_code="cancelled")
            return
        if run["status"] == "waiting_approval":
            if not await self._resume_if_decided(run_id, run):
                return
            resumed_run = get_run(run_id)
            if resumed_run is None or resumed_run["status"] != "running":
                return
            run = resumed_run
        if run["status"] != "running":
            return
        if run["type"] == "coach":
            await self._coach(run, cancel_event)
        elif run["type"] == "diagnosis":
            await self._diagnosis(run, cancel_event)
        elif run["type"] == "audio_transcription":
            await self._audio(run, cancel_event)
        elif run["type"] == "report_export":
            await self._export(run, cancel_event)
        else:
            await self._finalize(run_id, "failed", error_code="invalid_run_type")

    async def _resume_if_decided(self, run_id: str, run: dict[str, Any]) -> bool:
        approval_id = str(run["state"].get("approval_id", ""))
        approval = get_approval(approval_id, run["profile_id"]) if approval_id else None
        if approval is None or approval["status"] == "pending":
            return False
        expected_flow = {
            "coach": "coach",
            "audio_transcription": "audio",
            "report_export": "export",
        }.get(str(run["type"]))
        if expected_flow is None or approval["flow_kind"] != expected_flow:
            await self._finalize(run_id, "failed", error_code="approval_flow_mismatch")
            return False
        if approval["status"] == "denied" and approval["flow_kind"] in {"audio", "export"}:
            await self._finalize(run_id, "cancelled", error_code="permission_denied")
            return False
        if approval["status"] not in {"approved", "denied"}:
            return False
        transition_run(
            run_id,
            "running",
            state=run["state"],
            event_type="run_resumed",
            event_data={"approval_id": approval["id"]},
        )
        notify(run_id)
        return True

    async def _coach(self, run: dict[str, Any], cancel_event: asyncio.Event) -> None:
        run_id = run["id"]
        loop = CoachLoop(
            session_id=run["session_id"],
            profile_id=run["profile_id"],
            run_id=run_id,
            cancel_event=cancel_event,
        )
        approval_id = str(run["state"].get("approval_id", ""))
        approval = get_approval(approval_id, run["profile_id"]) if approval_id else None
        event_stream = loop.resume_stream(approval_id, approval) if approval and approval["status"] in {"approved", "denied"} else loop.stream()
        pending_approval: dict[str, Any] | None = None
        async for raw in event_stream:
            event_type, payload = _event_payload(raw)
            if event_type == "ping":
                continue
            if event_type == "approval_required":
                pending_approval = payload
                continue
            append_event(run_id, event_type, payload)
            notify(run_id)
        result = loop.result
        if result and result.waiting_approval:
            current = get_run(run_id)
            if current is None:
                return
            approval_id = str(current["state"].get("approval_id", ""))
            transition_run(
                run_id,
                "waiting_approval",
                state=current["state"],
                event_type="run_waiting_approval",
                event_data={"approval_id": approval_id},
            )
            append_event(run_id, "approval_required", pending_approval or {"approval_id": approval_id, "flow_kind": "coach"})
            notify(run_id)
            return
        if result and result.success:
            await self._finalize(run_id, "completed", result={"output": result.final_output})
        elif result and result.error == "cancelled":
            await self._finalize(run_id, "cancelled", error_code="cancelled")
        else:
            await self._finalize(run_id, "failed", error_code=result.error if result else "coach_failed")

    async def _diagnosis(self, run: dict[str, Any], cancel_event: asyncio.Event) -> None:
        payload = run["input"]
        result = await run_diagnosis(
            session_id=run["session_id"],
            profile_id=run["profile_id"],
            run_id=run["id"],
            question=str(payload.get("question", "")),
            answer=str(payload.get("answer", "")),
            timeout=DIAGNOSIS_RUN_TIMEOUT_SECONDS,
            cancel_event=cancel_event,
        )
        if cancel_event.is_set():
            raise asyncio.CancelledError()
        if result.get("report_id"):
            result["followups"] = create_followups(run["session_id"], run["profile_id"], result["report_id"], result.get("diagnosis", {}))
        rebuild_session_summary(run["session_id"], run["profile_id"])
        await self._finalize(run["id"], "completed", result=result)

    async def _audio(self, run: dict[str, Any], cancel_event: asyncio.Event) -> None:
        run_id = run["id"]
        upload_id = str(run["input"].get("upload_id", ""))
        approval_id = str(run["state"].get("approval_id", ""))
        approval = get_approval(approval_id, run["profile_id"]) if approval_id else None
        if approval is None:
            upload = get_audio_upload(upload_id, run["session_id"], run["profile_id"])
            if upload is None or upload["status"] != "pending_approval":
                raise RuntimeError("audio_upload_unavailable")
            public_params = {"content_type": upload["content_type"], "size": upload["size_bytes"]}
            approval_id = create_approval(run_id, run["session_id"], run["profile_id"], "transcribe_audio", "medium", {"upload_id": upload_id}, "audio", public_params)
            write_permission_log(
                run["session_id"],
                "transcribe_audio",
                "medium",
                "request",
                profile_id=run["profile_id"],
                run_id=run_id,
            )
            transition_run(run_id, "waiting_approval", state={"approval_id": approval_id}, event_type="run_waiting_approval", event_data={"approval_id": approval_id})
            append_event(run_id, "approval_required", {"approval_id": approval_id, "tool_name": "transcribe_audio", "risk_level": "medium", "flow_kind": "audio", "public_params": public_params})
            notify(run_id)
            return
        if approval["status"] != "approved":
            return
        if claim_approval(approval["id"], run["profile_id"]) is None:
            return
        claimed = begin_audio_transcription(
            upload_id,
            run["session_id"],
            run["profile_id"],
            run_id=run_id,
        )
        if claimed is None:
            finish_approval(approval["id"], False, "audio_upload_unavailable")
            raise RuntimeError("audio_upload_unavailable")
        asr_deadline = deadline_after(30.0)
        result = await transcribe_audio(
            claimed["storage_name"],
            cancel_event=cancel_event,
            timeout=30.0,
            deadline=asr_deadline,
            run_id=run_id,
            session_id=run["session_id"],
            profile_id=run["profile_id"],
            logical_call_id=f"asr:{upload_id}",
        )
        if cancel_event.is_set():
            raise asyncio.CancelledError()
        transcript = str(result.get("transcript", ""))
        finalize_audio_upload(upload_id, run["session_id"], run["profile_id"], "completed" if transcript else "failed", error=str(result.get("error", "")), run_id=run_id)
        finish_approval(approval["id"], bool(transcript), str(result.get("error", "")))
        write_permission_log(
            run["session_id"],
            "transcribe_audio",
            "medium",
            "execute",
            result="ok" if transcript else "tool_execution_failed",
            profile_id=run["profile_id"],
            run_id=run_id,
        )
        if not transcript:
            await self._finalize(run_id, "failed", result=result, error_code=str(result.get("error") or "asr_failed"))
            return
        add_audio_transcript(run["session_id"], run_id, transcript, upload_id)
        await self._finalize(run_id, "completed", result=result)

    async def _export(self, run: dict[str, Any], cancel_event: asyncio.Event) -> None:
        run_id = run["id"]
        approval_id = str(run["state"].get("approval_id", ""))
        approval = get_approval(approval_id, run["profile_id"]) if approval_id else None
        if approval is None:
            report_id = str(run["input"].get("report_id", ""))
            approval_id = create_approval(run_id, run["session_id"], run["profile_id"], "export_report", "high", {"report_id": report_id}, "export", {"report_id": report_id})
            write_permission_log(
                run["session_id"],
                "export_report",
                "high",
                "request",
                profile_id=run["profile_id"],
                run_id=run_id,
            )
            transition_run(run_id, "waiting_approval", state={"approval_id": approval_id}, event_type="run_waiting_approval", event_data={"approval_id": approval_id})
            append_event(run_id, "approval_required", {"approval_id": approval_id, "tool_name": "export_report", "risk_level": "high", "flow_kind": "export", "public_params": {"report_id": report_id}})
            notify(run_id)
            return
        if approval["status"] != "approved" or claim_approval(approval["id"], run["profile_id"]) is None:
            return
        report = get_report(str(run["input"].get("report_id", "")), run["profile_id"])
        if report is None or report["session_id"] != run["session_id"]:
            finish_approval(approval["id"], False, "report_not_found")
            raise RuntimeError("report_not_found")
        if cancel_event.is_set():
            raise asyncio.CancelledError()
        finish_approval(approval["id"], True)
        write_permission_log(
            run["session_id"],
            "export_report",
            "high",
            "execute",
            result="ok",
            profile_id=run["profile_id"],
            run_id=run_id,
        )
        await self._finalize(run_id, "completed", result={"report": report})

    async def _finalize(
        self,
        run_id: str,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error_code: str = "",
    ) -> None:
        current = get_run(run_id)
        if current is None or current["status"] in RUN_TERMINAL:
            return
        event_type = {
            "completed": "run_completed",
            "failed": "run_failed",
            "cancelled": "run_cancelled",
            "interrupted": "run_interrupted",
        }[status]
        event_data: dict[str, Any] = {"success": status == "completed"}
        if error_code:
            event_data["error"] = error_code
        try:
            updated = transition_run(run_id, status, result=result, error_code=error_code, event_type=event_type, event_data=event_data)
        except ValueError:
            return
        if updated is None:
            return
        if status != "completed":
            finish_followup_for_run(run_id, updated["profile_id"], False)
            cancel_open_approvals(run_id, error=error_code or status)
            if updated["type"] == "audio_transcription":
                upload_id = str(updated["input"].get("upload_id", ""))
                if upload_id:
                    finalize_audio_upload(
                        upload_id,
                        updated["session_id"],
                        updated["profile_id"],
                        "cancelled",
                        error=error_code or status,
                        run_id=run_id,
                    )
        else:
            followup_id = updated["input"].get("followup_id")
            if followup_id:
                finish_followup_for_run(run_id, updated["profile_id"], True)
        timing = get_run_timing(run_id)
        if status in {"failed", "interrupted"}:
            write_log(
                f"run_{status}",
                f"{updated['type']} Run {status}",
                profile_id=updated["profile_id"],
                session_id=updated["session_id"],
                run_id=run_id,
                metadata={"status": status, "error_code": error_code},
                level="error",
            )
        log_event(
            logger,
            logging.INFO,
            "run_finalized",
            run_id=run_id,
            run_type=updated["type"],
            run_status=status,
            error_code=error_code,
            **timing,
        )
        notify(run_id)


run_service = RunService()
