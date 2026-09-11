# Copyright (c) 2026 Zhambyl Yermagambet
"""Cross-harness compaction translation tests."""

from __future__ import annotations

from domain.event_telemetry import CompactionFinished, CompactionStarted
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS, CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import raw_event
from tests.plugin_tests.support_values import text_of


def _assert_finished_context(finished: CompactionFinished, expected: str) -> None:
    """Verify the finished compaction context."""
    assert finished.context is not None
    assert text_of(finished.context) == expected


def test_codex_encrypted_compaction_maps_unique() -> None:
    """Verify codex encrypted compaction maps unique non expandable lifecycle."""
    translator = CodexCanonicalTranslator()
    before = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: "PreCompact",
                fixture.HOOK_EVENT_ID_FIELD: fixture.COMPACT_ONE_ID,
            },
            harness=CODEX_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="compact-before",
        ),
    )
    after = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: "PostCompact",
                fixture.HOOK_EVENT_ID_FIELD: fixture.COMPACT_ONE_ID,
            },
            harness=CODEX_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="compact-after",
        ),
    )
    boundary = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: "compacted",
                fixture.PAYLOAD_FIELD: {
                    fixture.MESSAGE_FIELD: "",
                    "replacement_history": [
                        {
                            fixture.TYPE_FIELD: fixture.MESSAGE_FIELD,
                            fixture.ROLE_FIELD: fixture.USER,
                            fixture.CONTENT_FIELD: [
                                {
                                    fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID,
                                    fixture.TEXT_FIELD: "Remember amber circle.",
                                },
                            ],
                        },
                        {
                            fixture.TYPE_FIELD: "compaction",
                            "encrypted_content": "opaque-native-summary",
                        },
                    ],
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id=fixture.COMPACT_BOUNDARY_ID,
        ),
    )

    assert isinstance(before.canonical_events[0].payload, CompactionStarted)
    assert not after.canonical_events
    assert isinstance(boundary.canonical_events[0].payload, CompactionFinished)
    finished = boundary.canonical_events[0].payload
    assert finished.context is None


def test_codex_rollout_compaction_boundary() -> None:
    """Verify codex rollout compaction boundary carries direct context."""
    translated = CodexCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: "compacted",
                fixture.PAYLOAD_FIELD: {
                    fixture.MESSAGE_FIELD: "Direct compacted context",
                    "replacement_history": [],
                    "guardian_history": [
                        {fixture.TYPE_FIELD: fixture.MESSAGE_FIELD, "role": "user", "content": []},
                    ],
                    "compaction_response_id": "compact-response",
                    "latest_token_usage_record": {
                        "thread_id": "thread-one",
                        "turn_id": "turn-one",
                        "session_id": "session-one",
                        "root_turn_id": "root-turn",
                        "response_id": "response-one",
                        "usage": {"input_tokens": 100},
                        "turn_token_usage": {"input_tokens": 100},
                        "thread_token_usage": {"input_tokens": 100},
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id=fixture.COMPACT_BOUNDARY_ID,
        ),
    )

    assert translated.decision == fixture.TRANSLATED
    finished = translated.canonical_events[0].payload
    assert isinstance(finished, CompactionFinished)
    _assert_finished_context(finished, "Direct compacted context")


def test_claude_compaction_metadata_maps_to_one() -> None:
    """Verify claude compaction metadata maps to one finished event."""
    translator = ClaudeCanonicalTranslator()
    boundary = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.SYSTEM,
                fixture.SUBTYPE: "compact_boundary",
                fixture.UUID_FIELD: fixture.COMPACT_ONE_ID,
                fixture.CONTENT_FIELD: "Conversation compacted",
                "compactMetadata": {
                    "trigger": "manual",
                    "preTokens": 15182,
                    "postTokens": 1426,
                    "cumulativeDroppedTokens": 13756,
                    fixture.DURATION_MS_FIELD: 15964,
                    "preCompactDiscoveredTools": [fixture.READ_TOOL],
                    "preservedSegment": {
                        "headUuid": "head-one",
                        "anchorUuid": "anchor-one",
                        "tailUuid": "tail-one",
                    },
                    "preservedMessages": {
                        "anchorUuid": "anchor-one",
                        "uuids": ["head-one", "tail-one"],
                        "allUuids": ["head-one", "middle-one", "tail-one"],
                    },
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=fixture.COMPACT_ONE_ID,
        ),
    )
    translated = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: fixture.COMPACT_SUMMARY_ID,
                fixture.PARENT_UUID: fixture.COMPACT_ONE_ID,
                "isCompactSummary": True,
                fixture.MESSAGE_FIELD: {
                    fixture.ROLE_FIELD: fixture.USER,
                    fixture.CONTENT_FIELD: "Compacted summary retains amber circle.",
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=fixture.COMPACT_SUMMARY_ID,
        ),
    )

    assert boundary.decision == fixture.IGNORED_NONSEMANTIC
    assert translated.decision == fixture.TRANSLATED
    finished = translated.canonical_events[0].payload
    assert isinstance(finished, CompactionFinished)
    assert finished.before_tokens == fixture.COMPACTION_INPUT_TOKENS
    _assert_finished_context(finished, "Compacted summary retains amber circle.")


def test_claude_compaction_hook_and_boundary_map() -> None:
    """Verify claude compaction hook and boundary map one complete lifecycle."""
    translator = ClaudeCanonicalTranslator()
    lifecycle = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: "PreCompact",
                fixture.HOOK_EVENT_ID_FIELD: "compact-start",
                "prompt_id": fixture.COMPACT_ONE_ID,
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="compact-start",
        ),
    ).canonical_events
    hook_finished = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: "PostCompact",
                fixture.HOOK_EVENT_ID_FIELD: "compact-hook-finish",
                "prompt_id": fixture.COMPACT_ONE_ID,
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="compact-hook-finish",
        ),
    )
    boundary_finished = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.SYSTEM,
                fixture.SUBTYPE: "compact_boundary",
                fixture.UUID_FIELD: fixture.COMPACT_BOUNDARY_ID,
                fixture.CONTENT_FIELD: "Conversation compacted",
                "compactMetadata": {
                    "trigger": "manual",
                    "preTokens": 15182,
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=fixture.COMPACT_BOUNDARY_ID,
        ),
    )
    lifecycle = (
        *lifecycle,
        *hook_finished.canonical_events,
        *boundary_finished.canonical_events,
        *translator.translate(
            raw_event(
                {
                    fixture.TYPE_FIELD: fixture.USER,
                    fixture.UUID_FIELD: fixture.COMPACT_SUMMARY_ID,
                    fixture.PARENT_UUID: fixture.COMPACT_BOUNDARY_ID,
                    "isCompactSummary": True,
                    fixture.MESSAGE_FIELD: {
                        fixture.ROLE_FIELD: fixture.USER,
                        fixture.CONTENT_FIELD: "The compacted context.",
                    },
                },
                harness=CLAUDE_CODE_HARNESS,
                source_type=fixture.TRANSCRIPT_SOURCE,
                raw_event_id=fixture.COMPACT_SUMMARY_ID,
            ),
        ).canonical_events,
    )
    assert hook_finished.decision == fixture.IGNORED_NONSEMANTIC
    assert boundary_finished.decision == fixture.IGNORED_NONSEMANTIC
    assert [type(event.payload) for event in lifecycle] == [
        CompactionStarted,
        CompactionFinished,
    ]
    finished = lifecycle[-1].payload
    assert isinstance(finished, CompactionFinished)
    assert finished.before_tokens == fixture.COMPACTION_INPUT_TOKENS
    _assert_finished_context(finished, "The compacted context.")
