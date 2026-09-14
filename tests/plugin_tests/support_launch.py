# Copyright (c) 2026 Zhambyl Yermagambet
"""Shared fixtures and builders for canonical harness tests."""

from pathlib import Path
from types import SimpleNamespace

from domain import ids as domain_ids
from harness import contract as harness_contract
from harness.impl.claude_code.usage import live as claude_live_usage
from harness.impl.discovery import installed
from harness.runtime import (
    HarnessRuntimeConfig,
    HarnessRuntimeConfigs,
    HarnessRuntimeEntry,
)
from terminal.models.input import (
    KeySendRequest,
    KeySendResponse,
)
from terminal.models.values import (
    SESSION_WINDOW_TAG,
)
from tests.fake_terminal import FakeTerminal, window
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_audit import silent_audit

CODEX_HARNESS = domain_ids.HarnessName("codex")
CLAUDE_CODE_HARNESS = domain_ids.HarnessName("claude_code")


class NoSessions:
    """A telemetry context for a delivery that names no session."""

    def find_session(self, _session_id: domain_ids.SessionId) -> None:
        """Return no session.

        Returns:
            None for every session identifier.

        """


def claude_usage_response(
    model: str = "Fable",
    percent: int = 47,
) -> claude_live_usage.GetUsageResponse:
    """Build native usage data with a model-specific weekly limit.

    Returns:
        Validated usage data with the supplied model name and percentage.

    """
    return claude_live_usage.GetUsageResponse.model_validate(
        {
            "rate_limits": {
                "five_hour": {"utilization": 12, fixture.RESETS_AT: None},
                "seven_day": {
                    "utilization": 34,
                    fixture.RESETS_AT: "2026-08-26T00:00:00+00:00",
                },
                "limits": [
                    {
                        fixture.KIND_FIELD: "weekly_scoped",
                        "percent": percent,
                        fixture.RESETS_AT: "2026-08-27T00:00:00+00:00",
                        "scope": {fixture.MODEL: {"display_name": model}},
                    },
                ],
                "juniper_tide": {"utilization": 3, "new_field": True},
            },
            "rate_limits_available": True,
            "subscription_type": "max",
            "future_top_level_field": {fixture.VALUE_FIELD: 1},
        },
    )


def _test_launcher(harness: domain_ids.HarnessName, terminal: FakeTerminal) -> harness_contract.HarnessLauncher:
    runtime_configs = HarnessRuntimeConfigs(
        (
            HarnessRuntimeEntry(
                CLAUDE_CODE_HARNESS,
                HarnessRuntimeConfig(
                    fixture.CLAUDE,
                    Path(fixture.WORK_CLAUDE_HOME_PATH),
                    Path("/work/claude-home/managed-settings.json"),
                ),
            ),
            HarnessRuntimeEntry(
                CODEX_HARNESS,
                HarnessRuntimeConfig(fixture.CODEX_HARNESS, Path(fixture.WORK_CODEX_HOME_PATH)),
            ),
            HarnessRuntimeEntry(
                domain_ids.HarnessName("opencode2"),
                HarnessRuntimeConfig(
                    "opencode2", Path("/work/opencode2-events"), Path("/work/opencode2-settings.json"),
                ),
            ),
        ),
    )
    plugin = next(
        plugin
        for plugin in installed(
            runtime_configs,
            terminal.plugin(),
            SimpleNamespace(resumed=lambda *_arguments: None),
            silent_audit(),
        )
        if plugin.harness_info.name == harness
    )
    assert plugin.launcher is not None
    return plugin.launcher


class _StartupTerminal(FakeTerminal):
    def __init__(self, screens: tuple[str, ...]) -> None:
        startup_window = window(fixture.WINDOW_TWO_ID)
        super().__init__(windows=[startup_window], screen_text=screens[0])
        self.screens = list(screens)

    def send_key(self, request: KeySendRequest) -> KeySendResponse:
        result = super().send_key(request)
        self.screens.pop(0)
        if self.screens:
            self.screen_text = self.screens[0]
        else:
            self.windows_on_screen = [
                window(fixture.WINDOW_TWO_ID, tags={SESSION_WINDOW_TAG: fixture.SESSION_ONE_ID}),
            ]
            self.screen_text = ""
        return result
