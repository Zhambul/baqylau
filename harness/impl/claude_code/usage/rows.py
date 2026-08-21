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

from decimal import Decimal
from typing import Literal

from harness.contract import HarnessUsage
from harness.models import AccountUsageSnapshot, UsageRow, UsageWindow, UsageWindowSample
from repository.contract.usage import AccountUsageRepository
from harness.impl.claude_code import account
from harness.impl.claude_code.account import AccountRecord
from harness.impl.claude_code.usage import live

HARNESS = "claude_code"
# The account-wide windows. A per-model cap is one of these keys with the model
# appended — `seven_day_fable` is the weekly Fable bucket under the weekly bar —
# which is what lets one vocabulary carry both.
WINDOWS: dict[str, tuple[str, int]] = {
    "five_hour": ("5h", 5 * 60),
    "seven_day": ("7d", 7 * 24 * 60),
}
UNKNOWN_WINDOW_MINUTES = 7 * 24 * 60


def window_shape(key: str) -> tuple[str, int, Literal["account", "model"], str | None]:
    """One window key as (label, duration, scope, model).

    Scope is what the strip lays itself out by: an `account` window gets its own
    reset column, a `model` one is a cap UNDER the account window of the same
    duration and rides in the block beside it.
    """
    if key in WINDOWS:
        label, minutes = WINDOWS[key]
        return label, minutes, "account", None
    for base, (label, minutes) in WINDOWS.items():
        prefix = f"{base}_"
        if key.startswith(prefix) and len(key) > len(prefix):
            model = key[len(prefix):].replace("_", " ")
            return f"{label} {model}", minutes, "model", model
    # A window neither source has sent before still renders, because the harness
    # may add one without telling us: model-scoped by assumption, since every
    # account-wide window we know of is named above.
    return key.replace("_", " "), UNKNOWN_WINDOW_MINUTES, "model", key


def _order(usage_window_sample: UsageWindowSample) -> tuple[int, str]:
    """Account-wide windows first in their canonical order, then model caps by
    key — so the strip reads short-window-first and each cap follows the window
    it belongs to."""
    if usage_window_sample.key in WINDOWS:
        return list(WINDOWS).index(usage_window_sample.key), ""
    return len(WINDOWS), usage_window_sample.key


def merge(
    account_usage_snapshot: AccountUsageSnapshot | None,
    live_usage: live.LiveUsage | None,
) -> tuple[UsageWindowSample, ...]:
    """Every window either source knows, each at its freshest reading."""
    readings: dict[str, tuple[float, UsageWindowSample]] = {}
    sources = []
    if account_usage_snapshot is not None:
        sources.append((account_usage_snapshot.captured_at, account_usage_snapshot.windows))
    if live_usage is not None:
        sources.append((live_usage.captured_at, live_usage.windows))
    for captured_at, samples in sources:
        for sample in samples:
            previous = readings.get(sample.key)
            if previous is None or captured_at >= previous[0]:
                readings[sample.key] = (captured_at, sample)
    return tuple(sorted((sample for _, sample in readings.values()), key=_order))


class ClaudeCodeUsage(HarnessUsage):
    def read(self, account_usage_repository: AccountUsageRepository) -> tuple[UsageRow, ...]:
        snapshots = {
            snapshot.account_id: snapshot
            for snapshot in account_usage_repository.snapshots()
            if snapshot.harness == HARNESS
        }
        accounts = account.registry()
        if not accounts and None in snapshots:
            accounts = [{"slug": "", "label": snapshots[None].display_name, "alias": ""}]
        return tuple(
            self._row(
                record,
                snapshots.get(record["slug"] or None),
                live.usage(account.config_directory(record["slug"] or None)),
            )
            for record in accounts
        )

    @staticmethod
    def _row(
        account_record: AccountRecord,
        account_usage_snapshot: AccountUsageSnapshot | None,
        live_usage: live.LiveUsage | None,
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
                    model_id=model,
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
            plan=live_usage.plan if live_usage is not None else None,
            windows=tuple(windows),
            scheduling_score=scheduling_score,
            scheduling_allowed=True,
            limit=None,
            authentication_error=None,
        )


usage_reader = ClaudeCodeUsage()
