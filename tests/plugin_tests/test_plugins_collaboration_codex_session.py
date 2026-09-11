# Copyright (c) 2026 Zhambyl Yermagambet
"""Codex session collaboration tests."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING

from domain import (
    event_actor,
    event_conversation,
    event_session,
    event_shell,
    event_telemetry,
    ids as domain_ids,
)
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from harness.impl.codex.continuity import RewindContinuity
from tests.harness_names import CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event

if TYPE_CHECKING:
    from harness.models.raw_events import (
        TranslationResult,
    )


def test_codex_session_turn_operation_usage() -> None:
    """Verify codex session turn operation usage and context records."""
    translator = CodexCanonicalTranslator()
    session = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.SESSION_META_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.CWD_FIELD: fixture.WORK_PATH,
                    fixture.ORIGINATOR: fixture.CODEX_TUI,
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="session",
            source_position=fixture.ZERO_TEXT,
        ),
    )
    assert isinstance(
        translator
        .translate(
            raw_event(
                {
                    fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                    fixture.PAYLOAD_FIELD: {
                        fixture.TYPE_FIELD: fixture.TASK_STARTED_ID,
                        fixture.TURN_ID_FIELD: fixture.TURN_ONE_ID,
                    },
                },
                harness=CODEX_HARNESS,
                source_type=fixture.ROLLOUT_SOURCE,
                raw_event_id="turn",
            ),
        )
        .canonical_events[0]
        .payload,
        event_conversation.TurnStarted,
    )
    operation = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.FUNCTION_CALL_ID,
                    fixture.NAME_FIELD: "exec_command",
                    fixture.CALL_ID_FIELD: fixture.CALL_ONE_ID,
                    fixture.ARGUMENTS_FIELD: json.dumps({"cmd": fixture.PRINT_DIRECTORY_COMMAND}),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id=fixture.OPERATION_FIELD,
        ),
    )
    usage = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: "token_count",
                    "info": {
                        "total_token_usage": {fixture.INPUT_TOKENS_ID: 100, fixture.OUTPUT_TOKENS_ID: 20},
                        "last_token_usage": {"total_tokens": 60},
                        "model_context_window": 200,
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="usage",
        ),
    )
    _assert_codex_session_usage(session, operation, usage)

    limits_only = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: "token_count",
                    "info": None,
                    "rate_limits": {
                        "primary": {
                            "used_percent": 19.0,
                            "window_minutes": 10080,
                            fixture.RESETS_AT: 1788142132,
                        },
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="limits-only",
        ),
    )
    assert limits_only.canonical_events == ()
    assert limits_only.decision == fixture.IGNORED_NONSEMANTIC


def _assert_codex_session_usage(
    session: TranslationResult,
    operation: TranslationResult,
    usage: TranslationResult,
) -> None:
    """Verify the session, shell, and usage records."""
    assert isinstance(session.canonical_events[0].payload, event_session.SessionStarted)
    assert isinstance(session.canonical_events[1].payload, event_actor.ActorStarted)
    assert isinstance(operation.canonical_events[0].payload, event_shell.ShellStarted)
    assert len(payloads(usage, event_telemetry.UsageReported)) == 1
    assert len(payloads(usage, event_telemetry.ContextReported)) == 1


def test_codex_rewind_session_keeps_its_prior() -> None:
    """Verify codex rewind session keeps its prior session identity."""
    result = CodexCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.SESSION_META_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.CWD_FIELD: fixture.WORK_PATH,
                    fixture.ORIGINATOR: fixture.CODEX_TUI,
                    "forked_from_id": fixture.SESSION_BEFORE_REWIND_ID,
                    "history_mode": "paginated",
                    "history_base": {
                        fixture.THREAD_ID_FIELD: "rollout-before-rewind",
                        "end_ordinal_exclusive": 15,
                        "end_byte_offset": 43030,
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="rewind-session",
            source_position=fixture.ZERO_TEXT,
        ),
    )

    started = payloads(result, event_session.SessionStarted)
    assert len(started) == 1
    assert started[0].payload.continued_from == domain_ids.SessionId(fixture.SESSION_BEFORE_REWIND_ID)


def test_codex_rewind_intent_supplies_relation() -> None:
    """Verify codex rewind intent supplies the relation missing from native metadata."""
    continuity = RewindContinuity()
    continuity.expect(
        domain_ids.SessionId(fixture.SESSION_BEFORE_REWIND_ID), domain_ids.WindowId(fixture.WINDOW_ONE_ID),
    )
    translator = CodexCanonicalTranslator(continuity)
    native = replace(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.SESSION_META_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.CWD_FIELD: fixture.WORK_PATH,
                    fixture.ORIGINATOR: fixture.CODEX_TUI,
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="rewind-session-without-native-parent",
            source_position=fixture.ZERO_TEXT,
        ),
        session_id=domain_ids.SessionId("session-after-rewind"),
        actor_id=domain_ids.ActorId("session-after-rewind:lead"),
        terminal_window_id=domain_ids.WindowId(fixture.WINDOW_ONE_ID),
    )

    first = translator.translate(native)
    repeated = translator.translate(native)

    assert payloads(first, event_session.SessionStarted)[0].payload.continued_from == domain_ids.SessionId(
        fixture.SESSION_BEFORE_REWIND_ID,
    )
    assert payloads(repeated, event_session.SessionStarted)[0].payload.continued_from == domain_ids.SessionId(
        fixture.SESSION_BEFORE_REWIND_ID,
    )


def test_codex_message_keeps_its_native_turn() -> None:
    """Verify codex message keeps its native turn identity."""
    translation = CodexCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.MESSAGE_FIELD,
                    fixture.ID_FIELD: fixture.MESSAGE_ONE_ID,
                    fixture.ROLE_FIELD: fixture.ASSISTANT,
                    "phase": "final_answer",
                    fixture.CONTENT_FIELD: [{fixture.TYPE_FIELD: "output_text", fixture.TEXT_FIELD: "Finished"}],
                    fixture.CHAT_METADATA_PASSTHROUGH_KIND: {
                        fixture.TURN_ID_FIELD: fixture.TURN_ONE_ID,
                        "create_time": 1787403595.261263,
                        "content_item_kinds": ["assistant.text"],
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id=fixture.MESSAGE_FIELD,
        ),
    )

    assert translation.canonical_events[0].turn_id == domain_ids.TurnId(fixture.TURN_ONE_ID)


def test_codex_source_can_attach_actor_to_another() -> None:
    """Verify codex source can attach an actor to another harness session."""
    nested_raw_event = raw_event(
        {
            fixture.TYPE_FIELD: fixture.SESSION_META_ID,
            fixture.PAYLOAD_FIELD: {
                fixture.CWD_FIELD: fixture.WORK_PATH,
                fixture.ORIGINATOR: "codex-exec",
            },
        },
        harness=CODEX_HARNESS,
        source_type="sidecar_rollout",
        raw_event_id="nested-session",
        source_position=fixture.ZERO_TEXT,
    )
    nested_raw_event = replace(
        nested_raw_event,
        actor_id=domain_ids.ActorId("codex-child"),
        parent_actor_id=domain_ids.ActorId("claude-lead"),
    )

    translation = CodexCanonicalTranslator().translate(nested_raw_event)

    assert len(translation.canonical_events) == 1
    assert isinstance(translation.canonical_events[0].payload, event_actor.ActorStarted)
    assert translation.canonical_events[0].payload.role == "sidecar"
    assert translation.canonical_events[0].actor_id == domain_ids.ActorId("codex-child")
    assert translation.canonical_events[0].parent_actor_id == domain_ids.ActorId("claude-lead")


def test_codex_native_subagent_keeps_child_role() -> None:
    """Verify codex native subagent keeps the child role."""
    child_raw_event = raw_event(
        {
            fixture.TYPE_FIELD: fixture.SESSION_META_ID,
            fixture.PAYLOAD_FIELD: {fixture.PARENT_THREAD_ID_FIELD: "codex-parent"},
        },
        harness=CODEX_HARNESS,
        source_type=fixture.CHILD_ROLLOUT_ID,
        raw_event_id="native-child",
        source_position=fixture.ZERO_TEXT,
    )
    child_raw_event = replace(
        child_raw_event,
        actor_id=domain_ids.ActorId("codex-child"),
        parent_actor_id=domain_ids.ActorId("codex-lead"),
    )

    translation = CodexCanonicalTranslator().translate(child_raw_event)

    assert isinstance(translation.canonical_events[0].payload, event_actor.ActorStarted)
    assert translation.canonical_events[0].payload.role == "child"
