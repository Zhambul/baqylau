# Copyright (c) 2026 Zhambyl Yermagambet
"""Native launcher startup screen tests."""

from __future__ import annotations

import pytest

from harness.models.launch import LaunchRequest
from tests.fake_terminal import FakeTerminal, window
from tests.harness_names import CLAUDE_CODE_HARNESS, CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_launch import _StartupTerminal, _test_launcher


def test_claude_approves_managed_settings() -> None:
    """Verify claude approves managed settings and workspace trust."""
    terminal = _StartupTerminal(
        (
            "Managed settings require approval\nYes, I trust these settings",
            "Do you trust the files in this folder?",
        ),
    )
    launcher = _test_launcher(CLAUDE_CODE_HARNESS, terminal)

    result = launcher.launch(
        LaunchRequest(fixture.WORK_PATH, fixture.HELLO, None, None, None, None),
    )

    assert result.status == fixture.STARTED
    assert terminal.keys == [
        (fixture.WINDOW_TWO_ID, fixture.ENTER),
        (fixture.WINDOW_TWO_ID, fixture.ENTER),
    ]


def test_claude_selects_workspace_trust() -> None:
    """Select Yes when the current trust screen starts on No."""
    terminal = _StartupTerminal((
        "Accessing workspace:\n\u276f No, exit\n  Yes, I trust this folder",
        "Accessing workspace:\n  No, exit\n\u276f Yes, I trust this folder",
    ))
    result = _test_launcher(CLAUDE_CODE_HARNESS, terminal).launch(
        LaunchRequest(fixture.WORK_PATH, fixture.HELLO, None, None, None, None),
    )
    assert result.status == fixture.STARTED
    assert terminal.keys == [(fixture.WINDOW_TWO_ID, "down"), (fixture.WINDOW_TWO_ID, fixture.ENTER)]


@pytest.mark.parametrize(
    (fixture.SCREEN, fixture.REASON_FIELD),
    [
        (
            "Choose the text style that looks best with your terminal\nTo change this later, run /theme",
            "needs onboarding",
        ),
        ("Select login method:", "needs you to sign in"),
    ],
)
def test_claude_reports_onboarding_and_login(
    screen: str,
    reason: str,
) -> None:
    """Verify claude reports onboarding and login as launch errors."""
    terminal = FakeTerminal(windows=[window(fixture.WINDOW_TWO_ID)], screen_text=screen)
    launcher = _test_launcher(CLAUDE_CODE_HARNESS, terminal)

    result = launcher.launch(
        LaunchRequest(fixture.WORK_PATH, fixture.HELLO, None, None, None, None),
    )

    assert result.status == fixture.REJECTED
    assert reason in (result.reason or "")
    assert result.window_id == fixture.WINDOW_TWO_ID


def test_codex_approves_workspace_trust() -> None:
    """Verify codex approves workspace trust."""
    terminal = _StartupTerminal(("Do you trust this directory?",))
    launcher = _test_launcher(CODEX_HARNESS, terminal)

    result = launcher.launch(
        LaunchRequest(fixture.WORK_PATH, fixture.HELLO, None, None, None, None),
    )

    assert result.status == fixture.STARTED
    assert terminal.keys == [(fixture.WINDOW_TWO_ID, fixture.ENTER)]


def test_codex_reports_login_as_a_launch_error() -> None:
    """Verify codex reports login as a launch error."""
    terminal = FakeTerminal(
        windows=[window(fixture.WINDOW_TWO_ID)],
        screen_text=("Welcome to Codex, OpenAI's command-line coding agent\nSign in with ChatGPT"),
    )
    launcher = _test_launcher(CODEX_HARNESS, terminal)

    result = launcher.launch(
        LaunchRequest(fixture.WORK_PATH, fixture.HELLO, None, None, None, None),
    )

    assert result.status == fixture.REJECTED
    assert "needs you to sign in" in (result.reason or "")
    assert result.window_id == fixture.WINDOW_TWO_ID


@pytest.mark.parametrize(
    "state",
    [
        fixture.ASK_CODEX_TO_DO_ANYTHING_TEXT,
        "Working (20s • esc to interrupt)",
    ],
)
def test_codex_accepts_its_normal_main_screen(state: str) -> None:
    """Verify codex accepts its normal main screen before the session tag."""
    terminal = FakeTerminal(
        windows=[window(fixture.WINDOW_TWO_ID)],
        screen_text=(f"╭──────────────────────╮\n│ >_ OpenAI Codex      │\n╰──────────────────────╯\n{state}"),
    )
    launcher = _test_launcher(CODEX_HARNESS, terminal)

    result = launcher.launch(
        LaunchRequest(fixture.WORK_PATH, fixture.HELLO, None, None, None, None),
    )

    assert result.status == fixture.STARTED
    assert result.window_id == fixture.WINDOW_TWO_ID
