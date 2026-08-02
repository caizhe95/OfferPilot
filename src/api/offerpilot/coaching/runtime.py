"""In-process cancellation signals keyed by the durable run identity."""

from __future__ import annotations

import asyncio

_ACTIVE_RUNS: dict[tuple[str, str], asyncio.Event] = {}


def register_run(session_id: str, trace_id: str) -> asyncio.Event:
    event = asyncio.Event()
    _ACTIVE_RUNS[(session_id, trace_id)] = event
    return event


def cancel_active_run(session_id: str, trace_id: str) -> bool:
    event = _ACTIVE_RUNS.get((session_id, trace_id))
    if event is None:
        return False
    event.set()
    return True


def release_run(session_id: str, trace_id: str) -> None:
    _ACTIVE_RUNS.pop((session_id, trace_id), None)
