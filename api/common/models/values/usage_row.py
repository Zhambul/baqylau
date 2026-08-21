# One account's plan limits, as its harness reports them — the fuel gauges on
# the list page. Percentages and scores are Decimals, at the HTTP boundary as strings.
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class UsageWindowResponse(BaseModel):
    key: str
    label: str
    used_percent: Decimal
    resets_at: float | None
    duration_minutes: int | None
    scope: Literal["account", "model"]
    model_id: str | None


class UsageBlockResponse(BaseModel):
    model_id: str | None
    message: str | None
    resets_at: float | None


class UsageRowResponse(BaseModel):
    harness: str
    account_id: str | None
    display_name: str
    switchable: bool
    plan: str | None
    windows: tuple[UsageWindowResponse, ...]
    scheduling_score: Decimal | None
    scheduling_allowed: bool
    limit: UsageBlockResponse | None
    authentication_error: str | None
