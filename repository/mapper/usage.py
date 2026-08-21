"""Row DTOs to an account's plan-limit snapshot.

Absorbs the deterministic JSON encoding this used to need, and the `_reset`
key-suffix convention its reader used to decode by string matching: the reset
time is a column now.
"""

from __future__ import annotations

from decimal import Decimal

from domain.ids import HarnessName
from harness.models import AccountUsageSnapshot, UsageWindowSample
from repository.model.usage import AccountUsageSnapshotRow, AccountUsageWindowRow
from repository.model.sql import SqlValues

# An absent account id is stored as the empty string, because it is half of a
# composite primary key and SQLite does not treat NULLs in one as equal.
NO_ACCOUNT = ""


def account_usage_snapshot(
    account_usage_snapshot_row: AccountUsageSnapshotRow,
    windows: tuple[AccountUsageWindowRow, ...],
) -> AccountUsageSnapshot:
    return AccountUsageSnapshot(
        harness=HarnessName(account_usage_snapshot_row.harness),
        account_id=account_usage_snapshot_row.account_id or None,
        display_name=account_usage_snapshot_row.display_name,
        captured_at=account_usage_snapshot_row.captured_at,
        windows=tuple(
            UsageWindowSample(
                key=window.window_key,
                used_percent=Decimal(window.used_percent),
                resets_at=window.resets_at,
            )
            for window in sorted(windows, key=lambda window: window.window_key)
        ),
    )


def snapshot_values(account_usage_snapshot: AccountUsageSnapshot) -> SqlValues:
    return (
        account_usage_snapshot.harness,
        account_usage_snapshot.account_id or NO_ACCOUNT,
        account_usage_snapshot.display_name,
        account_usage_snapshot.captured_at,
    )


def window_values(account_usage_snapshot: AccountUsageSnapshot) -> tuple[SqlValues, ...]:
    return tuple(
        (
            account_usage_snapshot.harness,
            account_usage_snapshot.account_id or NO_ACCOUNT,
            window.key,
            str(window.used_percent),
            window.resets_at,
        )
        for window in account_usage_snapshot.windows
    )
