# One optimistic-UI lifecycle report.
from enum import StrEnum

from pydantic import BaseModel


class OptimisticActionKind(StrEnum):
    COMPOSER = "composer"
    CLOSE = "close"
    ANSWER = "answer"
    PLAN = "plan"


class OptimisticActionPhase(StrEnum):
    SHOWN = "shown"
    RECONCILED = "reconciled"
    DROPPED = "dropped"
    STALE = "stale"


class OptimisticActionRequest(BaseModel):
    action: OptimisticActionKind
    phase: OptimisticActionPhase
    character_count: int | None = None
    elapsed_milliseconds: int | None = None
    reason: str | None = None
