"""Codex current rate limits exposed through the shared usage contract."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from domain.ids import HarnessName
from harness.contract import HarnessUsage
from harness.impl.codex import usage as native_usage
from harness.models import UsageRow, UsageWindow
from harness.models.usage import UsageWindowScope
from harness.runtime import HarnessRuntimeConfig, default_harness_runtime_configs

HARNESS = HarnessName.CODEX
WINDOW_LABELS: Mapping[int, str] = {300: "5h", 10080: "7d"}


def _window_label(duration_minutes: int) -> str:
    return WINDOW_LABELS.get(duration_minutes, f"{duration_minutes}m")


class CodexUsage(HarnessUsage):
    def __init__(self, harness_runtime_config: HarnessRuntimeConfig) -> None:
        self.runtime = harness_runtime_config

    def read(self) -> tuple[UsageRow, ...]:
        collection = native_usage.collect_rate_limits(self.runtime)
        rate_limits = collection.usage
        plan = rate_limits.plan if rate_limits is not None else None
        windows = tuple(
            UsageWindow(
                key=f"minutes_{window.duration_minutes}",
                label=_window_label(window.duration_minutes),
                used_percent=Decimal(str(window.used_percent)),
                resets_at=float(window.resets_at) if window.resets_at is not None else None,
                duration_minutes=window.duration_minutes,
                scope=UsageWindowScope.ACCOUNT,
                model_name=None,
            )
            for window in (rate_limits.windows if rate_limits is not None else ())
        )
        return (UsageRow(
            harness=HARNESS,
            account_id=None,
            display_name="codex",
            switchable=False,
            default_for_launch=False,
            plan=plan or None,
            windows=windows,
            scheduling_score=None,
            scheduling_allowed=False,
            limit=None,
            authentication_error=None,
            collection_error=collection.error,
        ),)


usage_reader = CodexUsage(
    default_harness_runtime_configs().for_harness(HARNESS)
)
