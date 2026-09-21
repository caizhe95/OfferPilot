"""Session lifecycle coordination and storage cleanup."""

from __future__ import annotations

from offerpilot.audio.storage import delete_uploaded_audio, list_session_storage_names
from offerpilot.profiles.growth import rebuild_profile_growth
from offerpilot.sessions import repository


def delete_session_with_assets(session_id: str, profile_id: str) -> bool:
    if repository.get_session(session_id, profile_id) is None:
        return False
    for storage_name in list_session_storage_names(session_id, profile_id):
        try:
            delete_uploaded_audio(storage_name)
        except (OSError, ValueError):
            pass
    deleted = repository.delete_session_record(session_id, profile_id)
    if deleted:
        rebuild_profile_growth(profile_id)
    return deleted
