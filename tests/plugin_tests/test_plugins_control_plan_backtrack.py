# Copyright (c) 2026 Zhambyl Yermagambet
"""Cross-harness canonical translation tests from native fixture shapes."""

from __future__ import annotations

import pytest

from domain import (
    ids as domain_ids,
)
from harness.impl.claude_code.controls import (
    plan_models,
    plandialog,
)
from harness.impl.codex.controls import backtrack, composer, composer_state
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.control_draft_support import (
    BacktrackDriver,
    RestoredComposerDriver,
)
from tests.plugin_tests.control_driver_support import (
    CursorScreenDriver,
)

IMPLEMENTATION_CONFIRM_KEY_COUNT = 2


class _ImplementationPromptDriver(CursorScreenDriver):
    def send_key(self, window: domain_ids.WindowId, *pressed: str) -> bool:
        """Clear the implementation prompt after the second Enter key.

        Returns:
            True.

        """
        delivered = super().send_key(window, *pressed)
        if len(self.keys) == IMPLEMENTATION_CONFIRM_KEY_COUNT:
            self.screen = "Working"
        return delivered


@pytest.mark.parametrize(
    (fixture.SCREEN, "keys"),
    [
        (
            "Would you like to proceed?\n\u276f 1. Yes, and bypass permissions\n  2. Tell Claude what to change",
            [fixture.ENTER],
        ),
        (
            "Would you like to proceed?\n  1. Yes, and bypass permissions\n\u276f 2. Tell Claude what to change",
            [fixture.UP, fixture.ENTER],
        ),
    ],
)
def test_claude_plan_decision_uses_cursor(
    screen: str,
    keys: list[str],
) -> None:
    """Verify claude plan decision uses cursor navigation."""
    driver = CursorScreenDriver(
        screen,
        {
            (
                fixture.UP,
            ): "Would you like to proceed?\n\u276f 1. Yes, and bypass permissions\n  2. Tell Claude what to change",
            (fixture.ENTER,): "",
        },
    )

    outcome = plandialog.decide(
        driver,
        domain_ids.WindowId(fixture.WINDOW_ONE_ID),
        fixture.ONE_TEXT,
        "Yes, and bypass permissions",
        sleep=lambda _seconds: None,
    )

    assert outcome == plan_models.Decided("Yes, and bypass permissions")
    assert driver.keys == keys


def test_claude_plan_submits_confirmation() -> None:
    """Verify Claude submits the new implementation confirmation prompt."""
    driver = _ImplementationPromptDriver(
        "Would you like to proceed?\n\u276f 1. Yes, and bypass permissions",
        {
            (fixture.ENTER,): "\u276f Implement the plan.",
        },
    )

    plandialog.decide(
        driver,
        domain_ids.WindowId(fixture.WINDOW_ONE_ID),
        fixture.ONE_TEXT,
        "Yes, and bypass permissions",
        sleep=lambda _seconds: None,
    )

    assert driver.keys == [fixture.ENTER, fixture.ENTER]


def test_claude_plan_feedback_uses_cursor() -> None:
    """Verify claude plan feedback uses cursor navigation before text."""
    driver = CursorScreenDriver(
        "Would you like to proceed?\n\u276f 1. Yes, and bypass permissions\n  2. Tell Claude what to change",
        {
            (
                fixture.DOWN,
            ): "Would you like to proceed?\n  1. Yes, and bypass permissions\n\u276f 2. Tell Claude what to change",
            (fixture.ENTER,): "Would you like to proceed?\nTell Claude what to change:",
        },
    )

    outcome = plandialog.feedback(
        driver,
        domain_ids.WindowId(fixture.WINDOW_ONE_ID),
        "start with the tests",
        sleep=lambda _seconds: None,
    )

    assert outcome == plan_models.Fedback(feedback=True)
    assert driver.keys == [fixture.DOWN, fixture.ENTER]
    assert driver.text == ["start with the tests"]


def test_codex_backtrack_selects_named_prompt() -> None:
    """Verify codex backtrack selects a named prompt before it confirms."""
    prompts = ("Reply only with the word first.", "Reply only with the word second.")
    driver = BacktrackDriver(prompts)

    backtrack.drive(
        driver,
        domain_ids.WindowId(fixture.WINDOW_ONE_ID),
        prompts[0],
        newer_prompt_count=1,
        sleep=lambda _seconds: None,
    )

    assert driver.keys == [fixture.ESCAPE, fixture.ESCAPE, "left", fixture.ENTER]


def test_codex_backtrack_can_verify_plain_pty() -> None:
    """Verify codex backtrack can verify a plain pty transcript."""
    screen = f"{backtrack.TRANSCRIPT_HEADER}\n\u203a Reply only with the word first.\n{backtrack.TRANSCRIPT_FOOTER}"

    assert backtrack.selected_prompt(screen, "Reply only with the word first.")


def test_codex_composer_clears_complete_restored() -> None:
    """Verify codex composer clears the complete restored draft."""
    driver = RestoredComposerDriver()

    composer.clear(driver, domain_ids.WindowId(fixture.WINDOW_ONE_ID), sleep=lambda _seconds: None)

    assert driver.keys == [
        fixture.CLEAR_BEFORE_CURSOR_KEY,
        fixture.CLEAR_AFTER_CURSOR_KEY,
        fixture.BACKSPACE,
        fixture.CLEAR_BEFORE_CURSOR_KEY,
        fixture.CLEAR_AFTER_CURSOR_KEY,
        fixture.BACKSPACE,
        fixture.CLEAR_BEFORE_CURSOR_KEY,
        fixture.CLEAR_AFTER_CURSOR_KEY,
    ]
    assert composer_state.empty(driver.read_text(domain_ids.WindowId(fixture.WINDOW_ONE_ID)))
