# api/sse.py — the SSE framing vocabulary every stream router shares: the
# frame encoder, the idle beat, the cadences, the change-detection dump, and the
# one hop that keeps a poll off the event loop.
#
# A frame body is a MODEL (api/common/models/streams/, and the dashboard's own
# response models for the big ones), never a dict assembled at the call site:
# the streams carry the same shapes the routes do, and there is one encoder for
# both.
from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

Frame = TypeVar("Frame")

STREAM_POLL_SECONDS = 0.25
STREAM_HEARTBEAT_SECONDS = 15.0
BEAT = ": beat\n\n"
EVENT_STREAM = "text/event-stream"
NO_STORE = {"Cache-Control": "no-store"}


def sse_frame(event: str, payload: BaseModel, identity: int | None = None) -> str:
    """One frame. `identity` becomes the SSE `id:`, which the browser sends back
    as Last-Event-ID — so only a stream whose frames ARE a cursor sets it."""
    prefix = "" if identity is None else "id: %d\n" % identity
    return "%sevent: %s\ndata: %s\n\n" % (prefix, event, payload.model_dump_json())


async def off_loop(
    read: Callable[..., Frame],
    *arguments: object,  # loose: generic forwarding of a store read's own arguments
) -> Frame:
    """One synchronous store read, on a worker thread.

    THE POLL DESIGN DEPENDS ON THIS. Every frame in every stream comes from a
    blocking SQLite read, and a stream is an async generator — so calling one
    directly stalls the whole event loop, and therefore every other stream and
    every request, for the length of that query. Once per client per
    STREAM_POLL_SECONDS. Nothing about it fails visibly; the server just gets
    slower the more of it you watch, which is why the streams are the one place
    in api/ that has to say this out loud instead of relying on FastAPI running
    a `def` handler in the pool for free.

    The thread is borrowed for the read and returned before the sleep, so an idle
    stream still costs no thread — the property the design was claiming all along
    (api/config.py THREAD_POOL_TOKENS).
    """
    return await run_in_threadpool(read, *arguments)
