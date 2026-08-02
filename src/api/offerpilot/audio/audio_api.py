"""Audio upload, durable approval, and ASR endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict

from offerpilot.audio.audio import (
    MAX_AUDIO_SIZE,
    UPLOAD_CHUNK_BYTES,
    begin_audio_transcription,
    complete_audio_upload,
    create_audio_upload,
    finalize_audio_upload,
    public_audio_params,
    resolve_uploaded_audio,
    save_transcript_to_session,
    transcribe_audio,
    validate_audio,
    validate_audio_signature,
)
from offerpilot.coaching.state import claim_approved_approval, create_approval, finish_approval, get_approval
from offerpilot.core.api_helpers import permission_required_response
from offerpilot.core.config import settings
from offerpilot.core.deadline import deadline_after
from offerpilot.core.profile import require_owned_session, require_profile_id
from offerpilot.permission.permission import permission_gate, write_audit_log
from offerpilot.session.session import get_session, resume_after_approval

router = APIRouter(prefix="/api/audio", tags=["audio"])


class AudioResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    session_id: str


async def _transcribe_claimed_upload(
    upload: dict,
    *,
    cancel_event: asyncio.Event | None = None,
) -> dict:
    """Run ASR after the upload status was atomically claimed."""
    result = await transcribe_audio(
        upload["storage_name"],
        cancel_event=cancel_event,
        deadline=deadline_after(30.0),
    )
    if "error" in result:
        finalize_audio_upload(
            upload["id"], upload["session_id"], upload["profile_id"], "failed", error=str(result["error"])
        )
        return {"status": "asr_failed", **result}
    saved = save_transcript_to_session(upload["session_id"], result["transcript"], upload["storage_name"])
    finalize_audio_upload(upload["id"], upload["session_id"], upload["profile_id"], "completed")
    return {"status": "transcribed", **result, "saved": saved}


@router.post("/upload")
async def upload_audio(
    session_id: str = Form(...),
    file: UploadFile = File(...),
    profile_id: str = Depends(require_profile_id),
):
    """Store an owned WAV/MP3 upload in chunks before creating an ASR approval."""
    require_owned_session(get_session(session_id), profile_id)
    content_type = file.content_type or ""
    valid_type, error_msg = validate_audio(content_type, 1)
    if not valid_type:
        raise HTTPException(status_code=400, detail=error_msg)
    upload = create_audio_upload(
        session_id=session_id,
        profile_id=profile_id,
        original_filename=file.filename or "audio.wav",
        content_type=content_type,
    )
    size = 0
    header = bytearray()
    started_at = asyncio.get_running_loop().time()
    try:
        path = resolve_uploaded_audio(upload["storage_name"])
        with path.open("wb") as destination:
            while True:
                chunk = await file.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_AUDIO_SIZE:
                    raise ValueError(f"文件大小超过限制 ({MAX_AUDIO_SIZE // (1024 * 1024)}MB)")
                if len(header) < 12:
                    header.extend(chunk[: 12 - len(header)])
                await asyncio.to_thread(destination.write, chunk)
                rate = max(1, settings.audio_upload_max_bytes_per_second)
                expected_elapsed = size / rate
                remaining_delay = expected_elapsed - (asyncio.get_running_loop().time() - started_at)
                if remaining_delay > 0:
                    await asyncio.sleep(remaining_delay)
        valid, error_msg = validate_audio(content_type, size)
        if not valid:
            raise ValueError(error_msg)
        valid_signature, signature_error = validate_audio_signature(content_type, bytes(header))
        if not valid_signature:
            raise ValueError(signature_error)
        completed_upload = complete_audio_upload(upload["id"], session_id, profile_id, size)
        if completed_upload is None:
            raise RuntimeError("Audio upload state changed before approval")
        upload = completed_upload
    except Exception as exc:
        finalize_audio_upload(upload["id"], session_id, profile_id, "failed", error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await file.close()

    public_params = public_audio_params(
        filename=upload["original_filename"], content_type=upload["content_type"], size=upload["size_bytes"]
    )
    policy = permission_gate.check(session_id, "transcribe_audio", {"upload_id": upload["id"]}, profile_id=profile_id)
    if not policy["allowed"]:
        request_id = create_approval(
            session_id,
            profile_id,
            "transcribe_audio",
            policy["risk_level"],
            {"upload_id": upload["id"]},
            flow_kind="audio",
            public_params=public_params,
        )
        event = permission_required_response(
            session_id=session_id,
            tool_name="transcribe_audio",
            message="音频转写需要用户确认，批准后才会调用 ASR 服务",
            permission_result={"request_id": request_id, "risk_level": policy["risk_level"]},
            params=public_params,
        )
        event["status"] = "approval_required"
        return event

    claimed = begin_audio_transcription(upload["id"], session_id, profile_id)
    if claimed is None:
        raise HTTPException(status_code=409, detail="Audio upload is not available for transcription")
    result = await _transcribe_claimed_upload(claimed)
    write_audit_log(
        session_id=session_id,
        tool_name="transcribe_audio",
        risk_level="medium",
        action="execute",
        params=public_params,
        result=f"Transcript length: {len(result.get('transcript', ''))} chars",
    )
    return result


@router.post("/resume")
async def resume_audio_approval(body: AudioResumeRequest, profile_id: str = Depends(require_profile_id)):
    """Execute exactly one approved Audio approval and always delete its bytes."""
    require_owned_session(get_session(body.session_id), profile_id)
    approval = get_approval(body.request_id, body.session_id, profile_id)
    if approval is None or approval["flow_kind"] != "audio" or approval["tool_name"] != "transcribe_audio":
        raise HTTPException(status_code=409, detail="Approval does not belong to the Audio flow")
    pending = claim_approved_approval(body.request_id, body.session_id, profile_id)
    if pending is None:
        raise HTTPException(status_code=409, detail="Approval is not ready for a single execution")
    upload_id = pending["params"].get("upload_id")
    if not isinstance(upload_id, str):
        finish_approval(body.request_id, succeeded=False, error="missing managed upload")
        raise HTTPException(status_code=400, detail="Audio approval has no managed upload")
    claimed = begin_audio_transcription(upload_id, body.session_id, profile_id)
    if claimed is None:
        finish_approval(body.request_id, succeeded=False, error="audio upload unavailable")
        raise HTTPException(status_code=409, detail="Audio upload is not available for transcription")
    try:
        result = await _transcribe_claimed_upload(claimed)
        finish_approval(body.request_id, succeeded=result["status"] == "transcribed", error=str(result.get("error", "")))
        write_audit_log(
            session_id=body.session_id,
            tool_name="transcribe_audio",
            risk_level=pending["risk_level"],
            action="execute",
            params=pending["public_params"],
            result=f"Transcript length: {len(result.get('transcript', ''))} chars",
        )
        return result
    finally:
        resume_after_approval(body.session_id)


@router.post("/transcript/manual")
async def manual_transcript(
    session_id: str = Form(...),
    transcript: str = Form(...),
    profile_id: str = Depends(require_profile_id),
):
    require_owned_session(get_session(session_id), profile_id)
    if not transcript.strip():
        raise HTTPException(status_code=400, detail="Transcript must not be empty")
    saved = save_transcript_to_session(session_id, transcript.strip(), "manual")
    return {"status": "saved", "transcript": transcript, "saved": saved}


@router.get("/supported-formats")
async def supported_formats():
    from offerpilot.audio.audio import ALLOWED_AUDIO_TYPES

    return {"formats": [{"mime": mime, "extension": ext} for mime, ext in ALLOWED_AUDIO_TYPES.items()], "max_size_mb": 25}
