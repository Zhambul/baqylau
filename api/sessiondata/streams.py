# api/sessiondata/streams.py — the two SSE surfaces, and they are one loop twice.
#
# Every 0.25 s: ask the read model what changed after the client's cursor. The
# global stream also checks one in-memory application revision. It reads the
# application snapshot on connect and only after that revision changes. There
# is no broker, subscription registry, or per-client buffer. A slow client
# delays only its own generator, and its next poll returns current state.
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

from api.application.mapper import preferences as application_mapper
from api.common.models.fields import SessionIdPath
from api.common.models.streams.error_frame import ErrorFrame
from api.common.models.streams.ready_frame import ReadyFrame
from api.dependencies import Policy
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
from app.providers import (
    ApplicationPreferences,
    ApplicationUpdates,
    Recorder,
    SessionApplication,
    SessionDataStore,
)
from audit.recorder import AuditRecorder
from audit.models import PathAudit, SessionAudit
from domain.ids import SessionId
from repository.contract.session_data import SessionDataRepository
from dashboard.services.workspace import SessionApplicationService

router = APIRouter()
SESSION_APPLICATION_POLL_SECONDS = 1.0


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
    session_application_service: SessionApplication,
    after_cursor: int = 0,
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
) -> StreamingResponse:
    return StreamingResponse(
        _session_frames(
            read_model,
            audit,
            SessionId(session_id),
            _from_cursor(last_event_id, after_cursor),
            session_application_service,
        ),
        media_type=EVENT_STREAM,
        headers=NO_STORE,
    )


@router.get("/sessionData/stream")
def global_stream(
    read_model: SessionDataStore,
    audit: Recorder,
    policy: Policy,
    application_preferences: ApplicationPreferences,
    application_updates: ApplicationUpdates,
    after_cursor: int = 0,
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
) -> StreamingResponse:
    return StreamingResponse(
        _global_frames(
            read_model,
            audit,
            _from_cursor(last_event_id, after_cursor),
            policy.boot_id,
            application_preferences,
            application_updates,
        ),
        media_type=EVENT_STREAM,
        headers=NO_STORE,
    )


async def _session_frames(
    session_data_repository: SessionDataRepository,
    audit_recorder: AuditRecorder,
    session_id: SessionId,
    cursor: int,
    session_application_service: SessionApplicationService | None = None,
) -> AsyncIterator[str]:
    try:
        application = (
            None
            if session_application_service is None
            else await off_loop(session_application_service.snapshot, session_id)
        )
        if application is not None:
            yield sse_frame(
                "application",
                application_mapper.session_application(application),
            )
        heartbeat_at = asyncio.get_running_loop().time()
        application_read_at = heartbeat_at
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
            if (
                session_application_service is not None
                and now - application_read_at >= SESSION_APPLICATION_POLL_SECONDS
            ):
                next_application = await off_loop(session_application_service.snapshot, session_id)
                application_read_at = now
                if next_application != application:
                    application = next_application
                    yield sse_frame(
                        "application",
                        application_mapper.session_application(application),
                    )
                    heartbeat_at = now
            elif delta.empty and now - heartbeat_at >= STREAM_HEARTBEAT_SECONDS:
                yield BEAT
                heartbeat_at = now
            await asyncio.sleep(STREAM_POLL_SECONDS)
    except (asyncio.CancelledError, GeneratorExit):
        raise
    except Exception:
        # An SSE stream drives the whole view; it must not die silently. The
        # audit row is the trace, the error frame is the client's signal, and the
        # connection ends so the client reconnects.
        audit_recorder.error(
            str(session_id),
            "session data stream",
            SessionAudit(session_id=session_id),
        )
        yield sse_frame("error", ErrorFrame(error="stream failed"))


async def _global_frames(
    session_data_repository: SessionDataRepository,
    audit_recorder: AuditRecorder,
    cursor: int,
    boot_id: str,
    application_preferences: ApplicationPreferences,
    application_updates: ApplicationUpdates,
) -> AsyncIterator[str]:
    yield sse_frame("ready", ReadyFrame(boot_id=boot_id))
    try:
        application_revision = application_updates.revision()
        application = await off_loop(application_preferences.snapshot)
        yield sse_frame(
            "application",
            application_mapper.global_application(application),
        )
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
            next_application_revision = application_updates.revision()
            if next_application_revision != application_revision:
                application_revision = next_application_revision
                application = await off_loop(application_preferences.snapshot)
                yield sse_frame(
                    "application",
                    application_mapper.global_application(application),
                )
                heartbeat_at = now
            elif delta.empty and now - heartbeat_at >= STREAM_HEARTBEAT_SECONDS:
                yield BEAT
                heartbeat_at = now
            await asyncio.sleep(STREAM_POLL_SECONDS)
    except (asyncio.CancelledError, GeneratorExit):
        raise
    except Exception:
        audit_recorder.error(
            "", "global stream", PathAudit(path="/sessionData/stream")
        )
        yield sse_frame("error", ErrorFrame(error="stream failed"))
