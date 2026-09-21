"""Profile-wide cleanup coordination outside repository code."""

from __future__ import annotations

from offerpilot.audio.storage import delete_uploaded_audio, list_profile_storage_names
from offerpilot.profiles.repository import delete_profile_record


def reset_profile_with_assets(profile_id: str) -> None:
    for storage_name in list_profile_storage_names(profile_id):
        try:
            delete_uploaded_audio(storage_name)
        except (OSError, ValueError):
            pass
    delete_profile_record(profile_id)
