# Copyright (c) 2026 Zhambyl Yermagambet
"""Native launcher command tests."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from domain.ids import AccountId, HarnessName
from harness.models import controls, launch
from terminal.models.tabs import EnvironmentVariable
from terminal.models.values import SESSION_WINDOW_TAG
from tests.fake_terminal import FakeTerminal, window
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.native_launch_support import _native_launch_results
from tests.plugin_tests.support_launch import _StartupTerminal, _test_launcher

if TYPE_CHECKING:
    import pytest


CODEX_HARNESS = HarnessName("codex")
CLAUDE_CODE_HARNESS = HarnessName("claude_code")
VISIBLE_DRAFT = "This is the last visible prompt line: hello"
# The native start page is the only screen that draws this logo.
START_PAGE_LOGO = "█▀▀█ █▀▀█"


def test_launchers_build_native_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify launchers build native commands and share terminal launch mechanics."""
    monkeypatch.delenv(fixture.CLAUDE_CONFIG_DIR_ENV)
    monkeypatch.delenv("CLAUDE_CODE_MANAGED_SETTINGS_PATH", raising=False)
    terminal = FakeTerminal(
        windows=[window(fixture.WINDOW_TWO_ID, tags={SESSION_WINDOW_TAG: fixture.SESSION_ONE_ID})],
    )
    claude_result, codex_result = _native_launch_results(terminal)

    assert claude_result.status == codex_result.status == fixture.STARTED
    assert terminal.opened_tabs[0].command == (
        fixture.CLAUDE,
        "--dangerously-skip-permissions",
        "--model",
        fixture.FABLE,
        "--effort",
        fixture.HIGH,
        "hello @/work/context.md",
    )
    assert terminal.opened_tabs[1].command == (
        fixture.CODEX_HARNESS,
        "-C",
        fixture.WORK_PATH,
        "-m",
        "gpt-5.6-terra",
        "-c",
        "model_reasoning_effort=high",
        "-c",
        'model_reasoning_summary="concise"',
        "/work/context.md\nhello",
    )
    assert terminal.opened_tabs[0].environment == (
        EnvironmentVariable(fixture.DASHBOARD_PORT_ENV, fixture.DASHBOARD_PORT_TEXT),
        EnvironmentVariable(fixture.CLAUDE_CONFIG_DIR_ENV, fixture.WORK_CLAUDE_HOME_PATH),
        EnvironmentVariable("BAQYLAU_LAUNCH_MODEL", fixture.FABLE),
        EnvironmentVariable("BAQYLAU_LAUNCH_EFFORT", fixture.HIGH),
    )
    assert terminal.opened_tabs[1].environment == (
        EnvironmentVariable(fixture.DASHBOARD_PORT_ENV, fixture.DASHBOARD_PORT_TEXT),
        EnvironmentVariable(fixture.CODEX_HOME_ENV, fixture.WORK_CODEX_HOME_PATH),
    )


def test_claude_rejects_legacy_account_selection() -> None:
    """Verify claude rejects legacy account selection."""
    terminal = FakeTerminal()
    result = _test_launcher(CLAUDE_CODE_HARNESS, terminal).launch(
        launch.LaunchRequest(
            working_directory=fixture.WORK_PATH,
            initial_text=fixture.HELLO,
            model=None,
            effort=None,
            account_id=AccountId("legacy-account"),
            resume_session_id=None,
        ),
    )

    assert result.status == fixture.REJECTED
    assert "does not support account selection" in (result.reason or "")
    assert not terminal.opened_tabs


def test_harness_that_announces_at_its_first_turn() -> None:
    """Verify a harness that announces at its first turn refuses an empty launch."""
    terminal = FakeTerminal(
        windows=[window(fixture.WINDOW_TWO_ID, tags={SESSION_WINDOW_TAG: fixture.SESSION_ONE_ID})],
    )
    codex = _test_launcher(CODEX_HARNESS, terminal)
    empty = launch.LaunchRequest(
        working_directory=fixture.WORK_PATH,
        initial_text="   ",
        model=None,
        effort=None,
        account_id=None,
        resume_session_id=None,
    )

    rejected = codex.launch(empty)
    assert rejected.status == fixture.REJECTED
    assert "needs a first message" in (rejected.reason or "")
    assert not terminal.opened_tabs

    attached = codex.launch(
        replace(
            empty,
            initial_text=None,
            attachments=(controls.AttachmentReference("/work/context.md", "context.md"),),
        ),
    )
    assert attached.status == fixture.STARTED


def test_launch_checks_native_draft_submission() -> None:
    """A successful key write must not hide an unchanged native draft."""
    draft = (
        f"{START_PAGE_LOGO}\n┃ {VISIBLE_DRAFT}\n"
        "Build · DeepSeek V4.1 Flash · low\nshift+tab agents  ctrl+p commands"
    )
    terminal = _StartupTerminal((draft, draft, "Working  shift+tab agents  ctrl+p commands"))
    result = _test_launcher(HarnessName("opencode2"), terminal).launch(
        launch.LaunchRequest(
            working_directory=fixture.WORK_PATH,
            initial_text=f"Hidden text above the screen. {VISIBLE_DRAFT}",
            model=None,
            effort=None,
            account_id=None,
            resume_session_id=None,
        ),
    )
    assert result.status == fixture.STARTED
    assert [key for _window, key in terminal.keys] == ["enter", "enter"]
    assert terminal.screen_text == "Working  shift+tab agents  ctrl+p commands"
    assert EnvironmentVariable(
        "OPENCODE_CONFIG", "/work/opencode2-settings.json",
    ) in terminal.opened_tabs[0].environment
