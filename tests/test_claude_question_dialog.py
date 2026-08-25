"""Claude Code question screens remain identifiable when the viewport clips them."""

from harness.impl.claude_code.canonical.records import Question, QuestionOption
from harness.impl.claude_code.controls.askdialog_screen import current_question
from harness.impl.claude_code.controls.screen_driver import (
    SCREEN_LIMIT,
    StepError,
    failure_detail,
)


def test_screen_driver_failure_keeps_only_a_bounded_screen_tail():
    screen = "discarded-prefix:" + "x" * SCREEN_LIMIT

    detail = failure_detail(StepError("open", "menu missing", screen))

    assert detail.startswith("open: menu missing; screen=")
    assert "discarded-prefix" not in detail
    assert "x" * SCREEN_LIMIT in detail


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
