"""What one account's plan limits look like, as one harness reports them."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class UsageWindow:
    key: str
    label: str
    used_percent: Decimal
    resets_at: float | None
    duration_minutes: int | None
    scope: Literal["account", "model"]
    model_id: str | None


@dataclass(frozen=True)
class UsageBlock:
    model_id: str | None
    message: str | None
    resets_at: float | None


@dataclass(frozen=True)
class UsageRow:
    harness: str
    account_id: str | None
    display_name: str
    switchable: bool
    plan: str | None
    windows: tuple[UsageWindow, ...]
    scheduling_score: Decimal | None
    scheduling_allowed: bool
    limit: UsageBlock | None
    authentication_error: str | None
