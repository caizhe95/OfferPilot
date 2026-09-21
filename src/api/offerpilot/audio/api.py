"""Audio upload and manual transcript endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from offerpilot.audio.storage import (
    MAX_AUDIO_SIZE,
    complete_audio_upload,
    create_audio_upload,
    detect_audio_type,
    finalize_audio_upload,
    public_audio_params,
    resolve_uploaded_audio,
    validate_audio,
    validate_audio_signature,
)
from offerpilot.profiles.cookies import require_profile_id
from offerpilot.sessions.guards import require_active_session
from offerpilot.sessions.repository import add_message

router = APIRouter(prefix="/api", tags=["audio"])
_UPLOAD_CHUNK_SIZE = 64 * 1024


class TranscriptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transcript: str = Field(min_length=1, max_length=12000)


@router.post("/sessions/{session_id}/audio-uploads")
async def upload_audio_endpoint(
    session_id: str,
    file: UploadFile = File(...),
    profile_id: str = Depends(require_profile_id),
):
    require_active_session(session_id, profile_id)
    declared_content_type = (file.content_type or "").lower()
    header = await file.read(16)
    await file.seek(0)
    file.file.seek(0, 2)
    declared_size = file.file.tell()
    file.file.seek(0)
    detected_content_type = detect_audio_type(header)
    valid, reason = validate_audio_signature(declared_content_type, header)
    if not valid:
        raise HTTPException(status_code=400, detail=reason)
    if detected_content_type is None:
        raise HTTPException(status_code=400, detail="音频文件格式不支持")
    content_type = detected_content_type
    valid, reason = validate_audio(content_type, declared_size)
    if not valid:
        raise HTTPException(status_code=400, detail=reason)
    upload = create_audio_upload(
        session_id=session_id,
        profile_id=profile_id,
        original_filename=file.filename or "audio",
        content_type=content_type,
    )
    upload_id = upload["id"]
    try:
        written = 0
        with resolve_uploaded_audio(upload["storage_name"]).open("wb") as stored:
            while True:
                chunk = await file.read(min(_UPLOAD_CHUNK_SIZE, MAX_AUDIO_SIZE - written + 1))
                if not chunk:
                    break
                if written + len(chunk) > MAX_AUDIO_SIZE:
                    raise HTTPException(status_code=400, detail="文件大小超过限制 (25MB)")
                stored.write(chunk)
                written += len(chunk)
        valid, reason = validate_audio(content_type, written)
        if not valid:
            raise HTTPException(status_code=400, detail=reason)
        completed_upload = complete_audio_upload(upload_id, session_id, profile_id, written)
        if completed_upload is None:
            raise RuntimeError("audio_upload_unavailable")
        upload = completed_upload
    except asyncio.CancelledError:
        finalize_audio_upload(upload_id, session_id, profile_id, "failed", error="audio_upload_cancelled")
        raise
    except Exception:
        try:
            finalize_audio_upload(upload_id, session_id, profile_id, "failed", error="audio_upload_failed")
        finally:
            raise
    return {
        "upload": upload,
        "public": public_audio_params(
            filename=file.filename,
            content_type=content_type,
            size=int(upload["size_bytes"]),
        ),
    }


@router.post("/sessions/{session_id}/transcript")
async def save_manual_transcript(
    session_id: str,
    body: TranscriptRequest,
    profile_id: str = Depends(require_profile_id),
):
    require_active_session(session_id, profile_id)
    saved = add_message(session_id, "user", body.transcript.strip(), kind="audio_transcript")
    return {"message": saved}
