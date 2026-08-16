# api/sse.py — the SSE framing vocabulary every stream router shares: the
# frame encoder, the idle beat, the cadences, and the change-detection dump.
from __future__ import annotations

import json

STREAM_POLL_SECONDS = 0.25
STREAM_HEARTBEAT_SECONDS = 15.0
BEAT = ": beat\n\n"
EVENT_STREAM = "text/event-stream"
NO_STORE = {"Cache-Control": "no-store"}


def sse_frame(event: str, payload) -> str:
    return "event: %s\ndata: %s\n\n" % (event, json.dumps(payload, default=str))


def stable_snapshot(snapshot) -> str:
    """The change-detection encoding: a differing dump is a frame worth sending."""
    return json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
