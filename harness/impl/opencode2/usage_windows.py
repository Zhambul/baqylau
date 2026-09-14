# Copyright (c) 2026 Zhambyl Yermagambet
"""Read native account windows and map them to dashboard windows."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from harness.models.usage import UsageWindow, UsageWindowScope


class NativeUsageWindow(BaseModel):
    """Read one limit window from OpenCode Go."""

    percent: Decimal = Field(ge=0, le=100)
    resets_at: datetime = Field(alias="resetsAt")


class NativeUsageWindows(BaseModel):
    """Require all three subscription windows."""

    rolling: NativeUsageWindow
    weekly: NativeUsageWindow
    monthly: NativeUsageWindow


class NativeUsageResult(BaseModel):
    """Read the credential owner's response without credential data."""

    status: int
    usage: NativeUsageWindows | None = None


class NativeUsageResponse(BaseModel):
    """Read the native RPC response."""

    output: NativeUsageResult


def windows(native_usage_windows: NativeUsageWindows) -> tuple[UsageWindow, ...]:
    """Map the reported percentages and reset times.

    Returns:
        The three account windows.

    """
    return tuple(
        UsageWindow(
            key=key, label=label, used_percent=window.percent,
            resets_at=window.resets_at.timestamp(), duration_minutes=minutes,
            scope=UsageWindowScope.ACCOUNT, model_name=None,
        )
        for key, label, minutes, window in (
            ("rolling", "5h", 300, native_usage_windows.rolling),
            ("weekly", "7d", 10080, native_usage_windows.weekly),
            # The reset comes from the service. The duration is the nominal
            # month used to place this window beside the other account limits.
            ("monthly", "1mo", 43200, native_usage_windows.monthly),
        )
    )
