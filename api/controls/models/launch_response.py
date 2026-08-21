# What a launch did. `rejected` carries the same body as `started` — a refusal
# here is a verdict, not an error — and the status is which one it was.
from pydantic import BaseModel

from harness.models import LaunchStatus


class LaunchResponse(BaseModel):
    status: LaunchStatus
    window_id: str | None
    reason: str | None
