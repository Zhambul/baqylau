"""Claude Code account and rate-limit rows from plugin-owned status snapshots."""

from __future__ import annotations

from decimal import Decimal

from contracts.harness import HarnessUsage, UsageRow, UsageWindow
from plugins.claude_code import account, usage_state

WINDOWS = {
    "five_hour": ("5h", 5 * 60, "account"),
    "seven_day": ("7d", 7 * 24 * 60, "account"),
}


class ClaudeCodeUsage(HarnessUsage):
    def read(self) -> tuple[UsageRow, ...]:
        snapshots = usage_state.latest_by_account()
        accounts = account.registry()
        if not accounts and None in snapshots:
            accounts = [{"slug": "", "label": snapshots[None]["display_name"]}]
        return tuple(self._row(record, snapshots.get(record["slug"] or None)) for record in accounts)

    @staticmethod
    def _row(account_record: dict, snapshot: dict | None) -> UsageRow:
        values = snapshot["windows"] if snapshot is not None else {}
        windows = []
        for key, used_percent in values.items():
            if key.endswith("_reset") or not isinstance(used_percent, (int, float)):
                continue
            label, minutes, scope = WINDOWS.get(
                key,
                (key.replace("_", " "), 7 * 24 * 60, "model"),
            )
            windows.append(
                UsageWindow(
                    key=key,
                    label=label,
                    used_percent=Decimal(str(used_percent)),
                    resets_at=values.get(f"{key}_reset"),
                    duration_minutes=minutes,
                    scope=scope,
                    model_id=key if scope == "model" else None,
                )
            )
        five_hour = values.get("five_hour")
        scheduling_score = (
            Decimal(100) - Decimal(str(five_hour))
            if isinstance(five_hour, (int, float))
            else None
        )
        return UsageRow(
            harness="claude_code",
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
