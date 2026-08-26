"""Harness-specific prompt conventions used by the live question matrix."""

from tests.e2e.testkit.questions import QuestionWorkDriver
from tests.e2e.testkit.references import SessionSpec
from tests.e2e.steps.questions import choice_label_matches


def test_codex_question_prompt_explains_the_native_free_text_wrapper() -> None:
    prompt = QuestionWorkDriver._native_prompt(
        SessionSpec("codex", "gpt-5.6-luna", "low"),
        "After the user answers, reply only with the exact answer text.",
    )

    assert "text after user_note:" in prompt
    assert "never include user_note: or None of the above" in prompt
    assert prompt.endswith("reply only with the exact answer text.")


def test_choice_label_matcher_tolerates_only_the_native_recommendation_badge() -> None:
    assert choice_label_matches("Blue", "Blue")
    assert choice_label_matches("Blue (Recommended)", "Blue")
    assert not choice_label_matches("Recommended: Blue", "Blue")
    assert not choice_label_matches("Green (Recommended)", "Blue")
