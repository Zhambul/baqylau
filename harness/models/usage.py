"""What one account's plan limits look like, as one harness reports them."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from domain.ids import AccountId, HarnessName


class UsageWindowScope(StrEnum):
    """What the strip lays a window out by: an `ACCOUNT` window gets its own
    reset column, a `MODEL` one is a cap under the account window of the same
    duration and rides in the block beside it."""

    ACCOUNT = "account"
    MODEL = "model"


@dataclass(frozen=True)
class UsageWindow:
    key: str
    label: str
    used_percent: Decimal
    resets_at: float | None
    duration_minutes: int | None
    scope: UsageWindowScope
    model_name: str | None


@dataclass(frozen=True)
class UsageBlock:
    model_name: str | None
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
class UsageRow:
    harness: HarnessName
    account_id: AccountId | None
    display_name: str
    switchable: bool
    default_for_launch: bool
    plan: str | None
    windows: tuple[UsageWindow, ...]
    scheduling_score: Decimal | None
    scheduling_allowed: bool
    limit: UsageBlock | None
    authentication_error: str | None
    collection_error: str | None = None
