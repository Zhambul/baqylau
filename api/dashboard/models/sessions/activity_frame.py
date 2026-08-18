# One SSE `activity` frame: everything that changed since the reader's cursor,
# and the snapshot as of that cursor. Lives here, with the other wire models,
# because a frame is a RESPONSE — the service that folds it (dashboard/services
# /streams.py) knows nothing about how it reaches a browser.
from pydantic import BaseModel

from api.dashboard.models.sessions.activity_item import ActivityItemResponse
from api.dashboard.models.sessions.canonical_snapshot import CanonicalSnapshotResponse

class ActivityFrameResponse(BaseModel):
    cursor: int
    items: tuple[ActivityItemResponse, ...]
    snapshot: CanonicalSnapshotResponse
