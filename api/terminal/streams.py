# api/terminal/streams.py — the pane frame streams the two pane processes
# copy to their terminals.
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from api.common.models.fields import SessionIdPath
from api.common.models.streams.error_frame import ErrorFrame
from api.common.models.streams.pane_frame import PaneScreenFrame, PaneSessionFrame
from api.responses import errors
from api.sse import BEAT, EVENT_STREAM, NO_STORE, STREAM_POLL_SECONDS, off_loop, sse_frame
from app.providers import PaneStreams, Recorder, Sessions
from audit.recorder import AuditRecorder
from domain.ids import SessionId
from repository.contract.sessions import SessionRepository
from terminal.panes.streams import WAITING_FRAME, PaneStreamService

router = APIRouter()


@router.get("/api/sessions/{session_id}/panes/{kind}/stream",
            responses=errors({404: "No pane of that kind."}))
def pane_stream(
    session_id: SessionIdPath,
    kind: str,
    request: Request,
    sessions: Sessions,
    panes: PaneStreams,
    audit: Recorder,
    width: int = Query(..., gt=0),
) -> Response:
    """One terminal pane's frame stream. The pane process is a byte-copying
    client: everything it paints arrives as `frame` events rendered here at
    the client's width; idle ticks are SSE comments the client uses as its
    resize/liveness clock (a resize is a reconnect at the new width)."""
    if kind not in ("mirror", "scoreboard"):
        # Raised, not built: api/app.py's handler renders every refusal as this
        # server's one error body, and there is no second place that shape lives.
        raise HTTPException(404, "not found")
    session = SessionId(session_id)
    request_path = (request.url.path + ("?" + request.url.query if request.url.query else ""))[:200]
    return StreamingResponse(
        _pane_frames(sessions, panes, audit, session, kind, width, request_path),
        media_type=EVENT_STREAM,
        headers=NO_STORE,
    )


async def _pane_frames(
    sessions: SessionRepository,
    panes: PaneStreamService,
    audit: AuditRecorder,
    session_id: SessionId,
    kind: str,
    width: int,
    request_path: str,
):
    stream_identifier = audit.stream_start(str(session_id), f"pane-{kind}", src_path=request_path)
    try:
        if kind == "mirror" and await off_loop(sessions.find, session_id) is None:
            # The pane's own banner, as a frame: the client paints nothing of its
            # own, so the "waiting for commands" state has to be sent to it.
            yield sse_frame("frame", PaneScreenFrame(ansi=WAITING_FRAME))
        while await off_loop(sessions.find, session_id) is None:
            # A pane process can connect before the session's row exists; hold
            # the stream open, beating, until it does.
            yield BEAT
            await asyncio.sleep(STREAM_POLL_SECONDS)
        yield sse_frame("session", PaneSessionFrame(session_id=str(session_id)))
        if kind == "mirror":
            rendered_version = None
            while True:
                frame = await off_loop(
                    panes.mirror_frame, session_id, width, rendered_version
                )
                if frame is None:
                    yield BEAT
                else:
                    rendered_version, ansi = frame
                    yield sse_frame("frame", PaneScreenFrame(ansi=ansi))
                await asyncio.sleep(STREAM_POLL_SECONDS)
        else:
            stream = await off_loop(panes.scoreboard_stream, session_id, width)
            while True:
                # Named apart from the mirror branch's `ansi`: that one is a
                # str, this one is a str-or-None, and they are two variables.
                scoreboard = await off_loop(stream.frame)
                yield BEAT if scoreboard is None else sse_frame(
                    "frame", PaneScreenFrame(ansi=scoreboard)
                )
                await asyncio.sleep(STREAM_POLL_SECONDS)
    except (asyncio.CancelledError, GeneratorExit):
        audit.stream_end(stream_identifier, "client-gone")
        raise
    except Exception:
        audit.error(str(session_id), f"pane {kind} stream", {"path": request_path})
        audit.stream_end(stream_identifier, "error")
        yield sse_frame("error", ErrorFrame(error="pane stream failed"))
