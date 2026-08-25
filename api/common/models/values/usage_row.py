# One account's plan limits, as its harness reports them — the fuel gauges on
# the list page. Percentages and scores are Decimals, at the HTTP boundary as strings.
from decimal import Decimal

from pydantic import BaseModel

from harness.models import UsageWindowScope


class UsageWindowResponse(BaseModel):
    key: str
    label: str
    used_percent: Decimal
    resets_at: float | None
    duration_minutes: int | None
    scope: UsageWindowScope
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
    default_for_launch: bool
    plan: str | None
    windows: tuple[UsageWindowResponse, ...]
    scheduling_score: Decimal | None
    scheduling_allowed: bool
    limit: UsageBlockResponse | None
    authentication_error: str | None
    collection_error: str | None = None
