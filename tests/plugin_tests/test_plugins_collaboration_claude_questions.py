# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude question collaboration tests."""

from __future__ import annotations

from domain import (
    event_work,
)
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event

QUESTION_COUNT = 2


def test_claude_question_preserves_multiple() -> None:
    """Verify claude question preserves multiple prompts and multiselect."""
    translation = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: fixture.QUESTION_ONE,
                fixture.TOOL_NAME_FIELD: fixture.ASK_USER_QUESTION_TOOL,
                fixture.TOOL_INPUT_FIELD: {
                    fixture.QUESTIONS_FIELD: [
                        {
                            fixture.ID_FIELD: "language",
                            fixture.HEADER_FIELD: "Language",
                            fixture.QUESTION_FIELD: "Which languages?",
                            "multiSelect": True,
                            fixture.OPTIONS_FIELD: [
                                {fixture.LABEL_FIELD: "Python", fixture.DESCRIPTION_FIELD: "Backend"},
                                {fixture.LABEL_FIELD: "JavaScript", fixture.DESCRIPTION_FIELD: "Browser"},
                            ],
                        },
                        {
                            fixture.ID_FIELD: "deploy",
                            fixture.QUESTION_FIELD: "Deploy now?",
                            fixture.OPTIONS_FIELD: [],
                        },
                    ],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="ask",
        ),
    )
    asked = payloads(translation, event_work.QuestionAsked)[0].payload
    assert len(asked.questions) == QUESTION_COUNT
    assert asked.questions[0].multiple is True
    assert [choice.label for choice in asked.questions[0].choices] == ["Python", "JavaScript"]


def test_claude_question_resolution_is_canon_not() -> None:
    """Verify claude question resolution is canonical not a native response object."""
    translation = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: fixture.QUESTION_ONE,
                fixture.TOOL_NAME_FIELD: fixture.ASK_USER_QUESTION_TOOL,
                fixture.TOOL_INPUT_FIELD: {
                    fixture.QUESTIONS_FIELD: [
                        {
                            fixture.ID_FIELD: "language",
                            fixture.QUESTION_FIELD: "Which languages?",
                            "multiSelect": True,
                        },
                    ],
                    fixture.ANSWERS_FIELD: {"Which languages?": "Python, JavaScript"},
                },
                fixture.TOOL_RESPONSE_FIELD: {"vendor_field": "not canonical"},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="ask-answer",
        ),
    )

    answered = payloads(translation, event_work.QuestionAnswered)[0].payload

    assert answered.answers[0].prompt_id == "language"
    # Labels, not values: both harnesses answer with the label they were shown,
    # so a second spelling of the same string was a mapping nobody needed.
    assert answered.answers[0].labels == ("Python", "JavaScript")
    assert not hasattr(answered, fixture.TOOL_RESPONSE_FIELD)


def test_claude_refused_question_resolves() -> None:
    """Verify claude refused question resolves from the transcript not a missing hook.

    A refused tool call never runs, so Claude Code fires no PostToolUse for it. The
        transcript's tool_result is the only evidence the question ended — and it names no
        tool, so the resolution depends on remembering the id from the request.
    """
    translator = ClaudeCanonicalTranslator()
    translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: "question-refused",
                fixture.TOOL_NAME_FIELD: fixture.ASK_USER_QUESTION_TOOL,
                fixture.TOOL_INPUT_FIELD: {
                    fixture.QUESTIONS_FIELD: [{fixture.QUESTION_FIELD: "Which approach?", fixture.OPTIONS_FIELD: []}],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="ask-refused",
        ),
    )

    refusal = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "question-refused-result",
                "userFeedback": ("The user wants to clarify these questions. No answer provided."),
                fixture.MESSAGE_FIELD: {
                    fixture.ROLE_FIELD: fixture.USER,
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TOOL_RESULT_ID,
                            fixture.TOOL_USE_ID_FIELD: "question-refused",
                            fixture.IS_ERROR: True,
                            fixture.CONTENT_FIELD: (
                                "The user doesn't want to proceed with this tool use. "
                                "The tool use was rejected. To tell you how to proceed, "
                                "the user said:\nThe user wants to clarify these questions."
                            ),
                        },
                    ],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="ask-refused-result",
        ),
    )

    # A refusal answers nothing, and the harness's own word for the refusal
    # (rejected, discussed) is deliberately not carried: every reader collapsed
    # all of them to one line.
    answered = payloads(refusal, event_work.QuestionAnswered)[0].payload
    assert answered.attention_id == "question-refused"
    assert answered.answers == ()
    assert answered.feedback is None


def test_claude_answered_question_leaves() -> None:
    """Leave a text-only result to the hook when structured answers are absent.

    Both sources share one event identity. An empty answer must not replace
    the structured answer from a later hook.
    """
    translator = ClaudeCanonicalTranslator()
    translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: "question-answered",
                fixture.TOOL_NAME_FIELD: fixture.ASK_USER_QUESTION_TOOL,
                fixture.TOOL_INPUT_FIELD: {
                    fixture.QUESTIONS_FIELD: [{fixture.QUESTION_FIELD: "Which approach?", fixture.OPTIONS_FIELD: []}],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="ask-answered",
        ),
    )

    result = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "question-answered-result",
                fixture.MESSAGE_FIELD: {
                    fixture.ROLE_FIELD: fixture.USER,
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TOOL_RESULT_ID,
                            fixture.TOOL_USE_ID_FIELD: "question-answered",
                            fixture.CONTENT_FIELD: "The user answered: vulture wrapper",
                        },
                    ],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="ask-answered-result",
        ),
    )

    assert payloads(result, event_work.QuestionAnswered) == []
