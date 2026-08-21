"""Plan-limit snapshots, one per account per harness."""

from __future__ import annotations

from typing import Protocol

from harness.models import AccountUsageSnapshot


class AccountUsageRepository(Protocol):
    def record(self, account_usage_snapshot: AccountUsageSnapshot) -> None:
        """Upsert the account row and replace its windows, in one transaction.

        Keyed by account rather than by session: nothing reads a session's own
        historical snapshot, and keying by account turns the newest-first fold
        the reader used to do into a plain select.
        """
        ...

    def snapshots(self) -> tuple[AccountUsageSnapshot, ...]:
        """Every account with its windows, in two queries."""
        ...
