"""What one account's plan limits look like, as one harness reports them."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from domain.ids import AccountId, ModelId


@dataclass(frozen=True)
class UsageWindow:
    key: str
    label: str
    used_percent: Decimal
    resets_at: float | None
    duration_minutes: int | None
    scope: Literal["account", "model"]
    model_id: ModelId | None


@dataclass(frozen=True)
class UsageBlock:
    model_id: ModelId | None
    message: str | None
    resets_at: float | None


@dataclass(frozen=True)
class UsageWindowSample:
    """One plan window as a harness last reported it.

    `resets_at` is a field, not a sibling key with a `_reset` name suffix —
    which is how it was carried when the whole snapshot was one JSON blob.
    """

    key: str
    used_percent: Decimal
    resets_at: float | None


@dataclass(frozen=True)
class AccountUsageSnapshot:
    """The current plan-limit picture for one account of one harness."""

    harness: str
    account_id: AccountId | None
    display_name: str
    captured_at: float
    windows: tuple[UsageWindowSample, ...]


@dataclass(frozen=True)
class UsageRow:
    harness: str
    account_id: AccountId | None
    display_name: str
    switchable: bool
    plan: str | None
    windows: tuple[UsageWindow, ...]
    scheduling_score: Decimal | None
    scheduling_allowed: bool
    limit: UsageBlock | None
    authentication_error: str | None
