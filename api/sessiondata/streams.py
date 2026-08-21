# api/sessiondata/streams.py — the two SSE surfaces, and they are one loop twice.
#
# Every 0.25 s: ask the read model what changed after the client's cursor, and
# if anything did, send one frame carrying all of it with the highest cursor as
# its id. No broker, no subscription registry, no per-client buffer — a slow
# client delays only its own generator, and its next poll returns a bigger
# batch.
#
# The batching is the same mechanism as the cursor: ten context reports inside
# one poll window are one committed row by the time the poll reads it, so they
# collapse into one actor object in one frame. There is no coalescing rule
# because the poll interval IS the batch boundary.
#
# Every poll goes through `off_loop`, because the reads behind it are blocking
# SQLite and the generator runs ON the event loop — see api/sse.py for what
# calling one directly costs.
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse

from api.common.models.fields import SessionIdPath
from api.common.models.streams.error_frame import ErrorFrame
from api.sessiondata import mapper
from api.sessiondata.models.stream_frame import GlobalStreamFrame, SessionStreamFrame
from api.sse import (
    BEAT,
    EVENT_STREAM,
    NO_STORE,
    STREAM_HEARTBEAT_SECONDS,
    STREAM_POLL_SECONDS,
    off_loop,
    sse_frame,
)
from app.providers import Recorder, SessionDataStore
from audit.recorder import AuditRecorder
from domain.ids import SessionId
from repository.contract.session_data import SessionDataRepository

router = APIRouter()


def _from_cursor(last_event_id: str | None, after_cursor: int) -> int:
    """Where to resume. The browser's `EventSource` sends the header on its own
    after a drop, so a reconnect is the same request as the first one; the query
    parameter is for the clients that are not a browser."""
    if last_event_id is None:
        return after_cursor
    try:
        return int(last_event_id)
    except ValueError:
        return after_cursor


@router.get("/sessionData/{session_id}/stream")
def session_stream(
    session_id: SessionIdPath,
    read_model: SessionDataStore,
    audit: Recorder,
    after_cursor: int = 0,
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
) -> StreamingResponse:
    return StreamingResponse(
        _session_frames(
            read_model, audit, SessionId(session_id), _from_cursor(last_event_id, after_cursor)
        ),
        media_type=EVENT_STREAM,
        headers=NO_STORE,
    )


@router.get("/sessionData/stream")
def global_stream(
    read_model: SessionDataStore,
    audit: Recorder,
    after_cursor: int = 0,
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
) -> StreamingResponse:
    return StreamingResponse(
        _global_frames(read_model, audit, _from_cursor(last_event_id, after_cursor)),
        media_type=EVENT_STREAM,
        headers=NO_STORE,
    )


async def _session_frames(
    session_data_repository: SessionDataRepository,
    audit_recorder: AuditRecorder,
    session_id: SessionId,
    cursor: int,
) -> AsyncIterator[str]:
    try:
        heartbeat_at = asyncio.get_running_loop().time()
        while True:
            delta = await off_loop(session_data_repository.delta, session_id, cursor)
            now = asyncio.get_running_loop().time()
            if not delta.empty:
                # The frame's id is the highest revision the read SAW, which is
                # what the client sends back — so an aggregate-only change
                # advances the cursor too, instead of being re-sent every poll
                # for the life of the connection.
                yield sse_frame(
                    "sessionData",
                    SessionStreamFrame(
                        session=(
                            None if delta.session is None else mapper.session(delta.session)
                        ),
                        actors=tuple(mapper.actor(row) for row in delta.actors),
                        entries=tuple(mapper.entry(item) for item in delta.entries),
                    ),
                    delta.cursor,
                )
                cursor = delta.cursor
                heartbeat_at = now
            elif now - heartbeat_at >= STREAM_HEARTBEAT_SECONDS:
                yield BEAT
                heartbeat_at = now
            await asyncio.sleep(STREAM_POLL_SECONDS)
    except (asyncio.CancelledError, GeneratorExit):
        raise
    except Exception:
        # An SSE stream drives the whole view; it must not die silently. The
        # audit row is the trace, the error frame is the client's signal, and the
        # connection ends so the client reconnects.
        audit_recorder.error(str(session_id), "session data stream", {"session_id": str(session_id)})
        yield sse_frame("error", ErrorFrame(error="stream failed"))


async def _global_frames(
    session_data_repository: SessionDataRepository,
    audit_recorder: AuditRecorder,
    cursor: int,
) -> AsyncIterator[str]:
    try:
        heartbeat_at = asyncio.get_running_loop().time()
        while True:
            delta = await off_loop(session_data_repository.changed_after, cursor)
            now = asyncio.get_running_loop().time()
            if not delta.empty:
                yield sse_frame(
                    "sessionData",
                    GlobalStreamFrame(
                        sessions=tuple(mapper.session(facts) for facts in delta.sessions),
                        actors=tuple(mapper.actor(row) for row in delta.actors),
                    ),
                    delta.cursor,
                )
                cursor = delta.cursor
                heartbeat_at = now
            elif now - heartbeat_at >= STREAM_HEARTBEAT_SECONDS:
                yield BEAT
                heartbeat_at = now
            await asyncio.sleep(STREAM_POLL_SECONDS)
    except (asyncio.CancelledError, GeneratorExit):
        raise
    except Exception:
        audit_recorder.error("", "session data stream", {"path": "/sessionData/stream"})
        yield sse_frame("error", ErrorFrame(error="stream failed"))
