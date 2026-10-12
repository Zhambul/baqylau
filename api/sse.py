# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide the SSE module."""

# api/sse.py — the SSE framing vocabulary every stream router shares: the
# frame encoder, the idle beat, and the worker call for blocking reads.
#
# A frame body is a MODEL (api/common/models/streams/, and the dashboard's own
# response models for the big ones), never a dict assembled at the call site:
# the streams carry the same shapes the routes do, and there is one encoder for
# both.
from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, ParamSpec, TypeVar

from fastapi.concurrency import run_in_threadpool

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

Frame = TypeVar("Frame")
StoreReadParams = ParamSpec("StoreReadParams")

STREAM_HEARTBEAT_SECONDS = 15.0
BEAT = ": beat\n\n"
EVENT_STREAM = "text/event-stream"
NO_STORE = MappingProxyType({"Cache-Control": "no-store"})


def heartbeat_wait(heartbeat_at: float, now: float) -> float:
    """Return the seconds until the next heartbeat is due.

    A stream waits for a change notice at most this long. A notice that brings
    no frame does not move the heartbeat, so a quiet stream beats on time while
    other sessions change.

    Returns:
        The seconds left, or zero when the heartbeat is due.

    """
    return max(heartbeat_at + STREAM_HEARTBEAT_SECONDS - now, 0)


def sse_frame(event: str, payload: BaseModel, identity: int | None = None) -> str:
    """Return the SSE frame.

    One frame. `identity` becomes the SSE `id:`, which the browser sends back
        as Last-Event-ID — so only a stream whose frames ARE a cursor sets it.
        The body uses the field aliases, as a route response does, so one model
        has one shape on both.

    Returns:
        SSE frame.

    """
    prefix = "" if identity is None else f"id: {int(identity)}\n"
    return f"{prefix}event: {event}\ndata: {payload.model_dump_json(by_alias=True)}\n\n"


async def off_loop[**StoreReadParams, Frame](
    read: Callable[StoreReadParams, Frame],
    *arguments: StoreReadParams.args,
    **keywords: StoreReadParams.kwargs,
) -> Frame:
    """Run one synchronous store read on a worker thread.

    A blocking read must not stop the async event loop. The worker thread is
    released before the stream waits for its next change notice.

    Returns:
        The frame.

    """
    return await run_in_threadpool(read, *arguments, **keywords)
