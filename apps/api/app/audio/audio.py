"""Audio upload and ASR transcription module.

Supports wav and mp3 uploads, validates format, calls ASR provider,
and saves transcripts. Requires permission approval (medium risk).
"""

import base64
import mimetypes
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

    Calls MiMo's chat-completions ASR protocol. Returns dict with transcript
    and metadata.

    Note: This requires permission approval (medium risk) before calling.
    """
    try:
        if not settings.mimo_api_key.strip():
            raise RuntimeError("MiMo ASR is not configured")

        import httpx

        audio_bytes = Path(filepath).read_bytes()
        mime_type = mimetypes.guess_type(filepath)[0] or "audio/wav"
        audio_data = base64.b64encode(audio_bytes).decode("ascii")
        payload = {
            "model": settings.mimo_asr_model,
            "messages": [{
                "role": "user",
                "content": [{
                    "type": "input_audio",
                    "input_audio": {"data": f"data:{mime_type};base64,{audio_data}"},
                }],
            }],
            "asr_options": {"language": "auto"},
        }
        headers = {
            "api-key": settings.mimo_api_key,
            "Authorization": f"Bearer {settings.mimo_api_key}",
        }
        response = httpx.post(
            f"{settings.mimo_base_url.rstrip('/')}/chat/completions",
            headers=headers,
            json=payload,
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        transcript = _extract_mimo_transcript(data)
        if not transcript:
            raise RuntimeError("MiMo ASR returned an empty transcript")

        return {
            "transcript": transcript,
            "provider": "mimo",
            "duration_seconds": data.get("duration", 0),
            "language": data.get("language", "auto"),
        }
    except Exception as e:
        return {
            "transcript": "",
            "error": str(e),
            "provider": "mimo",
        }


def _extract_mimo_transcript(data: dict) -> str:
    """Read the response shapes documented by MiMo ASR."""
    direct = data.get("text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    content = (choices[0].get("message") or {}).get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("text")
        ).strip()
    return ""


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
