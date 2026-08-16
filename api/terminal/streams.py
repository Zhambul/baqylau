# api/terminal/streams.py — the pane frame streams the two pane processes
# copy to their terminals.
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from api.sse import BEAT, EVENT_STREAM, NO_STORE, STREAM_POLL_SECONDS, sse_frame
from api.dependencies import ApplicationGraph
from diagnostics import record as A
from domain.ids import SessionId

router = APIRouter()


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
