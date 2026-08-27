"""Claude Code question screens remain identifiable when the viewport clips them."""

from typing import cast

import pytest

from domain.ids import WindowId
from harness.impl.claude_code.canonical.records import Question, QuestionOption
from harness.impl.claude_code.controls import askdialog
from harness.impl.claude_code.controls.askdialog import cursor_to
from harness.impl.claude_code.controls.askdialog_screen import current_question
from harness.impl.claude_code.controls.screen_driver import (
    SCREEN_LIMIT,
    ScreenDriver,
    StepError,
    failure_detail,
)


class ClippedCursorDriver:
    def __init__(self) -> None:
        self.state = 0

    def get_text(self, window_id: WindowId) -> str:
        del window_id
        if self.state == 0:
            return "  2. Green\n  3. Red\nEnter to select"
        if self.state == 1:
            return "❯ 2. Green\n  3. Red\nEnter to select"
        return "❯ 1. Blue\n  2. Green\nEnter to select"

    def send_key(self, window_id: WindowId, key: str) -> bool:
        del window_id
        if self.state == 0 and key == "down":
            self.state = 1
        elif self.state == 1 and key == "up":
            self.state = 2
        return True


def test_screen_driver_failure_keeps_only_a_bounded_screen_tail():
    screen = "discarded-prefix:" + "x" * SCREEN_LIMIT

    detail = failure_detail(StepError("open", "menu missing", screen))

    assert detail.startswith("open: menu missing; screen=")
    assert "discarded-prefix" not in detail
    assert "x" * SCREEN_LIMIT in detail


def test_cursor_navigation_reveals_a_selected_row_above_the_viewport():
    driver = ClippedCursorDriver()

    screen = cursor_to(
        cast(ScreenDriver, driver),
        WindowId("window"),
        lambda row: row.digit == "1",
        lambda _seconds: None,
        "option 1",
    )

    assert "❯ 1. Blue" in screen


def test_cursor_navigation_does_not_repeat_an_unverified_down_key():
    class FrozenDriver:
        def __init__(self) -> None:
            self.keys: list[str] = []

        def get_text(self, window_id: WindowId) -> str:
            del window_id
            return "  1. Blue\n  2. Green\nEnter to select"

        def send_key(self, window_id: WindowId, key: str) -> bool:
            del window_id
            self.keys.append(key)
            return True

    driver = FrozenDriver()

    with pytest.raises(askdialog.AskError, match="down key had no visible effect"):
        cursor_to(
            cast(ScreenDriver, driver),
            WindowId("window"),
            lambda row: row.digit == "2",
            lambda _seconds: None,
            "option 2",
        )

    assert driver.keys == ["down"]


def test_question_driver_restores_temporary_viewport_growth(monkeypatch):
    class ResizeDriver:
        def __init__(self) -> None:
            self.resizes: list[int] = []

        def lines(self, window_id: WindowId) -> int:
            del window_id
            return 24

        def resize_lines(self, window_id: WindowId, cells: int) -> bool:
            del window_id
            self.resizes.append(cells)
            return True

    driver = ResizeDriver()
    monkeypatch.setattr(
        askdialog,
        "_drive_dialog",
        lambda *_args, **_kwargs: askdialog.AskOutcome.SUBMITTED,
    )

    outcome = askdialog.drive(
        cast(ScreenDriver, driver),
        WindowId("window"),
        [],
        [],
        sleep=lambda _seconds: None,
    )

    assert outcome == askdialog.AskOutcome.SUBMITTED
    assert driver.resizes == [36, -36]


def test_visible_unique_options_identify_a_question_whose_prompt_is_above_the_viewport():
    questions = [
        Question(
            question="Which base should I use?",
            options=[QuestionOption(label="Remote base"), QuestionOption(label="Local base")],
        ),
        Question(
            question="Which regression scope should I use?",
            options=[
                QuestionOption(label="Full regression"),
                QuestionOption(label="Feature only"),
                QuestionOption(label="Blocker only"),
            ],
        ),
    ]
    clipped_screen = """
      1. Full regression
         Cover every affected adapter.
    ❯ 2. Feature only
         Keep the checks on this feature.
      3. Blocker only
         Report the blocker without more checks.
      4. Type something.

      Enter to select
    """

    assert current_question(clipped_screen, questions) == 1


def test_repeated_option_labels_do_not_guess_a_clipped_question():
    questions = [
        Question(question="First?", options=[QuestionOption(label="Yes")]),
        Question(question="Second?", options=[QuestionOption(label="Yes")]),
    ]
    clipped_screen = """
    ❯ 1. Yes
      2. Type something.
      Enter to select
    """

    assert current_question(clipped_screen, questions) is None
