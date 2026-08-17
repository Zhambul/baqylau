# api/dashboard/streams.py — the browser's two SSE surfaces: the global
# application stream and the one-cursor session stream.
#
# Deliberately the same design as always: one direct poll path over the
# canonical store and the application snapshots — no broker, no subscription
# registry, no event bus. Each stream is an async generator, so an idle open
# connection costs no worker thread; a client disconnect cancels the generator.
#
# Every poll goes through `off_loop`, because the store read behind it is
# blocking SQLite and the generator runs ON the event loop — see api/sse.py for
# what calling one directly costs.
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse

from api.dependencies import Policy
from api.common.models.fields import SessionIdPath
from api.dashboard.sessions import _scope
from api.sse import (
    BEAT,
    EVENT_STREAM,
    NO_STORE,
    STREAM_HEARTBEAT_SECONDS,
    STREAM_POLL_SECONDS,
    off_loop,
    sse_frame,
    stable_snapshot,
)
from app.providers import (
    DashboardStream,
    GlobalApplication,
    Recorder,
    SessionApplication,
    Sessions,
)
from dashboard.render.serialize import json_ready
from dashboard.services.streams import DashboardStreamService
from dashboard.services.workspace import SessionApplicationService
from audit.recorder import AuditRecorder
from domain.ids import SessionId
from engine.projections import ActivityScope

router = APIRouter()


@router.get("/api/stream")
def global_stream(
    overview: GlobalApplication, policy: Policy, audit: Recorder
) -> StreamingResponse:
    async def frames():
        try:
            yield sse_frame("ready", {"boot_id": policy.boot_id})
            previous_snapshot = None
            heartbeat_at = asyncio.get_running_loop().time()
            while True:
                snapshot = json_ready(await off_loop(overview.snapshot))
                encoded_snapshot = stable_snapshot(snapshot)
                now = asyncio.get_running_loop().time()
                if encoded_snapshot != previous_snapshot:
                    yield sse_frame("application", snapshot)
                    previous_snapshot = encoded_snapshot
                    heartbeat_at = now
                elif now - heartbeat_at >= STREAM_HEARTBEAT_SECONDS:
                    yield BEAT
                    heartbeat_at = now
                await asyncio.sleep(STREAM_POLL_SECONDS)
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception:
            # An SSE stream drives the whole page; it must not die silently.
            # The audit row is the trace, the error frame is the client's
            # signal, and the connection ends so the client reconnects.
            audit.error("", "global stream", {"path": "/api/stream"})
            yield sse_frame("error", {"error": "stream failed"})

    return StreamingResponse(frames(), media_type=EVENT_STREAM, headers=NO_STORE)


@router.get("/api/sessions/{session_id}/stream")
def session_stream(
    session_id: SessionIdPath,
    sessions: Sessions,
    stream: DashboardStream,
    workspace: SessionApplication,
    audit: Recorder,
    after_cursor: int = 0,
    actor_id: str | None = None,
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
) -> StreamingResponse:
    session = SessionId(session_id)
    scope = _scope(sessions, session, actor_id)
    cursor = int(last_event_id) if last_event_id is not None else after_cursor
    return StreamingResponse(
        _session_frames(stream, workspace, audit, session, cursor, scope),
        media_type=EVENT_STREAM,
        headers=NO_STORE,
    )


async def _session_frames(
    stream: DashboardStreamService,
    workspace: SessionApplicationService,
    audit: AuditRecorder,
    session_id: SessionId,
    cursor: int,
    scope: ActivityScope,
):
    try:
        heartbeat_at = asyncio.get_running_loop().time()
        previous_application = None
        while True:
            sent = False
            frame = await off_loop(stream.frame, session_id, cursor, scope)
            if frame is not None:
                yield frame.sse()
                cursor = frame.cursor
                sent = True
            application_snapshot = json_ready(
                await off_loop(workspace.snapshot, session_id)
            )
            encoded_application = stable_snapshot(application_snapshot)
            if encoded_application != previous_application:
                yield sse_frame("application", application_snapshot)
                previous_application = encoded_application
                sent = True
            now = asyncio.get_running_loop().time()
            if sent:
                heartbeat_at = now
            elif now - heartbeat_at >= STREAM_HEARTBEAT_SECONDS:
                yield BEAT
                heartbeat_at = now
            await asyncio.sleep(STREAM_POLL_SECONDS)
    except (asyncio.CancelledError, GeneratorExit):
        raise
    except Exception:
        # Same containment as the pane and global streams: audit, tell the
        # client, end the connection so it reconnects.
        audit.error(str(session_id), "session stream", {"cursor": cursor})
        yield sse_frame("error", {"error": "stream failed"})
