"""Claude Code account and rate-limit rows from plugin-owned status snapshots."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from harness.contract import HarnessUsage
from harness.models import AccountUsageSnapshot, UsageRow, UsageWindow
from repository.contract.usage import AccountUsageRepository
from harness.impl.claude_code import account

HARNESS = "claude_code"
WINDOWS: dict[str, tuple[str, int, Literal["account", "model"]]] = {
    "five_hour": ("5h", 5 * 60, "account"),
    "seven_day": ("7d", 7 * 24 * 60, "account"),
}


class ClaudeCodeUsage(HarnessUsage):
    def read(self, usage: AccountUsageRepository) -> tuple[UsageRow, ...]:
        snapshots = {
            snapshot.account_id: snapshot
            for snapshot in usage.snapshots()
            if snapshot.harness == HARNESS
        }
        accounts = account.registry()
        if not accounts and None in snapshots:
            accounts = [{"slug": "", "label": snapshots[None].display_name}]
        return tuple(
            self._row(record, snapshots.get(record["slug"] or None)) for record in accounts
        )

    @staticmethod
    def _row(account_record: dict, snapshot: AccountUsageSnapshot | None) -> UsageRow:
        samples = snapshot.windows if snapshot is not None else ()
        windows = []
        for sample in samples:
            fallback: tuple[str, int, Literal["account", "model"]] = (
                sample.key.replace("_", " "), 7 * 24 * 60, "model",
            )
            label, minutes, scope = WINDOWS.get(sample.key, fallback)
            windows.append(
                UsageWindow(
                    key=sample.key,
                    label=label,
                    used_percent=sample.used_percent,
                    resets_at=sample.resets_at,
                    duration_minutes=minutes,
                    scope=scope,
                    model_id=sample.key if scope == "model" else None,
                )
            )
        five_hour = next(
            (sample.used_percent for sample in samples if sample.key == "five_hour"), None
        )
        scheduling_score = Decimal(100) - five_hour if five_hour is not None else None
        return UsageRow(
            harness=HARNESS,
            account_id=account_record["slug"] or None,
            display_name=account_record["label"],
            switchable=bool(account_record["slug"]),
            plan=None,
            windows=tuple(windows),
            scheduling_score=scheduling_score,
            scheduling_allowed=True,
            limit=None,
            authentication_error=None,
        )


usage_reader = ClaudeCodeUsage()
