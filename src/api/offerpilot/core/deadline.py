"""Shared absolute-deadline and cancellation helpers for active runs."""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from typing import Awaitable, TypeVar


T = TypeVar("T")


class RunDeadlineExceeded(TimeoutError):
    """The active run has exhausted its absolute time budget."""


def deadline_after(timeout_seconds: float) -> float:
    return time.monotonic() + max(0.001, timeout_seconds)


def remaining_seconds(deadline: float | None) -> float:
    if deadline is None:
        return float("inf")
    return deadline - time.monotonic()


def require_remaining(deadline: float | None) -> float:
    remaining = remaining_seconds(deadline)
    if remaining <= 0:
        raise RunDeadlineExceeded("Active run budget exhausted")
    return remaining


def raise_if_cancelled(cancel_event: asyncio.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise asyncio.CancelledError()


async def await_with_deadline(
    operation: Awaitable[T],
    *,
    deadline: float | None,
    cancel_event: asyncio.Event | None = None,
) -> T:
    """Await one operation without exceeding the shared deadline or cancellation."""
    raise_if_cancelled(cancel_event)
    task = asyncio.ensure_future(operation)
    cancel_task: asyncio.Task[bool] | None = None
    try:
        if cancel_event is None:
            try:
                return await asyncio.wait_for(task, timeout=require_remaining(deadline))
            except TimeoutError as exc:
                task.cancel()
                raise RunDeadlineExceeded("Active run budget exhausted") from exc

        cancel_task = asyncio.create_task(cancel_event.wait())
        done, _ = await asyncio.wait(
            {task, cancel_task},
            timeout=require_remaining(deadline),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if cancel_task in done:
            task.cancel()
            raise asyncio.CancelledError()
        if task in done:
            return await task
        task.cancel()
        raise RunDeadlineExceeded("Active run budget exhausted")
    finally:
        if cancel_task is not None:
            cancel_task.cancel()
            with suppress(asyncio.CancelledError):
                await cancel_task
        if task.cancelled():
            with suppress(asyncio.CancelledError):
                await task
