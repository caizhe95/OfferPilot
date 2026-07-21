"""Audio upload and ASR transcription module.

Supports wav and mp3 uploads, validates format, calls ASR provider,
and saves transcripts. Requires permission approval (medium risk).
"""

import os
import uuid
from pathlib import Path
from datetime import datetime, timezone
from app.core.config import settings
from app.core.database import get_db

ALLOWED_AUDIO_TYPES = {
    "audio/wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wave": ".wav",
    "audio/x-wav": ".wav",
}

MAX_AUDIO_SIZE = 25 * 1024 * 1024  # 25 MB


def validate_audio(content_type: str, file_size: int) -> tuple[bool, str]:
    """Validate audio file format and size.

    Returns (is_valid, error_message).
    """
    if content_type not in ALLOWED_AUDIO_TYPES:
        supported = ", ".join(ALLOWED_AUDIO_TYPES.keys())
        return False, f"不支持的文件格式。支持: {supported}"

    if file_size > MAX_AUDIO_SIZE:
        return False, f"文件大小超过限制 ({MAX_AUDIO_SIZE // (1024*1024)}MB)"

    if file_size == 0:
        return False, "文件为空"

    return True, ""


def save_audio_file(content: bytes, original_filename: str, content_type: str) -> str:
    """Save uploaded audio to disk.

    Returns the saved file path.
    """
    upload_dir = Path(settings.sqlite_path).parent / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    ext = ALLOWED_AUDIO_TYPES.get(content_type, ".bin")
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = upload_dir / filename

    filepath.write_bytes(content)
    return str(filepath)


async def transcribe_audio(filepath: str) -> dict:
    """Transcribe audio using ASR provider.

    Currently supports OpenAI Whisper. Returns dict with transcript and metadata.

    Note: This requires permission approval (medium risk) before calling.
    """
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )

        with open(filepath, "rb") as audio_file:
            response = client.audio.transcriptions.create(
                model=settings.asr_model,
                file=audio_file,
                response_format="verbose_json",
            )

        return {
            "transcript": response.text,
            "provider": settings.asr_provider,
            "duration_seconds": getattr(response, "duration", 0),
            "language": getattr(response, "language", "unknown"),
        }
    except Exception as e:
        return {
            "transcript": "",
            "error": str(e),
            "provider": settings.asr_provider,
        }


def save_transcript_to_session(session_id: str, transcript: str, audio_path: str) -> dict:
    """Save transcript to the session as a message and in the database."""
    conn = get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, "user", f"[Audio Transcript]\n{transcript}", now),
        )
        conn.commit()
        return {
            "session_id": session_id,
            "transcript": transcript,
            "audio_path": audio_path,
            "saved_at": now,
        }
    finally:
        conn.close()
