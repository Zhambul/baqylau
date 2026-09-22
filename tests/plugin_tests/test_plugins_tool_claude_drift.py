# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude tool schema tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from domain.event_work import (
    TaskChanged,
)
from domain.ids import (
    ActorId,
    TaskId,
)
from harness.impl.claude_code.canonical.records import MessageUsage, SystemRecord
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import JsonValue

type CodexTranslationDecisionCase = tuple[dict[str, JsonValue], str]


def test_claude_unknown_hook_field_fails() -> None:
    """Verify claude unknown hook field fails translation naming it.

    The owner's strictest-stance decision (TASKS.md, 2026-08-21) applied to
        Claude Code's own hook contract (canonical/records.py's HookPayload): a
        hook delivery carrying a field that module has not declared is schema
        drift, not tolerance — the same outcome the codex wave's equivalent test
        checks for its own foreign register.
    """
    with pytest.raises(ValidationError, match=fixture.UNKNOWN_RECORD_FIELD):
        ClaudeCanonicalTranslator().translate(
            raw_event(
                {
                    fixture.HOOK_EVENT_NAME_FIELD: fixture.STOP_HOOK,
                    fixture.SESSION_ID_FIELD: fixture.SESSION_ONE_ID,
                    fixture.UNKNOWN_RECORD_FIELD: "surprise",
                },
                harness=CLAUDE_CODE_HARNESS,
                source_type=fixture.HOOK_SOURCE,
                raw_event_id="claude-unknown-field",
            ),
        )


def test_claude_stop_hook_accepts_cache_status() -> None:
    """Claude 2.1.251 adds cache status data to some Stop hooks."""
    translated = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.STOP_HOOK,
                fixture.SESSION_ID_FIELD: fixture.SESSION_ONE_ID,
                "seconds_since_last_response": 6,
                "context_tokens": 16231,
                "prompt_cache_likely_expired": False,
                "estimated_cache_write_usd": 0.0325,
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="claude-stop-cache-status",
        ),
    )

    assert translated.decision != "translation_failed"


def test_claude_wrong_typed_hook_field_fails() -> None:
    """Verify claude wrong typed hook field fails translation.

    Same decision, the other half of "shape mismatch": a declared field
        present with the WRONG type is exactly as much drift as a missing or an
        extra one — `duration_ms` is a number in every measured hook delivery,
        never a list.
    """
    with pytest.raises(ValidationError, match="duration_ms"):
        ClaudeCanonicalTranslator().translate(
            raw_event(
                {
                    fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
                    fixture.SESSION_ID_FIELD: fixture.SESSION_ONE_ID,
                    fixture.TOOL_USE_ID_FIELD: fixture.CALL_ONE_ID,
                    fixture.TOOL_NAME_FIELD: fixture.BASH_TOOL,
                    "duration_ms": ["not", fixture.LETTER_A, "number"],
                },
                harness=CLAUDE_CODE_HARNESS,
                source_type=fixture.HOOK_SOURCE,
                raw_event_id="claude-wrong-type",
            ),
        )


@pytest.mark.parametrize("hook_name", ["TaskCreated", "TaskCompleted"])
def test_claude_task_hooks_accept_complete(hook_name: str) -> None:
    """Task hooks keep lifecycle changes that a final task-file scan can miss."""
    translated = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: hook_name,
                fixture.SESSION_ID_FIELD: fixture.SESSION_ONE_ID,
                fixture.TASK_ID: "task-one",
                "task_subject": "Check the dashboard",
                "task_description": "Read each dashboard section.",
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id=f"claude-{hook_name}",
        ),
    )

    assert translated.decision == fixture.TRANSLATED
    task = payloads(translated, TaskChanged)[0].payload
    assert task.task_id == TaskId("task-one")
    assert task.subject == "Check the dashboard"
    expected_state = "pending" if hook_name == "TaskCreated" else fixture.COMPLETED
    assert task.state == expected_state
    assert task.owner_actor_id == ActorId(fixture.SESSION_ONE_LEAD_ID)


def test_claude_message_usage_models_complete() -> None:
    """Verify claude message usage models the complete current vendor shape.

    The 2.1.239 transcript contract is typed all the way through its nested
        usage records; these are records, not dynamic dictionaries.
    """
    usage = MessageUsage.model_validate(
        {
            "output_tokens_details": {"thinking_tokens": 7},
            fixture.INPUT_TOKENS_ID: 2,
            fixture.OUTPUT_TOKENS_ID: 3,
            "cache_creation_input_tokens": 4,
            "cache_read_input_tokens": 5,
            "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
            "service_tier": "standard",
            "cache_creation": {
                "ephemeral_1h_input_tokens": 0,
                "ephemeral_5m_input_tokens": 4,
            },
            "inference_geo": "not_available",
            "iterations": [
                {
                    fixture.INPUT_TOKENS_ID: 2,
                    fixture.OUTPUT_TOKENS_ID: 3,
                    "cache_read_input_tokens": 5,
                    "cache_creation_input_tokens": 4,
                    "cache_creation": {
                        "ephemeral_1h_input_tokens": 0,
                        "ephemeral_5m_input_tokens": 4,
                    },
                    fixture.TYPE_FIELD: fixture.MESSAGE_FIELD,
                    fixture.MODEL: "claude-fable-5",
                },
            ],
            "speed": "standard",
        },
    )

    assert usage.server_tool_use is not None
    assert usage.server_tool_use.web_fetch_requests == 0
    assert usage.iterations is not None
    assert usage.iterations[0].type.value == fixture.MESSAGE_FIELD


def test_claude_new_record_fields_translate() -> None:
    """Claude 2.1.x adds fields to transcript records.

    Every assistant record carries the advisor model and the transformation
    list, and a person's queued command is marked as a human turn. A model
    that refuses them loses the whole conversation: the mirror shows nothing
    of it and no Chrome tool call reaches the feed.
    """
    translator = ClaudeCanonicalTranslator()
    assistant = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.ASSISTANT,
                "advisorModel": "claude-opus-5",
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: [
                        {fixture.TYPE_FIELD: fixture.TEXT_FIELD, fixture.TEXT_FIELD: "done"},
                    ],
                    "input_transformations": [],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="claude-advisor-model",
        ),
    )
    user = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                "turnOrigin": "human",
                fixture.MESSAGE_FIELD: {fixture.CONTENT_FIELD: "go"},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="claude-turn-origin",
        ),
    )
    queued = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.ATTACHMENT,
                fixture.ATTACHMENT: {
                    fixture.TYPE_FIELD: "queued_command",
                    fixture.PROMPT_KIND: "Reply after the command",
                    "commandMode": fixture.PROMPT_KIND,
                    "humanTurn": True,
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="claude-human-turn",
        ),
    )

    assert assistant.decision == fixture.TRANSLATED
    assert user.decision == fixture.TRANSLATED
    assert queued.decision == fixture.TRANSLATED


def test_claude_stop_hook_summary_uses_typed_hook() -> None:
    """Verify claude stop hook summary uses typed hook records."""
    summary = SystemRecord.model_validate(
        {
            fixture.TYPE_FIELD: fixture.SYSTEM,
            fixture.SUBTYPE: "stop_hook_summary",
            "hookCount": 2,
            "hookInfos": [
                {
                    fixture.COMMAND_FIELD: ".venv/bin/python client/claude_hook.py",
                    fixture.DURATION_MS_FIELD: 74,
                },
                {fixture.COMMAND_FIELD: "node stop-review-gate-hook.mjs", fixture.DURATION_MS_FIELD: 105},
            ],
            "hookErrors": [],
            "hookAdditionalContext": [],
            "preventedContinuation": False,
            "stopReason": "",
            "hasOutput": False,
            "level": "suggestion",
        },
    )

    assert summary.hook_infos is not None
    assert summary.hook_infos[0].duration_ms == fixture.HOOK_DURATION_MILLISECONDS
