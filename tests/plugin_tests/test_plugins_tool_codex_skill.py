# Copyright (c) 2026 Zhambyl Yermagambet
"""Codex skill tool tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.event_resource import (
    SkillFinished,
    SkillStarted,
)
from domain.event_shell import (
    ShellFinished,
    ShellStarted,
)
from domain.outcomes import Outcome
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from tests.harness_names import CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import JsonValue, text_of

if TYPE_CHECKING:
    from harness.models.raw_events import (
        TranslationResult,
    )

type CodexTranslationDecisionCase = tuple[dict[str, JsonValue], str]


def test_codex_loaded_skill_has_shared_skill() -> None:
    """Verify codex loaded skill has the shared skill lifecycle."""
    translation = CodexCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.MESSAGE_FIELD,
                    fixture.ID_FIELD: "skill-message-one",
                    fixture.ROLE_FIELD: fixture.USER,
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID,
                            fixture.TEXT_FIELD: (
                                "<skill>\n"
                                "<name>baqylau-e2e-communication</name>\n"
                                "<path>/work/.agents/skills/baqylau-e2e-communication/SKILL.md</path>\n"
                                "instructions\n"
                                "</skill>"
                            ),
                        },
                    ],
                    fixture.CHAT_METADATA_PASSTHROUGH_KIND: {
                        fixture.TURN_ID_FIELD: fixture.TURN_ONE_ID,
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="codex-skill",
        ),
    )

    _assert_codex_loaded_skill_start(translation)
    _assert_codex_loaded_skill_finish(translation)


def _assert_codex_loaded_skill_start(translation: TranslationResult) -> None:
    """Verify the loaded Codex skill start fact."""
    started = payloads(translation, SkillStarted)[0]
    assert started.turn_id == fixture.TURN_ONE_ID
    assert started.payload.skill_id == "skill-message-one"
    assert started.payload.name == "baqylau-e2e-communication"
    assert started.payload.arguments is None


def _assert_codex_loaded_skill_finish(translation: TranslationResult) -> None:
    """Verify the loaded Codex skill finish fact."""
    finished = payloads(translation, SkillFinished)[0]
    assert finished.turn_id == fixture.TURN_ONE_ID
    assert finished.payload.skill_id == "skill-message-one"
    assert finished.payload.outcome == Outcome.SUCCEEDED
    assert finished.payload.result is not None
    assert "instructions" in text_of(finished.payload.result)


def test_codex_subagent_skill_read_has_shared() -> None:
    """Verify codex subagent skill read has the shared skill lifecycle."""
    translator = CodexCanonicalTranslator()
    started = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "skill-call-one",
                    fixture.INPUT_FIELD: (
                        "const r = await tools.exec_command("
                        '{"cmd":"cat /work/.agents/skills/baqylau-e2e-communication/SKILL.md"}'
                        "); text(r.output);"
                    ),
                    fixture.CHAT_METADATA_PASSTHROUGH_KIND: {
                        fixture.TURN_ID_FIELD: fixture.TURN_ONE_ID,
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.CHILD_ROLLOUT_ID,
            raw_event_id="codex-child-skill-start",
        ),
    )
    finished = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: "skill-call-one",
                    fixture.OUTPUT_FIELD: '{"output":"skill instructions","exit_code":0}',
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.CHILD_ROLLOUT_ID,
            raw_event_id="codex-child-skill-finish",
        ),
    )

    _assert_subagent_skill_start(started)
    _assert_subagent_skill_finish(finished)
    assert not payloads(started, ShellStarted)
    assert not payloads(finished, ShellFinished)


def _assert_subagent_skill_start(started: TranslationResult) -> None:
    """Verify the subagent skill start fact."""
    skill_started = payloads(started, SkillStarted)[0]
    assert skill_started.turn_id == fixture.TURN_ONE_ID
    assert skill_started.payload.name == "baqylau-e2e-communication"


def _assert_subagent_skill_finish(finished: TranslationResult) -> None:
    """Verify the subagent skill finish fact."""
    skill_finished = payloads(finished, SkillFinished)[0]
    assert skill_finished.turn_id == fixture.TURN_ONE_ID
    assert skill_finished.payload.outcome == Outcome.SUCCEEDED
    assert skill_finished.payload.result is not None
    assert text_of(skill_finished.payload.result) == "skill instructions"
