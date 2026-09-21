"""Small serialization and timestamp helpers shared by SQLite repositories."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def expires(hours: int = 24) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def loads(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
