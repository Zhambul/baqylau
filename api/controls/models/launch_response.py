# What a launch did. `rejected` carries the same body as `started` — a refusal
# here is a verdict, not an error — and the status is which one it was.
from typing import Literal

from pydantic import BaseModel



class LaunchResponse(BaseModel):
    status: Literal["started", "rejected"]
    window_id: str | None
    reason: str | None
