# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude usage collection tests."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from harness.impl.claude_code.otel import gateway as claude_telemetry
from harness.impl.claude_code.usage import live as claude_live_usage
from harness.impl.claude_code.usage.rows import ClaudeCodeUsage
from harness.models.telemetry import HarnessTelemetryRequest
from harness.runtime import HarnessRuntimeConfig, default_harness_runtime_configs
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import support_launch, vocabulary as fixture


@pytest.fixture(autouse=True)
def isolated_usage_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use a fresh cache for each test and restore the previous cache after it."""
    monkeypatch.setattr(claude_live_usage, "_cache", [])


def test_daemon_ignores_old_statusline_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """An old status-line process cannot add stale usage to the live row."""
    monkeypatch.setattr(
        claude_live_usage,
        "collect",
        lambda _runtime: claude_live_usage.LiveUsageCollection(
            None,
            None,
        ),
    )
    body = json.dumps(
        {
            fixture.SESSION_ID_FIELD: "session-usage",
            "rate_limits": {
                "five_hour": {
                    "used_percentage": fixture.FIVE_HOUR_USAGE_PERCENT,
                    fixture.RESETS_AT: fixture.USAGE_RESET_TIME,
                },
                "seven_day": {"used_percentage": 40, fixture.RESETS_AT: 2_000_100_000},
            },
            "_account_id": "work",
            "_account_name": "Work",
        },
    ).encode()

    response = claude_telemetry.ClaudeTelemetryGateway().receive_telemetry(
        HarnessTelemetryRequest("statusline", body),
        support_launch.NoSessions(),
    )
    usage_reader = ClaudeCodeUsage(default_harness_runtime_configs().for_harness(CLAUDE_CODE_HARNESS))
    rows = usage_reader.read()
    row = rows[0]

    assert not response.raw_events
    assert (
        row.account_id,
        row.display_name,
        row.windows,
        row.scheduling_score,
        row.scheduling_allowed,
    ) == (None, fixture.CLAUDE, (), None, False)


def test_claude_usage_ignores_new_provider_fields() -> None:
    """Verify claude usage ignores new provider fields and reads required limits."""
    response = support_launch.claude_usage_response()

    samples = claude_live_usage.windows(response.rate_limits)
    assert [sample.key for sample in samples] == [
        "five_hour",
        "seven_day",
        fixture.SEVEN_DAY_FABLE,
    ]
    assert samples[-1].used_percent == Decimal(fixture.CLAUDE_USAGE_PERCENT)
    assert samples[-1].resets_at is not None


def test_claude_usage_cache_is_isolated_for_each(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify claude usage cache is isolated for each config directory."""
    monkeypatch.setattr(fixture.CLAUDE_USAGE_CLOCK_TARGET, lambda: 100.0)
    monkeypatch.setattr(
        claude_live_usage,
        fixture.REQUEST_USAGE_ID,
        lambda runtime: claude_live_usage.ProbeResult(
            support_launch.claude_usage_response(
                "Fable" if runtime.configuration_directory.name == "first" else "Opus",
                (
                    fixture.FIRST_PROFILE_USAGE_PERCENT
                    if runtime.configuration_directory.name == "first"
                    else fixture.SECOND_PROFILE_USAGE_PERCENT
                ),
            ),
            None,
        ),
    )

    first = claude_live_usage.collect(
        HarnessRuntimeConfig(fixture.CLAUDE, Path("/work/first")),
    )
    second = claude_live_usage.collect(
        HarnessRuntimeConfig(fixture.CLAUDE, Path("/work/second")),
    )

    assert first.usage is not None
    assert second.usage is not None
    assert (
        first.usage.windows[-1].key,
        second.usage.windows[-1].key,
    ) == (
        fixture.SEVEN_DAY_FABLE,
        "seven_day_opus",
    )
    assert first.usage.windows[-1].used_percent == Decimal(fixture.FIRST_PROFILE_USAGE_PERCENT)
    assert second.usage.windows[-1].used_percent == Decimal(fixture.SECOND_PROFILE_USAGE_PERCENT)


def test_claude_temporary_probe_failure_keeps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify claude temporary probe failure keeps the last provider result."""
    responses = iter(
        (
            claude_live_usage.ProbeResult(
                support_launch.claude_usage_response(percent=fixture.RECOVERABLE_PROFILE_USAGE_PERCENT),
                None,
            ),
            claude_live_usage.ProbeResult(
                None,
                claude_live_usage.ProbeFailure(message="Claude usage probe timed out", recoverable=True),
            ),
        ),
    )
    now = iter((100.0, 221.0))
    monkeypatch.setattr(fixture.CLAUDE_USAGE_CLOCK_TARGET, lambda: next(now))
    monkeypatch.setattr(
        claude_live_usage,
        fixture.REQUEST_USAGE_ID,
        lambda _config_directory: next(responses),
    )

    first = claude_live_usage.collect()
    second = claude_live_usage.collect()

    assert first.usage is not None
    assert second.error is None
    assert second.usage == first.usage


def test_claude_permanent_probe_failure_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify claude permanent probe failure clears usage and reports error."""
    responses = iter(
        (
            claude_live_usage.ProbeResult(support_launch.claude_usage_response(), None),
            claude_live_usage.ProbeResult(
                None,
                claude_live_usage.ProbeFailure(message="Claude login was revoked", recoverable=False),
            ),
        ),
    )
    now = iter((100.0, 221.0))
    monkeypatch.setattr(fixture.CLAUDE_USAGE_CLOCK_TARGET, lambda: next(now))
    monkeypatch.setattr(
        claude_live_usage,
        fixture.REQUEST_USAGE_ID,
        lambda _config_directory: next(responses),
    )

    assert claude_live_usage.collect().usage is not None
    failed = claude_live_usage.collect()

    assert failed.usage is None
    assert failed.error == "Claude login was revoked"


def test_claude_temporary_failure_does_not_keep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify claude temporary failure does not keep an old result."""
    responses = iter(
        (
            claude_live_usage.ProbeResult(support_launch.claude_usage_response(), None),
            claude_live_usage.ProbeResult(
                None,
                claude_live_usage.ProbeFailure(message="Claude usage probe timed out", recoverable=True),
            ),
        ),
    )
    now = iter((0, fixture.EXPIRED_USAGE_CLOCK_TIME))
    monkeypatch.setattr(
        fixture.CLAUDE_USAGE_CLOCK_TARGET,
        lambda: next(now),
    )
    monkeypatch.setattr(
        claude_live_usage,
        fixture.REQUEST_USAGE_ID,
        lambda _config_directory: next(responses),
    )

    assert claude_live_usage.collect().usage is not None
    collection = claude_live_usage.collect()

    assert collection == claude_live_usage.LiveUsageCollection(None, None)
