# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude usage environment and response tests."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from api.common.mapper import usage as api_usage
from harness.impl.claude_code.usage import live as claude_live_usage
from harness.impl.claude_code.usage.rows import ClaudeCodeUsage
from harness.models.usage import UsageWindowSample
from harness.runtime import HarnessRuntimeConfig, default_harness_runtime_configs
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture

if TYPE_CHECKING:
    import pytest


def test_claude_usage_probe_preserves_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify claude usage probe preserves process identity and sets its config."""
    monkeypatch.setenv("HOME", "/Users/current-user")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("USER", "current-user")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "wrong-account")
    runtime = HarnessRuntimeConfig("/bin/claude", Path(fixture.WORK_CLAUDE_HOME_PATH))

    environment = claude_live_usage.subprocess_environment(runtime)

    assert environment["HOME"] == "/Users/current-user"
    assert environment["PATH"] == "/usr/bin:/bin"
    assert environment["USER"] == "current-user"
    assert environment[fixture.CLAUDE_CONFIG_DIR_ENV] == fixture.WORK_CLAUDE_HOME_PATH
    assert "ANTHROPIC_API_KEY" not in environment


def test_claude_default_profile_keeps_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify claude default profile keeps keychain authentication available."""
    monkeypatch.setenv(fixture.CLAUDE_CONFIG_DIR_ENV, "/work/wrong-profile")
    runtime = HarnessRuntimeConfig(
        "/bin/claude",
        Path("/Users/current-user/.claude"),
        use_vendor_default_configuration=True,
    )

    environment = claude_live_usage.subprocess_environment(runtime)

    assert fixture.CLAUDE_CONFIG_DIR_ENV not in environment


def test_claude_usage_row_maps_fable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify claude usage row maps fable and collection diagnostics to the API."""
    live_usage = claude_live_usage.LiveUsage(
        captured_at=1,
        plan="max",
        windows=(
            UsageWindowSample(
                fixture.SEVEN_DAY_FABLE,
                Decimal(fixture.API_USAGE_PERCENT),
                fixture.USAGE_RESET_TIME,
            ),
        ),
    )
    monkeypatch.setattr(
        claude_live_usage,
        "collect",
        lambda _runtime: claude_live_usage.LiveUsageCollection(
            live_usage,
            "profile refresh unavailable",
        ),
    )
    usage_reader = ClaudeCodeUsage(default_harness_runtime_configs().for_harness(CLAUDE_CODE_HARNESS))
    row = usage_reader.read()[0]
    response = api_usage.usage_row(row)

    assert response.windows[0].key == fixture.SEVEN_DAY_FABLE
    assert response.windows[0].scope == fixture.MODEL
    assert response.windows[0].model_id == fixture.FABLE
    assert response.windows[0].resets_at == fixture.USAGE_RESET_TIME
    assert response.collection_error == "profile refresh unavailable"
