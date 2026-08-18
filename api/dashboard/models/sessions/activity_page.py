# One page of the backlog, oldest-first, with the cursor that fetches the page
# before it.
from pydantic import BaseModel

from api.dashboard.models.sessions.activity_item import ActivityItemResponse

class ActivityPageResponse(BaseModel):
    oldest_cursor: int
    latest_cursor: int | None
    has_more: bool
    items: tuple[ActivityItemResponse, ...]
