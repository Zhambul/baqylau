"""Codex current rate limits exposed through the shared usage contract."""

from __future__ import annotations

from decimal import Decimal

from harness.contract import HarnessUsage
from harness.impl.codex import usage as native_usage
from repository.contract.usage import AccountUsageRepository
from harness.models import UsageRow, UsageWindow

WINDOW_LABELS = {300: "5h", 10080: "7d"}


class CodexUsage(HarnessUsage):
    def read(self, usage: AccountUsageRepository) -> tuple[UsageRow, ...]:
        del usage          # codex asks its own CLI live; nothing is cached
        rate_limits = native_usage.read_rate_limits()
        if rate_limits is None:
            return ()
        plan = rate_limits["plan"]
        windows = tuple(
            UsageWindow(
                key=f"minutes_{window['duration_minutes']}",
                label=WINDOW_LABELS.get(
                    window["duration_minutes"],
                    f"{window['duration_minutes']}m",
                ),
                used_percent=Decimal(str(window["used_percent"])),
                resets_at=window["resets_at"],
                duration_minutes=window["duration_minutes"],
                scope="account",
                model_id=None,
            )
            for window in rate_limits["windows"]
        )
        return (UsageRow(
            harness="codex",
            account_id=None,
            display_name=f"codex · {plan}" if plan else "codex",
            switchable=False,
            plan=plan or None,
            windows=windows,
            scheduling_score=None,
            scheduling_allowed=False,
            limit=None,
            authentication_error=None,
        ),)


usage_reader = CodexUsage()
