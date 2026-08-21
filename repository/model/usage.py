"""Row shapes for the two usage tables.

`used_percent` is TEXT, not REAL: it becomes a `Decimal`, and a float round trip
would lose the exactness the display depends on. Same reason the canonical
mapper encodes a cost as a string.
"""

from __future__ import annotations

from dataclasses import dataclass

from domain.ids import AccountId, HarnessName


@dataclass(frozen=True)
class AccountUsageSnapshotRow:
    harness: HarnessName
    account_id: AccountId
    display_name: str
    captured_at: float


@dataclass(frozen=True)
class AccountUsageWindowRow:
    harness: HarnessName
    account_id: AccountId
    window_key: str
    used_percent: str
    resets_at: float | None
