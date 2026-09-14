# Copyright (c) 2026 Zhambyl Yermagambet
"""Codex question collaboration tests."""

from __future__ import annotations

import json

from domain import (
    event_work,
)
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from tests.harness_names import CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event


def test_codex_question_uses_same_attention() -> None:
    """Verify codex question uses the same attention prompt model."""
    translation = CodexCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.FUNCTION_CALL_ID,
                    fixture.NAME_FIELD: fixture.REQUEST_USER_INPUT_ID,
                    fixture.CALL_ID_FIELD: "ask-one",
                    fixture.ARGUMENTS_FIELD: json.dumps(
                        {
                            fixture.QUESTIONS_FIELD: [
                                {
                                    fixture.ID_FIELD: "choice",
                                    fixture.HEADER_FIELD: "Choice",
                                    fixture.QUESTION_FIELD: fixture.CONTINUE,
                                    fixture.OPTIONS_FIELD: [
                                        {fixture.LABEL_FIELD: "Yes", fixture.DESCRIPTION_FIELD: "Proceed"},
                                    ],
                                },
                            ],
                        },
                    ),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="ask",
        ),
    )
    asked = payloads(translation, event_work.QuestionAsked)[0].payload
    first_choice = asked.questions[0].choices[0]
    assert asked.questions[0].prompt == fixture.CONTINUE
    assert first_choice.description == "Proceed"


def test_codex_question_result_records_selected() -> None:
    """Verify codex question result records the selected labels."""
    translator = CodexCanonicalTranslator()
    asked = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.FUNCTION_CALL_ID,
                    fixture.NAME_FIELD: fixture.REQUEST_USER_INPUT_ID,
                    fixture.CALL_ID_FIELD: "ask-one",
                    fixture.ARGUMENTS_FIELD: json.dumps(
                        {
                            fixture.QUESTIONS_FIELD: [
                                {
                                    fixture.ID_FIELD: fixture.COLOUR,
                                    fixture.HEADER_FIELD: "Colour",
                                    fixture.QUESTION_FIELD: "Which colour?",
                                    fixture.OPTIONS_FIELD: [
                                        {fixture.LABEL_FIELD: "Blue", fixture.DESCRIPTION_FIELD: "Use blue"},
                                        {fixture.LABEL_FIELD: "Green", fixture.DESCRIPTION_FIELD: "Use green"},
                                    ],
                                },
                            ],
                        },
                    ),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="ask",
            source_position=fixture.TEN_TEXT,
        ),
    )
    answered = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.FUNCTION_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: "ask-one",
                    fixture.OUTPUT_FIELD: json.dumps({
                        fixture.ANSWERS_FIELD: {fixture.COLOUR: {fixture.ANSWERS_FIELD: ["Green"]}},
                    }),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="answer",
            source_position=fixture.ELEVEN_TEXT,
        ),
    )

    question = payloads(asked, event_work.QuestionAsked)[0].payload.questions[0]
    answer = payloads(answered, event_work.QuestionAnswered)[0].payload.answers[0]
    assert answer.prompt_id == question.prompt_id
    assert answer.labels == ("Green",)


def test_codex_interrupted_question_resolves() -> None:
    """Verify codex interrupted question resolves without answers."""
    translator = CodexCanonicalTranslator()
    translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.FUNCTION_CALL_ID,
                    fixture.NAME_FIELD: fixture.REQUEST_USER_INPUT_ID,
                    fixture.CALL_ID_FIELD: "ask-interrupted",
                    fixture.ARGUMENTS_FIELD: json.dumps(
                        {
                            fixture.QUESTIONS_FIELD: [
                                {
                                    fixture.ID_FIELD: "path",
                                    fixture.HEADER_FIELD: "Path",
                                    fixture.QUESTION_FIELD: "Which path?",
                                    fixture.OPTIONS_FIELD: [
                                        {
                                            fixture.LABEL_FIELD: "Safe",
                                            fixture.DESCRIPTION_FIELD: "Use the safe path",
                                        },
                                    ],
                                },
                            ],
                        },
                    ),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="ask-interrupted",
        ),
    )
    resolved = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.FUNCTION_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: "ask-interrupted",
                    fixture.OUTPUT_FIELD: "aborted by user after 32.0s",
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="answer-interrupted",
        ),
    )

    answer = payloads(resolved, event_work.QuestionAnswered)[0].payload
    assert answer.answers == ()
    assert answer.feedback is None


def test_codex_empty_question_result_resolves() -> None:
    """Verify an empty codex question result resolves without answers."""
    translator = CodexCanonicalTranslator()
    translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.FUNCTION_CALL_ID,
                    fixture.NAME_FIELD: fixture.REQUEST_USER_INPUT_ID,
                    fixture.CALL_ID_FIELD: "ask-dismissed",
                    fixture.ARGUMENTS_FIELD: json.dumps(
                        {
                            fixture.QUESTIONS_FIELD: [
                                {
                                    fixture.ID_FIELD: "scope",
                                    fixture.HEADER_FIELD: "Scope",
                                    fixture.QUESTION_FIELD: "Which scope?",
                                    fixture.OPTIONS_FIELD: [
                                        {
                                            fixture.LABEL_FIELD: "Full",
                                            fixture.DESCRIPTION_FIELD: "Use the full scope",
                                        },
                                    ],
                                },
                            ],
                        },
                    ),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="ask-dismissed",
        ),
    )
    resolved = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.FUNCTION_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: "ask-dismissed",
                    fixture.OUTPUT_FIELD: json.dumps({fixture.ANSWERS_FIELD: {}}),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="answer-dismissed",
        ),
    )

    answer = payloads(resolved, event_work.QuestionAnswered)[0].payload
    assert answer.answers == ()
    assert answer.feedback is None


def test_codex_question_result_replaces_native() -> None:
    """Verify codex question result replaces native free text labels."""
    translator = CodexCanonicalTranslator()
    translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.FUNCTION_CALL_ID,
                    fixture.NAME_FIELD: fixture.REQUEST_USER_INPUT_ID,
                    fixture.CALL_ID_FIELD: "ask-free-text",
                    fixture.ARGUMENTS_FIELD: json.dumps(
                        {
                            fixture.QUESTIONS_FIELD: [
                                {
                                    fixture.ID_FIELD: fixture.COLOUR,
                                    fixture.HEADER_FIELD: "Colour",
                                    fixture.QUESTION_FIELD: "Which colour?",
                                    fixture.OPTIONS_FIELD: [
                                        {fixture.LABEL_FIELD: "Blue", fixture.DESCRIPTION_FIELD: "Use blue"},
                                    ],
                                },
                            ],
                        },
                    ),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="ask-free-text",
            source_position="20",
        ),
    )
    answered = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.FUNCTION_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: "ask-free-text",
                    fixture.OUTPUT_FIELD: json.dumps(
                        {
                            fixture.ANSWERS_FIELD: {
                                fixture.COLOUR: {
                                    fixture.ANSWERS_FIELD: [
                                        "None of the above",
                                        "user_note: Amber",
                                    ],
                                },
                            },
                        },
                    ),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="answer-free-text",
            source_position="21",
        ),
    )

    answer = payloads(answered, event_work.QuestionAnswered)[0].payload.answers[0]
    assert answer.labels == ("Amber",)


def test_codex_subagent_question_error_resolves() -> None:
    """Verify codex subagent question error resolves the false pending question."""
    translator = CodexCanonicalTranslator()
    translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.FUNCTION_CALL_ID,
                    fixture.NAME_FIELD: fixture.REQUEST_USER_INPUT_ID,
                    fixture.CALL_ID_FIELD: "ask-child",
                    fixture.ARGUMENTS_FIELD: json.dumps(
                        {
                            fixture.QUESTIONS_FIELD: [
                                {
                                    fixture.ID_FIELD: "continue",
                                    fixture.HEADER_FIELD: "Continue",
                                    fixture.QUESTION_FIELD: "Should the child continue?",
                                    fixture.OPTIONS_FIELD: [
                                        {fixture.LABEL_FIELD: "Yes", fixture.DESCRIPTION_FIELD: "Continue"},
                                    ],
                                },
                            ],
                        },
                    ),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.CHILD_ROLLOUT_ID,
            raw_event_id="ask-child",
            source_position="30",
        ),
    )
    resolved = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.FUNCTION_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: "ask-child",
                    fixture.OUTPUT_FIELD: ("request_user_input can only be used by the root thread"),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.CHILD_ROLLOUT_ID,
            raw_event_id="answer-child",
            source_position="31",
        ),
    )

    answer = payloads(resolved, event_work.QuestionAnswered)[0].payload
    assert answer.answers == ()
    assert answer.feedback is None
