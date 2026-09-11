# Copyright (c) 2026 Zhambyl Yermagambet
"""Loaded skill translation tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.event_conversation import MessageCreated
from domain.event_resource import (
    SkillFinished,
    SkillStarted,
)
from domain.event_shell import (
    ShellStarted,
)
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS, CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import JsonValue, text_of

if TYPE_CHECKING:
    from harness.models.raw_events import (
        TranslationResult,
    )

type CodexTranslationDecisionCase = tuple[dict[str, JsonValue], str]


def test_claude_loaded_skill_text_finishes_skill() -> None:
    """Verify claude loaded skill text finishes the skill instead of becoming a system message."""
    translator = ClaudeCanonicalTranslator()
    started = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: fixture.SKILL_ONE,
                fixture.TOOL_NAME_FIELD: fixture.SKILL_TOOL,
                fixture.TOOL_INPUT_FIELD: {fixture.SKILL_ARGUMENT: fixture.AUDIT_DEBUG, "args": "proof"},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="skill-start",
        ),
    )
    empty_result = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: fixture.SKILL_ONE,
                fixture.TOOL_NAME_FIELD: fixture.SKILL_TOOL,
                fixture.TOOL_INPUT_FIELD: {fixture.SKILL_ARGUMENT: fixture.AUDIT_DEBUG, "args": "proof"},
                fixture.TOOL_RESPONSE_FIELD: "Launching skill: audit-debug",
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="skill-empty-result",
        ),
    )
    transcript_result = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "skill-tool-result",
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TOOL_RESULT_ID,
                            fixture.TOOL_USE_ID_FIELD: fixture.SKILL_ONE,
                            fixture.CONTENT_FIELD: "Launching skill: audit-debug",
                        },
                    ],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="skill-tool-result",
        ),
    )
    loaded = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "skill-output",
                fixture.IS_META: True,
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TEXT_FIELD,
                            fixture.TEXT_FIELD: (
                                "Base directory for this skill: /work/.claude/skills/audit-debug\n"
                                "---\nname: audit-debug\n---\nDo the audit.\n"
                                "ARGUMENTS: proof"
                            ),
                        },
                    ],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="skill-output",
        ),
    )

    _assert_claude_skill_arguments(started)
    assert not payloads(empty_result, SkillFinished)
    assert not payloads(transcript_result, SkillFinished)
    _assert_loaded_claude_skill_finish(loaded)


def _assert_claude_skill_arguments(started: TranslationResult) -> None:
    """Verify the arguments of the started Claude skill."""
    arguments = payloads(started, SkillStarted)[0].payload.arguments
    assert arguments is not None
    assert text_of(arguments) == "proof"


def _assert_loaded_claude_skill_finish(loaded: TranslationResult) -> None:
    """Verify the loaded Claude skill completion fact."""
    result = payloads(loaded, SkillFinished)[0].payload.result
    assert result is not None
    assert text_of(result).endswith("Do the audit.")
    assert "ARGUMENTS:" not in text_of(result)
    assert not payloads(loaded, MessageCreated)


def test_codex_read_of_non_skill_file_remains() -> None:
    """Verify codex read of a non skill file remains a shell command."""
    translation = CodexCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "read-call-one",
                    fixture.INPUT_FIELD: (
                        'const r = await tools.exec_command({"cmd":"cat /work/docs/SKILL.md"}); text(r.output);'
                    ),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="codex-ordinary-read",
        ),
    )

    assert len(payloads(translation, ShellStarted)) == 1
    assert not payloads(translation, SkillStarted)
