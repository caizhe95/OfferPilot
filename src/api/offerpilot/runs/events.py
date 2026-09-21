"""Small in-process wake-up broker; SQLite remains the event source of truth."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import AsyncIterator

from offerpilot.runs.repository import events_after, get_run

_signals: dict[str, asyncio.Event] = defaultdict(asyncio.Event)


def notify(run_id: str) -> None:
    _signals[run_id].set()


async def stream(run_id: str, after: int = 0, heartbeat_seconds: float = 15.0) -> AsyncIterator[dict]:
    sequence = max(0, after)
    while True:
        signal = _signals[run_id]
        signal.clear()
        events = events_after(run_id, sequence)
        if events:
            for event in events:
                sequence = event["sequence"]
                yield event
            continue
        run = get_run(run_id)
        if run is None:
            return
        if run["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            return
        try:
            await asyncio.wait_for(signal.wait(), timeout=max(0.5, heartbeat_seconds))
        except TimeoutError:
            yield {
                "type": "ping",
                "session_id": run["session_id"],
                "trace_id": run.get("trace_id", run_id),
                "run_id": run_id,
                "sequence": sequence,
                "created_at": "",
                "data": {},
            }
