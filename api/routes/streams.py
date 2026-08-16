# api/routes/streams.py — the three SSE surfaces: the global application
# stream, the one-cursor session stream, and the terminal pane frame streams.
#
# Deliberately the same design as always: one direct poll path over the
# canonical store and the application snapshots — no broker, no subscription
# registry, no event bus (test_canonical_sse_has_no_broker_or_application_
# event_registry). Each stream is an async generator, so an open connection
# costs no worker thread; a client disconnect cancels the generator, which is
# where the audited pane streams record their exit.
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from api import config
from api.dependencies import ApplicationGraph
from api.routes.read import _scope
from core import audit as A
from dashboard.activity import to_wire
from domain.ids import SessionId
from runtime.projections import ActivityScope

router = APIRouter()

STREAM_POLL_SECONDS = 0.25
STREAM_HEARTBEAT_SECONDS = 15.0
BEAT = ": beat\n\n"
EVENT_STREAM = "text/event-stream"
NO_STORE = {"Cache-Control": "no-store"}


def sse_frame(event: str, payload) -> str:
    return "event: %s\ndata: %s\n\n" % (event, json.dumps(payload, default=str))


def _stable(snapshot) -> str:
    """The change-detection encoding: a differing dump is a frame worth sending."""
    return json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@router.get("/api/stream")
def global_stream(application: ApplicationGraph) -> StreamingResponse:
    async def frames():
        try:
            yield sse_frame("ready", {"boot_id": config.BOOT_ID})
            previous_snapshot = None
            heartbeat_at = asyncio.get_running_loop().time()
            while True:
                snapshot = to_wire(application.global_application.snapshot())
                encoded_snapshot = _stable(snapshot)
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
            A.error("", "global stream", {"path": "/api/stream"})
            yield sse_frame("error", {"error": "stream failed"})

    return StreamingResponse(frames(), media_type=EVENT_STREAM, headers=NO_STORE)


@router.get("/api/sessions/{session_id}/stream")
def session_stream(
    session_id: str,
    application: ApplicationGraph,
    after_cursor: int = 0,
    actor_id: str | None = None,
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
) -> StreamingResponse:
    session = SessionId(session_id)
    scope = _scope(application, session, actor_id)
    cursor = int(last_event_id) if last_event_id is not None else after_cursor
    return StreamingResponse(
        _session_frames(application, session, cursor, scope),
        media_type=EVENT_STREAM,
        headers=NO_STORE,
    )


async def _session_frames(application, session_id: SessionId, cursor: int, scope: ActivityScope):
    try:
        heartbeat_at = asyncio.get_running_loop().time()
        previous_application = None
        while True:
            sent = False
            frame = application.dashboard_stream.frame(session_id, cursor, scope)
            if frame is not None:
                yield frame.sse()
                cursor = frame.cursor
                sent = True
            application_snapshot = to_wire(application.session_application.snapshot(session_id))
            encoded_application = _stable(application_snapshot)
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
        A.error(str(session_id), "session stream", {"cursor": cursor})
        yield sse_frame("error", {"error": "stream failed"})


# The pane streams route BEFORE the generic session resources in the stdlib
# router because a pane may connect before the session's first fact commits;
# here the explicit path makes that ordering structural, and the wait loop
# below holds the stream open until the session's row exists.
@router.get("/api/sessions/{session_id}/panes/{kind}/stream")
def pane_stream(
    session_id: str,
    kind: str,
    request: Request,
    application: ApplicationGraph,
    width: int = Query(..., gt=0),
):
    """One terminal pane's frame stream. The pane process is a byte-copying
    client: everything it paints arrives as `frame` events rendered here at
    the client's width; idle ticks are SSE comments the client uses as its
    resize/liveness clock (a resize is a reconnect at the new width)."""
    if kind not in ("mirror", "scoreboard"):
        return JSONResponse({"error": "not found"}, 404)
    session = SessionId(session_id)
    request_path = (request.url.path + ("?" + request.url.query if request.url.query else ""))[:200]
    return StreamingResponse(
        _pane_frames(application, session, kind, width, request_path),
        media_type=EVENT_STREAM,
        headers=NO_STORE,
    )


async def _pane_frames(application, session_id: SessionId, kind: str, width: int, request_path: str):
    stream_identifier = A.stream_start(str(session_id), f"pane-{kind}", src_path=request_path)
    try:
        while application.sessions.find_by_id(session_id) is None:
            # A pane process can connect before the session's row exists; hold
            # the stream open, beating, until it does.
            yield BEAT
            await asyncio.sleep(STREAM_POLL_SECONDS)
        yield sse_frame("session", {"session_id": str(session_id)})
        if kind == "mirror":
            rendered_version = None
            while True:
                frame = application.pane_streams.mirror_frame(session_id, width, rendered_version)
                if frame is None:
                    yield BEAT
                else:
                    rendered_version, ansi = frame
                    yield sse_frame("frame", {"ansi": ansi})
                await asyncio.sleep(STREAM_POLL_SECONDS)
        else:
            stream = application.pane_streams.scoreboard_stream(session_id, width)
            while True:
                ansi = stream.frame()
                yield BEAT if ansi is None else sse_frame("frame", {"ansi": ansi})
                await asyncio.sleep(STREAM_POLL_SECONDS)
    except (asyncio.CancelledError, GeneratorExit):
        A.stream_end(stream_identifier, "client-gone")
        raise
    except Exception:
        A.error(str(session_id), f"pane {kind} stream", {"path": request_path})
        A.stream_end(stream_identifier, "error")
        yield sse_frame("error", {"error": "pane stream failed"})
