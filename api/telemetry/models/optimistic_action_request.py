# One optimistic-UI lifecycle report.
from typing import Literal

from pydantic import BaseModel


class OptimisticActionRequest(BaseModel):
    action: Literal["composer", "close", "answer", "plan"]
    phase: Literal["shown", "reconciled", "dropped", "stale"]
    character_count: int | None = None
    elapsed_milliseconds: int | None = None
    reason: str | None = None
