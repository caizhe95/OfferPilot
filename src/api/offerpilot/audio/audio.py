"""Managed temporary audio uploads and ASR transcription."""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from offerpilot.core.config import settings
from offerpilot.core.deadline import await_with_deadline, require_remaining
from offerpilot.core.database import get_db

ALLOWED_AUDIO_TYPES = {
    "audio/wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wave": ".wav",
    "audio/x-wav": ".wav",
}
MAX_AUDIO_SIZE = 25 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024
ASR_TIMEOUT_SECONDS = 30.0
_STORAGE_NAME = re.compile(r"^[0-9a-f]{32}\.(?:wav|mp3)$")
_AUDIO_TERMINAL_STATES = {"completed", "failed", "denied", "expired", "deleted"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upload_directory() -> Path:
    """Return the only directory allowed to hold temporary audio uploads."""
    directory = settings.db_path.parent / "uploads"
    directory.mkdir(parents=True, exist_ok=True)
    return directory.resolve()


def resolve_uploaded_audio(storage_name: str) -> Path:
    """Resolve a generated storage name without accepting paths or traversal input."""
    if not isinstance(storage_name, str) or not _STORAGE_NAME.fullmatch(storage_name):
        raise ValueError("Invalid managed audio storage name")
    path = (upload_directory() / storage_name).resolve(strict=False)
    if path.parent != upload_directory() or path.name != storage_name:
        raise ValueError("Audio file is outside the managed upload directory")
    return path


def delete_uploaded_audio(storage_name: str) -> bool:
    """Delete a generated upload name; callers cannot provide an arbitrary path."""
    resolve_uploaded_audio(storage_name).unlink(missing_ok=True)
    return True


def public_audio_params(*, filename: str | None, content_type: str | None, size: int) -> dict[str, Any]:
    """Return the approval metadata that is safe to expose to a browser."""
    return {
        "filename": Path(filename or "audio").name[:255],
        "content_type": content_type or "application/octet-stream",
        "size": size,
    }


def validate_audio(content_type: str, file_size: int) -> tuple[bool, str]:
    if content_type not in ALLOWED_AUDIO_TYPES:
        return False, f"不支持的文件格式。支持: {', '.join(ALLOWED_AUDIO_TYPES)}"
    if file_size > MAX_AUDIO_SIZE:
        return False, f"文件大小超过限制 ({MAX_AUDIO_SIZE // (1024 * 1024)}MB)"
    if file_size == 0:
        return False, "文件为空"
    return True, ""


def validate_audio_signature(content_type: str, header: bytes) -> tuple[bool, str]:
    """Verify the actual file signature instead of trusting the MIME header."""
    if content_type in {"audio/wav", "audio/wave", "audio/x-wav"}:
        if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE":
            return True, ""
        return False, "WAV 文件头无效"
    if content_type in {"audio/mpeg", "audio/mp3"}:
        is_id3 = len(header) >= 3 and header[:3] == b"ID3"
        is_mpeg_frame = len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0
        if is_id3 or is_mpeg_frame:
            return True, ""
        return False, "MP3 文件头无效"
    return False, "不支持的文件格式"


def save_audio_file(content: bytes, original_filename: str, content_type: str) -> str:
    """Test-only convenience for a generated managed storage name.

    Production uploads must use ``create_audio_upload`` and a row in
    ``audio_uploads`` before writing data.
    """
    _ = original_filename
    storage_name = f"{uuid.uuid4().hex}{ALLOWED_AUDIO_TYPES.get(content_type, '.bin')}"
    resolve_uploaded_audio(storage_name).write_bytes(content)
    return storage_name


def create_audio_upload(
    *,
    session_id: str,
    profile_id: str,
    original_filename: str,
    content_type: str,
) -> dict[str, Any]:
    """Reserve a server-generated storage name before the first chunk is written."""
    if content_type not in ALLOWED_AUDIO_TYPES:
        raise ValueError("Unsupported audio content type")
    upload_id = str(uuid.uuid4())
    storage_name = f"{uuid.uuid4().hex}{ALLOWED_AUDIO_TYPES[content_type]}"
    now = _now()
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO audio_uploads
                (id, session_id, profile_id, storage_name, original_filename, content_type, size_bytes, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, 'uploading', ?, ?)
            """,
            (upload_id, session_id, profile_id, storage_name, Path(original_filename or "audio").name[:255], content_type, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "id": upload_id,
        "session_id": session_id,
        "profile_id": profile_id,
        "storage_name": storage_name,
        "original_filename": Path(original_filename or "audio").name[:255],
        "content_type": content_type,
        "size_bytes": 0,
        "status": "uploading",
        "created_at": now,
        "updated_at": now,
        "deleted_at": "",
        "error": "",
    }


def complete_audio_upload(upload_id: str, session_id: str, profile_id: str, size_bytes: int) -> dict[str, Any] | None:
    """Commit the byte count only if the same owned upload is still uploading."""
    now = _now()
    conn = get_db()
    try:
        cursor = conn.execute(
            """
            UPDATE audio_uploads SET size_bytes = ?, status = 'pending_approval', updated_at = ?
            WHERE id = ? AND session_id = ? AND profile_id = ? AND status = 'uploading'
            """,
            (size_bytes, now, upload_id, session_id, profile_id),
        )
        conn.commit()
        if cursor.rowcount != 1:
            return None
    finally:
        conn.close()
    return get_audio_upload(upload_id, session_id, profile_id)


def get_audio_upload(upload_id: str, session_id: str, profile_id: str) -> dict[str, Any] | None:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM audio_uploads WHERE id = ? AND session_id = ? AND profile_id = ?",
            (upload_id, session_id, profile_id),
        ).fetchone()
        return _audio_upload_row(row) if row else None
    finally:
        conn.close()


def begin_audio_transcription(upload_id: str, session_id: str, profile_id: str) -> dict[str, Any] | None:
    """Atomically claim a completed upload for the one ASR attempt."""
    now = _now()
    conn = get_db()
    try:
        cursor = conn.execute(
            """
            UPDATE audio_uploads SET status = 'transcribing', updated_at = ?
            WHERE id = ? AND session_id = ? AND profile_id = ? AND status = 'pending_approval'
            """,
            (now, upload_id, session_id, profile_id),
        )
        conn.commit()
        if cursor.rowcount != 1:
            return None
    finally:
        conn.close()
    return get_audio_upload(upload_id, session_id, profile_id)


def finalize_audio_upload(
    upload_id: str,
    session_id: str,
    profile_id: str,
    status: str,
    *,
    error: str = "",
) -> bool:
    """Delete bytes only through the stored generated name and retain terminal metadata."""
    if status not in _AUDIO_TERMINAL_STATES:
        raise ValueError("Invalid terminal audio upload status")
    upload = get_audio_upload(upload_id, session_id, profile_id)
    if upload is None:
        return False
    try:
        delete_uploaded_audio(upload["storage_name"])
    except ValueError:
        status = "failed"
        error = "unsafe managed storage name"
    now = _now()
    conn = get_db()
    try:
        conn.execute(
            """
            UPDATE audio_uploads SET status = ?, updated_at = ?, deleted_at = ?, error = ?
            WHERE id = ? AND session_id = ? AND profile_id = ?
            """,
            (status, now, now, error[:500], upload_id, session_id, profile_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def expire_audio_for_approvals(approvals: list[dict[str, Any]]) -> int:
    """Delete temporary bytes for expired Audio approvals and retain their terminal rows."""
    cleaned = 0
    for approval in approvals:
        if approval.get("flow_kind") != "audio":
            continue
        upload_id = approval.get("params", {}).get("upload_id")
        if isinstance(upload_id, str) and finalize_audio_upload(
            upload_id,
            str(approval["session_id"]),
            str(approval["profile_id"]),
            "expired",
            error="approval expired",
        ):
            cleaned += 1
    return cleaned


def expire_legacy_audio_approvals() -> int:
    """Safely retire pre-audio_uploads approvals; only managed paths are removed."""
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT id, params FROM approval_requests
            WHERE flow_kind = 'audio' AND status IN ('pending', 'approved')
              AND params LIKE '%filepath%'
            """
        ).fetchall()
        now = _now()
        for row in rows:
            conn.execute(
                "UPDATE approval_requests SET status = 'expired', resolved_at = ?, error = 'legacy audio approval cannot be verified' WHERE id = ?",
                (now, row["id"]),
            )
        conn.commit()
    finally:
        conn.close()
    for row in rows:
        try:
            params = json_loads_dict(row["params"])
            legacy_path = params.get("filepath")
            if isinstance(legacy_path, str):
                _delete_legacy_managed_path(legacy_path)
        except ValueError:
            continue
    return len(rows)


def _delete_legacy_managed_path(filepath: str) -> None:
    root = upload_directory()
    candidate = Path(filepath).resolve(strict=False)
    if candidate.parent != root:
        raise ValueError("Legacy audio path is outside managed upload directory")
    candidate.unlink(missing_ok=True)


def json_loads_dict(value: str) -> dict[str, Any]:
    import json

    try:
        decoded = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


async def _await_asr_request(
    request: Any,
    cancel_event: asyncio.Event | None,
    deadline: float | None,
) -> Any:
    return await await_with_deadline(
        request, deadline=deadline, cancel_event=cancel_event
    )


def _asr_error_category(exc: Exception) -> str:
    import httpx

    if isinstance(exc, asyncio.TimeoutError):
        return "asr_timeout"
    if isinstance(exc, httpx.TimeoutException):
        return "asr_timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code in {400, 401, 403, 413, 415, 422}:
            return "asr_rejected"
        return "asr_provider"
    if isinstance(exc, httpx.RequestError):
        return "asr_network"
    if isinstance(exc, (ValueError, OSError)):
        return "asr_invalid_input"
    return "asr_provider"


async def _build_asr_payload(
    storage_name: str,
    *,
    deadline: float | None,
    cancel_event: asyncio.Event | None,
) -> dict[str, Any]:
    managed_path = resolve_uploaded_audio(storage_name)
    audio_bytes = await await_with_deadline(
        asyncio.to_thread(managed_path.read_bytes),
        deadline=deadline,
        cancel_event=cancel_event,
    )
    mime_type = mimetypes.guess_type(managed_path.name)[0] or "audio/wav"
    audio_data = await await_with_deadline(
        asyncio.to_thread(base64.b64encode, audio_bytes),
        deadline=deadline,
        cancel_event=cancel_event,
    )
    encoded_audio = audio_data.decode("ascii")
    return {
        "model": settings.mimo_asr_model,
        "messages": [{"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": f"data:{mime_type};base64,{encoded_audio}"}}]}],
        "asr_options": {"language": "auto"},
    }


async def transcribe_audio(
    storage_name: str,
    *,
    cancel_event: asyncio.Event | None = None,
    timeout: float = ASR_TIMEOUT_SECONDS,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Transcribe a server-owned generated upload name via the ASR provider."""
    try:
        if not settings.mimo_api_key.strip():
            return {"transcript": "", "error": "asr_configuration", "provider": "mimo"}
        import httpx

        if cancel_event and cancel_event.is_set():
            return {"transcript": "", "error": "asr_cancelled", "provider": "mimo"}
        timeout = max(0.001, min(float(timeout), ASR_TIMEOUT_SECONDS, require_remaining(deadline)))
        headers = {"api-key": settings.mimo_api_key, "Authorization": f"Bearer {settings.mimo_api_key}"}
        async with asyncio.timeout(timeout):
            payload = await _build_asr_payload(
                storage_name, deadline=deadline, cancel_event=cancel_event
            )
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
                response = await _await_asr_request(
                    client.post(
                        f"{settings.mimo_base_url.rstrip('/')}/chat/completions",
                        headers=headers,
                        json=payload,
                    ),
                    cancel_event,
                    deadline,
                )
                response.raise_for_status()
                data = response.json()
        if not isinstance(data, dict):
            raise ValueError("ASR response is not an object")
        transcript = _extract_mimo_transcript(data)
        if not transcript:
            return {"transcript": "", "error": "asr_invalid_response", "provider": "mimo"}
        return {
            "transcript": transcript,
            "provider": "mimo",
            "duration_seconds": data.get("duration", 0),
            "language": data.get("language", "auto"),
        }
    except asyncio.CancelledError:
        return {"transcript": "", "error": "asr_cancelled", "provider": "mimo"}
    except Exception as exc:
        return {"transcript": "", "error": _asr_error_category(exc), "provider": "mimo"}


def _extract_mimo_transcript(data: dict) -> str:
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
        return "".join(str(item.get("text", "")) for item in content if isinstance(item, dict) and item.get("text")).strip()
    return ""


def save_transcript_to_session(session_id: str, transcript: str, _audio_storage_name: str) -> dict[str, Any]:
    """Save a transcript without ever returning a server file path."""
    conn = get_db()
    try:
        now = _now()
        conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, "user", f"[Audio Transcript]\n{transcript}", now),
        )
        conn.commit()
        return {"session_id": session_id, "transcript": transcript, "saved_at": now}
    finally:
        conn.close()


def _audio_upload_row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "profile_id": row["profile_id"],
        "storage_name": row["storage_name"],
        "original_filename": row["original_filename"],
        "content_type": row["content_type"],
        "size_bytes": row["size_bytes"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "deleted_at": row["deleted_at"],
        "error": row["error"],
    }
