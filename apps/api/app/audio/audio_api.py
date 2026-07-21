"""Audio upload and ASR API endpoints."""

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from app.audio.audio import (
    validate_audio,
    save_audio_file,
    transcribe_audio,
    save_transcript_to_session,
)
from app.core.api_helpers import permission_required_response
from app.permission.permission import permission_gate, write_audit_log

router = APIRouter(prefix="/api/audio", tags=["audio"])


@router.post("/upload")
async def upload_audio(
    session_id: str = Form(...),
    file: UploadFile = File(...),
):
    """Upload an audio file for transcription.

    File must be wav or mp3 format, max 25MB.
    Requires permission approval for ASR (medium risk).
    """
    # Validate file
    if not file.content_type:
        raise HTTPException(status_code=400, detail="无法识别文件类型")

    content = await file.read()
    is_valid, error_msg = validate_audio(file.content_type or "", len(content))
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)

    # Save first so approval resume can transcribe the exact uploaded file.
    filepath = save_audio_file(content, file.filename or "audio.wav", file.content_type)
    params = {
        "filepath": filepath,
        "filename": file.filename,
        "content_type": file.content_type,
        "size": len(content),
    }

    # Check permission before calling ASR.
    permission = permission_gate.check(session_id, "transcribe_audio", params)
    if not permission["allowed"]:
        event = permission_required_response(
            session_id=session_id,
            tool_name="transcribe_audio",
            message="音频转写需要用户确认，批准后才会调用 ASR 服务",
            permission_result=permission,
            params=params,
        )
        event["status"] = "approval_required"
        return event

    # Transcribe
    result = await transcribe_audio(filepath)

    if "error" in result:
        return {
            "status": "asr_failed",
            "error": result["error"],
            "message": "ASR 转写失败，请手动粘贴 transcript",
        }

    # Save transcript
    saved = save_transcript_to_session(session_id, result["transcript"], filepath)

    write_audit_log(
        session_id=session_id,
        tool_name="transcribe_audio",
        risk_level="medium",
        action="execute",
        params={"filename": file.filename},
        result=f"Transcript length: {len(result['transcript'])} chars",
    )

    return {
        "status": "transcribed",
        "transcript": result["transcript"],
        "provider": result["provider"],
        "duration_seconds": result.get("duration_seconds", 0),
        "language": result.get("language", "unknown"),
        "saved": saved,
    }


@router.post("/transcript/manual")
async def manual_transcript(
    session_id: str = Form(...),
    transcript: str = Form(...),
):
    """Submit a manual transcript (fallback when ASR fails or is denied)."""
    if not transcript.strip():
        raise HTTPException(status_code=400, detail="Transcript must not be empty")

    import uuid
    audio_path = f"manual://{uuid.uuid4().hex}"

    saved = save_transcript_to_session(session_id, transcript.strip(), audio_path)

    return {
        "status": "saved",
        "transcript": transcript,
        "saved": saved,
    }


@router.get("/supported-formats")
async def supported_formats():
    """List supported audio formats."""
    from app.audio.audio import ALLOWED_AUDIO_TYPES
    return {
        "formats": [
            {"mime": mime, "extension": ext}
            for mime, ext in ALLOWED_AUDIO_TYPES.items()
        ],
        "max_size_mb": 25,
    }
