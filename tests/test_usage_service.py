# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide the test usage service module."""

from decimal import Decimal
from pathlib import Path

import pytest

from harness.models.usage import (
    UsageRow,
)
from harness.registry import HarnessRegistry
from harness.services.usage import (
    USAGE_INITIAL_DELAY_VARIABLE,
    USAGE_REFRESH_SECONDS,
    USAGE_REFRESH_VARIABLE,
    USAGE_SHARED_CACHE_SECONDS_VARIABLE,
    ApplicationUsageState,
    HarnessUsageService,
)
from tests.harness_names import CODEX_HARNESS
from tests.usage_test_support import (
    READS_BEFORE_STOP,
    FirstReadFailureUsageSource,
    RecordingUsageSource,
    UsageRetryStop,
)

CONFIGURED_INITIAL_DELAY_SECONDS = 12.5
SHARED_CACHE_MAX_AGE_SECONDS = 600


def test_app_usage_state_uses_configured_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify application usage state uses configured polling intervals."""
    monkeypatch.setenv(USAGE_INITIAL_DELAY_VARIABLE, "12.5")
    monkeypatch.setenv(USAGE_REFRESH_VARIABLE, "60")

    state = ApplicationUsageState.configured(RecordingUsageSource())

    assert state.initial_delay_seconds == pytest.approx(CONFIGURED_INITIAL_DELAY_SECONDS)
    assert state.refresh_seconds == pytest.approx(60.0)


def test_app_usage_state_falls_back_for_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify application usage state falls back for invalid polling intervals."""
    monkeypatch.setenv(USAGE_INITIAL_DELAY_VARIABLE, "invalid")
    monkeypatch.setenv(USAGE_REFRESH_VARIABLE, "invalid")

    state = ApplicationUsageState.configured(RecordingUsageSource())

    assert state.initial_delay_seconds == pytest.approx(0)
    assert state.refresh_seconds == USAGE_REFRESH_SECONDS


def test_harness_usage_service_uses_configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify harness usage service uses configured shared cache age."""
    monkeypatch.setenv("BAQYLAU_USAGE_SHARED_CACHE", str(tmp_path / "usage.json"))
    monkeypatch.setenv(USAGE_SHARED_CACHE_SECONDS_VARIABLE, "600")

    service = HarnessUsageService(HarnessRegistry())

    assert service.shared_cache is not None
    assert service.shared_cache.max_age_seconds == SHARED_CACHE_MAX_AGE_SECONDS


def test_app_usage_state_retries_after_transient() -> None:
    """Verify application usage state retries after a transient source failure."""
    source = FirstReadFailureUsageSource()
    stop = UsageRetryStop(source)

    ApplicationUsageState(source, refresh_seconds=60).run(stop)

    assert source.calls == READS_BEFORE_STOP
    assert stop.delays == [5.0, 60]


def test_app_usage_state_publishes_only_changed() -> None:
    """Verify application usage state publishes only changed rows."""
    row = UsageRow(
        harness=CODEX_HARNESS,
        account_id=None,
        display_name="Default",
        switchable=False,
        default_for_launch=True,
        plan="pro",
        windows=(),
        scheduling_score=Decimal(1),
        scheduling_allowed=True,
        limit=None,
        authentication_error=None,
    )
    source = RecordingUsageSource()
    published_changes: list[str] = []
    state = ApplicationUsageState(source, changed=lambda: published_changes.append("changed"))

    state.refresh()
    source.rows = (row,)
    state.refresh()
    state.refresh()

    assert published_changes == ["changed"]
