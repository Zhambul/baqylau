"""Claude Code account and rate-limit rows, from two sources that differ in
scope rather than in vocabulary.

  the status-line snapshot   pushed, seconds old, two account-wide windows only
                             (the harness copies no others into that payload)
  the harness's own answer   pulled, minutes old, every window the plan has —
                             including the per-model weekly caps, which is the
                             only place a Fable bar can come from (`live.py`)

Neither is a superset of the other in TIME, so a window is taken from whichever
source read it more recently, and a source that failed contributes nothing. With
no live answer at all this file behaves exactly as it did when the status line
was the only channel.
"""

from __future__ import annotations

from dataclasses import dataclass
from domain.ids import HarnessName
from harness.contract import HarnessUsage
from harness.models import AccountUsageSnapshot, UsageRow, UsageWindow, UsageWindowSample
from harness.models.usage import UsageWindowScope
from repository.contract.usage import AccountUsageRepository
from harness.impl.claude_code.usage import live

HARNESS = HarnessName.CLAUDE_CODE
DISPLAY_NAME = "claude"
# The account-wide windows. A per-model cap is one of these keys with the model
# appended — `seven_day_fable` is the weekly Fable bucket under the weekly bar —
# which is what lets one vocabulary carry both.
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
    """One window key as (label, duration, scope, model).

    Scope is what the strip lays itself out by: an `account` window gets its own
    reset column, a `model` one is a cap UNDER the account window of the same
    duration and rides in the block beside it.
    """
    known = next((window for window in WINDOWS if window.key == key), None)
    if known is not None:
        return known.label, known.minutes, UsageWindowScope.ACCOUNT, None
    for window in WINDOWS:
        prefix = f"{window.key}_"
        if key.startswith(prefix) and len(key) > len(prefix):
            model = key[len(prefix):].replace("_", " ")
            return f"{window.label} {model}", window.minutes, UsageWindowScope.MODEL, model
    # A window neither source has sent before still renders, because the harness
    # may add one without telling us: model-scoped by assumption, since every
    # account-wide window we know of is named above.
    return key.replace("_", " "), UNKNOWN_WINDOW_MINUTES, UsageWindowScope.MODEL, key


def _order(usage_window_sample: UsageWindowSample) -> tuple[int, str]:
    """Account-wide windows first in their canonical order, then model caps by
    key — so the strip reads short-window-first and each cap follows the window
    it belongs to."""
    known_index = next(
        (index for index, window in enumerate(WINDOWS)
         if window.key == usage_window_sample.key),
        None,
    )
    if known_index is not None:
        return known_index, ""
    return len(WINDOWS), usage_window_sample.key


def merge(
    account_usage_snapshot: AccountUsageSnapshot | None,
    live_usage: live.LiveUsage | None,
) -> tuple[UsageWindowSample, ...]:
    """Every window either source knows, each at its freshest reading."""
    @dataclass(frozen=True)
    class Reading:
        captured_at: float
        sample: UsageWindowSample

    readings: list[Reading] = []
    sources = []
    if account_usage_snapshot is not None:
        sources.append((account_usage_snapshot.captured_at, account_usage_snapshot.windows))
    if live_usage is not None:
        sources.append((live_usage.captured_at, live_usage.windows))
    for captured_at, samples in sources:
        for sample in samples:
            previous_index = next(
                (index for index, reading in enumerate(readings)
                 if reading.sample.key == sample.key),
                None,
            )
            if previous_index is None:
                readings.append(Reading(captured_at, sample))
            elif captured_at >= readings[previous_index].captured_at:
                readings[previous_index] = Reading(captured_at, sample)
    return tuple(sorted((reading.sample for reading in readings), key=_order))


class ClaudeCodeUsage(HarnessUsage):
    def read(self, account_usage_repository: AccountUsageRepository) -> tuple[UsageRow, ...]:
        snapshots = tuple(
            snapshot
            for snapshot in account_usage_repository.snapshots()
            if snapshot.harness == HARNESS
        )
        default_snapshot = next(
            (snapshot for snapshot in snapshots if snapshot.account_id is None),
            None,
        )
        collection = live.collect()
        return (
            self._row(
                default_snapshot,
                collection.usage,
                collection.error,
            ),
        )

    @staticmethod
    def _row(
        account_usage_snapshot: AccountUsageSnapshot | None,
        live_usage: live.LiveUsage | None,
        collection_error: str | None,
    ) -> UsageRow:
        samples = merge(account_usage_snapshot, live_usage)
        windows = []
        for sample in samples:
            label, minutes, scope, model = window_shape(sample.key)
            windows.append(
                UsageWindow(
                    key=sample.key,
                    label=label,
                    used_percent=sample.used_percent,
                    resets_at=sample.resets_at,
                    duration_minutes=minutes,
                    scope=scope,
                    model_name=model,
                )
            )
        return UsageRow(
            harness=HARNESS,
            account_id=None,
            display_name=DISPLAY_NAME,
            switchable=False,
            default_for_launch=True,
            plan=live_usage.plan if live_usage is not None else None,
            windows=tuple(windows),
            scheduling_score=None,
            scheduling_allowed=False,
            limit=None,
            authentication_error=None,
            collection_error=collection_error,
        )


usage_reader = ClaudeCodeUsage()
