"""Global harness usage checks."""

from __future__ import annotations

from pytest_bdd import parsers, then

from api.common.models.values.usage_row import UsageRowResponse, UsageWindowResponse
from sdk.client import BaqylauClient, wait_for
from tests.e2e.testkit.policy import WaitPolicy


@then(parsers.parse('global usage for {harness} has at least {count:d} window'))
def global_usage_has_windows(
    client: BaqylauClient,
    wait_policy: WaitPolicy,
    harness: str,
    count: int,
) -> None:
    def found() -> list[UsageRowResponse] | None:
        rows = [
            row
            for row in client.usage.state().usage_rows
            if row.harness == harness and len(row.windows) >= count
        ]
        return rows or None

    wait_for(
        f"global usage for {harness!r} to have at least {count} window",
        found,
        timeout=wait_policy.feed,
    )


@then(parsers.parse('each global usage window for {harness} has a positive duration'))
def global_usage_windows_have_positive_duration(
    client: BaqylauClient,
    wait_policy: WaitPolicy,
    harness: str,
) -> None:
    def found() -> list[UsageWindowResponse] | None:
        windows = [
            window
            for row in client.usage.state().usage_rows
            if row.harness == harness
            for window in row.windows
        ]
        positive = windows and all(
            window.duration_minutes is not None and window.duration_minutes > 0
            for window in windows
        )
        return windows if positive else None

    wait_for(
        f"each global usage window for {harness!r} to have a positive duration",
        found,
        timeout=wait_policy.feed,
    )


@then(parsers.parse('each global usage window for {harness} has a valid percentage'))
def global_usage_windows_have_valid_percentage(
    client: BaqylauClient,
    wait_policy: WaitPolicy,
    harness: str,
) -> None:
    def found() -> list[UsageWindowResponse] | None:
        windows = [
            window
            for row in client.usage.state().usage_rows
            if row.harness == harness
            for window in row.windows
        ]
        valid = windows and all(0 <= window.used_percent <= 100 for window in windows)
        return windows if valid else None

    wait_for(
        f"each global usage window for {harness!r} to have a percentage from 0 to 100",
        found,
        timeout=wait_policy.feed,
    )


@then(parsers.parse('global usage window keys for {harness} are unique per account'))
def global_usage_window_keys_are_unique(
    client: BaqylauClient,
    wait_policy: WaitPolicy,
    harness: str,
) -> None:
    def found() -> list[UsageRowResponse] | None:
        rows = [row for row in client.usage.state().usage_rows if row.harness == harness]
        if not rows:
            return None
        failures = [
            row.display_name
            for row in rows
            if len({window.key for window in row.windows}) != len(row.windows)
        ]
        if failures:
            raise AssertionError(
                f"global usage for {harness!r} has duplicate window keys in {failures}"
            )
        return rows

    wait_for(
        f"global usage for {harness!r} to have unique window keys per account",
        found,
        timeout=wait_policy.feed,
    )
