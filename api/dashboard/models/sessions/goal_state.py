# What the session is FOR, and whether it is still able to pursue it.
from typing import Literal

from pydantic import BaseModel


class GoalStateResponse(BaseModel):
    objective: str
    state: Literal[
        "active",
        "paused",
        "blocked",
        "usage_limited",
        "budget_limited",
        "completed",
    ]
    reason: str | None
