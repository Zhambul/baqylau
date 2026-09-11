# Copyright (c) 2026 Zhambyl Yermagambet
"""Codex current rate limits exposed through the shared usage contract."""

from __future__ import annotations

from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING

from domain.ids import HarnessName
from harness.contract import HarnessUsage
from harness.impl.codex import usage as native_usage
from harness.models.usage import (
    UsageRow,
    UsageWindow,
    UsageWindowScope,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness.runtime import HarnessRuntimeConfig

HARNESS = HarnessName("codex")
WINDOW_LABELS: Mapping[int, str] = MappingProxyType({300: "5h", 10080: "7d"})


def _window_label(duration_minutes: int) -> str:
    return WINDOW_LABELS.get(duration_minutes, f"{duration_minutes}m")


class CodexUsage(HarnessUsage):
    """Represent codex usage."""

    def __init__(self, harness_runtime_config: HarnessRuntimeConfig) -> None:
        """Initialize the object."""
        self.runtime = harness_runtime_config

    def read(self) -> tuple[UsageRow, ...]:
        """Return read.

        Returns:
            Read.

        """
        collection = native_usage.collect_rate_limits(self.runtime)
        rate_limits = collection.usage
        plan = None if rate_limits is None else rate_limits.plan
        rate_limit_windows = () if rate_limits is None else rate_limits.windows
        windows = tuple(
            UsageWindow(
                key=f"minutes_{window.duration_minutes}",
                label=_window_label(window.duration_minutes),
                used_percent=Decimal(str(window.used_percent)),
                resets_at=None if window.resets_at is None else float(window.resets_at),
                duration_minutes=window.duration_minutes,
                scope=UsageWindowScope.ACCOUNT,
                model_name=None,
            )
            for window in rate_limit_windows
        )
        return (
            UsageRow(
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
            ),
        )
