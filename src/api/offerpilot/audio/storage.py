"""Managed temporary audio uploads and ASR transcription."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from offerpilot.core.config import settings
from offerpilot.database.connection import get_db
from offerpilot.runs.operation_logs import write_log

ALLOWED_AUDIO_TYPES = {
    "audio/wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wave": ".wav",
    "audio/x-wav": ".wav",
}
MAX_AUDIO_SIZE = 25 * 1024 * 1024
_STORAGE_NAME = re.compile(r"^[0-9a-f]{32}\.(?:wav|mp3)$")
_AUDIO_TERMINAL_STATES = {"completed", "failed", "cancelled", "expired", "deleted"}


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


def detect_audio_type(header: bytes) -> str | None:
    """Detect the supported audio format from bytes, not from a MIME claim."""
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return "audio/wav"
    is_id3 = len(header) >= 3 and header[:3] == b"ID3"
    is_mpeg_frame = len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0
    if is_id3 or is_mpeg_frame:
        return "audio/mpeg"
    return None


def validate_audio_signature(content_type: str | None, header: bytes) -> tuple[bool, str]:
    """Verify the file signature and reject a recognized MIME mismatch."""
    detected = detect_audio_type(header)
    if detected is None:
        return False, "音频文件头无效或格式不支持"
    declared = (content_type or "").lower()
    if declared in ALLOWED_AUDIO_TYPES:
        declared_group = "audio/wav" if declared in {"audio/wav", "audio/wave", "audio/x-wav"} else "audio/mpeg"
        if declared_group != detected:
            return False, "文件内容与声明的音频格式不一致"
    return True, ""


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


def list_session_storage_names(session_id: str, profile_id: str) -> list[str]:
    """Return only managed upload names for Session deletion cleanup."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT storage_name FROM audio_uploads WHERE session_id = ? AND profile_id = ?",
            (session_id, profile_id),
        ).fetchall()
        return [str(row["storage_name"]) for row in rows]
    finally:
        conn.close()


def list_profile_storage_names(profile_id: str) -> list[str]:
    """Return only managed upload names for Profile reset cleanup."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT storage_name FROM audio_uploads WHERE profile_id = ?", (profile_id,)
        ).fetchall()
        return [str(row["storage_name"]) for row in rows]
    finally:
        conn.close()


def begin_audio_transcription(
    upload_id: str,
    session_id: str,
    profile_id: str,
    *,
    run_id: str,
) -> dict[str, Any] | None:
    """Atomically claim a completed upload for the one ASR attempt."""
    now = _now()
    conn = get_db()
    try:
        cursor = conn.execute(
            """
            UPDATE audio_uploads SET status = 'transcribing', run_id = ?, updated_at = ?
            WHERE id = ? AND session_id = ? AND profile_id = ? AND status = 'pending_approval'
              AND EXISTS(
                  SELECT 1 FROM runs
                  WHERE runs.id = ? AND runs.session_id = ? AND runs.profile_id = ?
                    AND runs.status = 'running' AND runs.cancel_requested_at = ''
              )
            """,
            (run_id, now, upload_id, session_id, profile_id, run_id, session_id, profile_id),
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
    run_id: str | None = None,
) -> bool:
    """Delete bytes only through the stored generated name and retain terminal metadata."""
    if status not in _AUDIO_TERMINAL_STATES:
        raise ValueError("Invalid terminal audio upload status")
    upload = get_audio_upload(upload_id, session_id, profile_id)
    if upload is None:
        return False
    deleted_at = ""
    try:
        delete_uploaded_audio(upload["storage_name"])
        deleted_at = _now()
    except ValueError:
        status = "failed"
        error = "unsafe managed storage name"
    except OSError:
        error = "audio_cleanup_failed"
        write_log(
            "audio_cleanup_failed",
            f"Audio upload cleanup failed: {upload_id}",
            profile_id=profile_id,
            session_id=session_id,
            run_id=run_id,
            metadata={"error_code": error, "status": status},
            level="error",
        )
    now = _now()
    conn = get_db()
    try:
        conn.execute(
            """
            UPDATE audio_uploads SET status = ?, updated_at = ?, deleted_at = ?, error = ?
            WHERE id = ? AND session_id = ? AND profile_id = ?
            """,
            (status, now, deleted_at, error[:500], upload_id, session_id, profile_id),
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
            run_id=str(approval.get("run_id") or "") or None,
        ):
            cleaned += 1
    return cleaned


def cleanup_stale_uploads(*, age_seconds: int = 300) -> int:
    """Remove abandoned upload bytes left by an interrupted HTTP upload."""
    cutoff = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - max(1, age_seconds), timezone.utc).isoformat()
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, session_id, profile_id FROM audio_uploads "
            "WHERE status IN ('uploading', 'transcribing') AND updated_at <= ?",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()
    cleaned = 0
    for row in rows:
        if finalize_audio_upload(
            str(row["id"]),
            str(row["session_id"]),
            str(row["profile_id"]),
            "failed",
            error="stale_upload_cleanup",
        ):
            cleaned += 1
    return cleaned


def _audio_upload_row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "profile_id": row["profile_id"],
        "run_id": row["run_id"],
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
