"""Map Claude's provider-owned usage result to the shared usage row."""

from __future__ import annotations

from dataclasses import dataclass

from domain.ids import HarnessName
from harness.contract import HarnessUsage
from harness.impl.claude_code.usage import live
from harness.models import UsageRow, UsageWindow, UsageWindowSample
from harness.models.usage import UsageWindowScope
from harness.runtime import HarnessRuntimeConfig, default_harness_runtime_configs

HARNESS = HarnessName.CLAUDE_CODE
DISPLAY_NAME = "claude"


@dataclass(frozen=True)
class WindowDefinition:
    key: str
    label: str
    minutes: int


WINDOWS = (
    WindowDefinition("five_hour", "5h", 5 * 60),
    WindowDefinition("seven_day", "7d", 7 * 24 * 60),
)
UNKNOWN_WINDOW_MINUTES = 7 * 24 * 60


def window_shape(key: str) -> tuple[str, int, UsageWindowScope, str | None]:
    known = next((window for window in WINDOWS if window.key == key), None)
    if known is not None:
        return known.label, known.minutes, UsageWindowScope.ACCOUNT, None
    for window in WINDOWS:
        prefix = f"{window.key}_"
        if key.startswith(prefix) and len(key) > len(prefix):
            model = key[len(prefix):].replace("_", " ")
            return f"{window.label} {model}", window.minutes, UsageWindowScope.MODEL, model
    return key.replace("_", " "), UNKNOWN_WINDOW_MINUTES, UsageWindowScope.MODEL, key


def _order(usage_window_sample: UsageWindowSample) -> tuple[int, str]:
    known_index = next(
        (
            index
            for index, window in enumerate(WINDOWS)
            if window.key == usage_window_sample.key
        ),
        None,
    )
    if known_index is not None:
        return known_index, ""
    return len(WINDOWS), usage_window_sample.key


def _window(usage_window_sample: UsageWindowSample) -> UsageWindow:
    label, minutes, scope, model = window_shape(usage_window_sample.key)
    return UsageWindow(
        key=usage_window_sample.key,
        label=label,
        used_percent=usage_window_sample.used_percent,
        resets_at=usage_window_sample.resets_at,
        duration_minutes=minutes,
        scope=scope,
        model_name=model,
    )


class ClaudeCodeUsage(HarnessUsage):
    def __init__(self, harness_runtime_config: HarnessRuntimeConfig) -> None:
        self.runtime = harness_runtime_config

    def read(self) -> tuple[UsageRow, ...]:
        collection = live.collect(self.runtime)
        samples = (
            tuple(sorted(collection.usage.windows, key=_order))
            if collection.usage is not None
            else ()
        )
        windows = tuple(_window(sample) for sample in samples)
        return (
            UsageRow(
                harness=HARNESS,
                account_id=None,
                display_name=DISPLAY_NAME,
                switchable=False,
                default_for_launch=True,
                plan=collection.usage.plan if collection.usage is not None else None,
                windows=windows,
                scheduling_score=None,
                scheduling_allowed=False,
                limit=None,
                authentication_error=None,
                collection_error=collection.error,
            ),
        )


usage_reader = ClaudeCodeUsage(
    default_harness_runtime_configs().for_harness(HARNESS)
)
