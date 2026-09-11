# Copyright (c) 2026 Zhambyl Yermagambet
"""Codex tool schema tests."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from domain import event_actor, ids as domain_ids
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import JsonValue
from tests.plugin_tests.tool_translation_support import codex_translation_decision, completed_codex_item

CODEX_HARNESS = domain_ids.HarnessName("codex")
CLAUDE_CODE_HARNESS = domain_ids.HarnessName("claude_code")


type CodexTranslationDecisionCase = tuple[dict[str, JsonValue], str]


def test_claude_child_actor_uses_task_description(tmp_path: Path) -> None:
    """Verify claude child actor uses the task description from its sidecar."""
    transcript_path = tmp_path / "agent-child-one.jsonl"
    transcript_path.with_suffix(".meta.json").write_text(
        json.dumps({"agentType": "general-purpose", fixture.DESCRIPTION_FIELD: "Get Bali weather"}),
        encoding=fixture.TEXT_ENCODING,
    )
    event = replace(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: fixture.CHILD_PROMPT_ID,
                fixture.CWD_FIELD: fixture.WORK_PATH,
                fixture.MESSAGE_FIELD: {fixture.CONTENT_FIELD: "Find the weather"},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type="child_transcript",
            raw_event_id=fixture.CHILD_PROMPT_ID,
            source_position=fixture.ZERO_TEXT,
        ),
        source_name=str(transcript_path),
        actor_id=domain_ids.ActorId(fixture.CHILD_ONE_ID),
        parent_actor_id=domain_ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
    )

    translated = ClaudeCanonicalTranslator().translate(event)

    actor = payloads(translated, event_actor.ActorStarted)[0].payload
    name = payloads(translated, event_actor.ActorNameChanged)[0].payload
    assert actor.name == fixture.CHILD_ONE_ID
    assert name.name == "Get Bali weather"


def test_codex_deliberate_ignores_are_nonsemantic() -> None:
    """`ignored_unknown` must mean "a shape nobody has decided about" — nothing else.

    Two records were decided about in code and still reported themselves as
    unknown (measured against codex-cli 0.147.0, which is what the live-harness
    suite caught): a `world_state` snapshot, and the `item_completed` envelope for
    message items whose prose the response_item register already delivers. They
    are nonsemantic now. An item_completed for a type NOBODY has ruled on stays
    unknown — that is the tripwire, and it has to survive this change.
    """
    decisions: tuple[CodexTranslationDecisionCase, ...] = (
        (
            {fixture.TYPE_FIELD: "world_state", fixture.PAYLOAD_FIELD: {"full": True, "state": {}}},
            fixture.IGNORED_NONSEMANTIC,
        ),
        (
            completed_codex_item({
                fixture.TYPE_FIELD: "AgentMessage",
                fixture.ID_FIELD: fixture.MESSAGE_ONE_ID,
                fixture.CONTENT_FIELD: [{fixture.TYPE_FIELD: "Text", fixture.TEXT_FIELD: "Hi"}],
                "phase": "final_answer",
            }),
            fixture.IGNORED_NONSEMANTIC,
        ),
        (
            completed_codex_item({fixture.TYPE_FIELD: "UserMessage", fixture.ID_FIELD: "item-one"}),
            fixture.IGNORED_NONSEMANTIC,
        ),
        (
            completed_codex_item({
                fixture.TYPE_FIELD: "Reasoning",
                fixture.ID_FIELD: "rs-one",
                "summary_text": [],
                "raw_content": [],
            }),
            fixture.IGNORED_NONSEMANTIC,
        ),
        (
            completed_codex_item({
                fixture.TYPE_FIELD: "ImageView",
                fixture.ID_FIELD: "image-one",
                "path": "file:///tmp/marker.png",
            }),
            fixture.IGNORED_NONSEMANTIC,
        ),
        (
            completed_codex_item({fixture.TYPE_FIELD: "SomethingCodexShipsNextMonth", fixture.ID_FIELD: "item-two"}),
            fixture.IGNORED_UNKNOWN,
        ),
        (
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.MESSAGE_FIELD,
                    fixture.ROLE_FIELD: fixture.ASSISTANT,
                    "phase": "commentary",
                    fixture.CONTENT_FIELD: [{fixture.TYPE_FIELD: "output_text", fixture.TEXT_FIELD: ""}],
                },
            },
            fixture.IGNORED_NONSEMANTIC,
        ),
        (
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {fixture.TYPE_FIELD: "reasoning", "summary": []},
            },
            fixture.IGNORED_NONSEMANTIC,
        ),
        (
            completed_codex_item({
                fixture.TYPE_FIELD: fixture.COMMAND_EXECUTION_KIND,
                fixture.ID_FIELD: "item-three",
            }),
            fixture.IGNORED_UNKNOWN,
        ),
    )

    assert all(codex_translation_decision(document) == expected for document, expected in decisions)


def test_codex_unknown_field_on_known_record() -> None:
    """Verify codex unknown field on a known record fails translation naming it.

    The owner's strictest-stance decision (TASKS.md, 2026-08-21): a KNOWN
        record kind carrying a field records.py has not declared is schema drift,
        not tolerance. `translate()` raises exactly like the existing "unknown
        Codex goal state" tripwire above — the interpreter loop
        (engine/interpret/loop.py) is what turns any exception into the stored
        `translation_failed` verdict, with pydantic's own `extra_forbidden`
        message naming the field.
    """
    with pytest.raises(ValidationError, match=fixture.UNKNOWN_RECORD_FIELD):
        CodexCanonicalTranslator().translate(
            raw_event(
                {
                    fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                    fixture.PAYLOAD_FIELD: {
                        fixture.TYPE_FIELD: fixture.TASK_STARTED_ID,
                        fixture.TURN_ID_FIELD: fixture.TURN_ONE_ID,
                        fixture.UNKNOWN_RECORD_FIELD: "surprise",
                    },
                },
                harness=CODEX_HARNESS,
                source_type=fixture.ROLLOUT_SOURCE,
                raw_event_id="codex-unknown-field",
            ),
        )


def test_codex_wrong_typed_field_on_known_record() -> None:
    """Verify codex wrong typed field on a known record fails translation.

    Same decision, the other half of "shape mismatch": a declared field
        present with the WRONG type is exactly as much drift as a missing one or
        an extra one — `turn_id` is a string in every measured rollout, never a
        list.
    """
    with pytest.raises(ValidationError, match=fixture.TURN_ID_FIELD):
        CodexCanonicalTranslator().translate(
            raw_event(
                {
                    fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                    fixture.PAYLOAD_FIELD: {
                        fixture.TYPE_FIELD: fixture.TASK_STARTED_ID,
                        fixture.TURN_ID_FIELD: ["not", fixture.LETTER_A, "string"],
                    },
                },
                harness=CODEX_HARNESS,
                source_type=fixture.ROLLOUT_SOURCE,
                raw_event_id="codex-wrong-type",
            ),
        )


def test_codex_unknown_record_kind_stays_ignored() -> None:
    """Verify codex unknown record kind stays ignored not failed.

    The distinction the owner's decision draws (TASKS.md, 2026-08-21): an
        UNRECOGNISED `payload.type` string is the grammar growing (verified drift
        across codex 0.95 -> 0.144), not a shape mismatch within a type this
        codebase claims to know — `ignored_unknown`, never `translation_failed`,
        however unfamiliar its payload looks.
    """
    translated = CodexCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: "a_record_kind_codex_has_not_shipped_yet",
                    "whatever_fields_it_someday_carries": True,
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="codex-unknown-kind",
        ),
    )
    assert translated.decision == fixture.IGNORED_UNKNOWN
    assert translated.canonical_events == ()
