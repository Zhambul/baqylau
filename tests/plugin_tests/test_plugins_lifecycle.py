# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude Code actor lifecycle translation tests."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from domain import event_actor, ids
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from harness.impl.claude_code.hooks import gateway as claude_hooks
from tests.canonical_runtime import CanonicalRuntime
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import encoded_event, payloads, raw_event
from tests.plugin_tests.support_hooks import deliver_hook

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

CLAUDE_CODE_HARNESS = ids.HarnessName("claude_code")


DISTINCT_HOOK_PAYLOAD_COUNT = 2


def test_claude_hook_and_child_transcript() -> None:
    """Verify claude hook and child transcript deduplicate actor start."""
    hook = replace(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.SUBAGENT_START_HOOK,
                fixture.HOOK_EVENT_ID_FIELD: fixture.CHILD_START_ID,
                fixture.AGENT_ID_FIELD: fixture.CHILD_ONE_ID,
                "agent_type": "researcher",
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="child-hook",
        ),
        actor_id=ids.ActorId(fixture.CHILD_ONE_ID),
        parent_actor_id=ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
    )
    transcript_record = replace(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: fixture.CHILD_PROMPT_ID,
                fixture.MESSAGE_FIELD: {fixture.CONTENT_FIELD: "inspect"},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="child-transcript",
            source_position=fixture.ZERO_TEXT,
        ),
        actor_id=ids.ActorId(fixture.CHILD_ONE_ID),
        parent_actor_id=ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
    )

    hook_start = ClaudeCanonicalTranslator().translate(hook).canonical_events[0]
    transcript_start = ClaudeCanonicalTranslator().translate(transcript_record).canonical_events[0]

    assert encoded_event(hook_start) == encoded_event(transcript_start)


def test_claude_subagent_stop_hook_finishes_actor() -> None:
    """Verify claude subagent stop hook finishes the actor.

    The one signal that survives even when Claude Code suppresses the
        parent's `<task-notification>` — e.g. because the subagent left a
        `run_in_background` command still tracked — is the child's own
        SubagentStop hook. It must close the actor out on its own.
    """
    hook = replace(
        raw_event(
            {fixture.HOOK_EVENT_NAME_FIELD: "SubagentStop", fixture.HOOK_EVENT_ID_FIELD: "child-stop"},
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="child-stop-hook",
        ),
        actor_id=ids.ActorId(fixture.CHILD_ONE_ID),
        parent_actor_id=ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
    )

    result = ClaudeCanonicalTranslator().translate(hook)

    finished = payloads(result, event_actor.ActorFinished)
    assert len(finished) == 1
    assert finished[0].actor_id == ids.ActorId(fixture.CHILD_ONE_ID)


def test_claude_first_teammate_message_starts() -> None:
    """Verify claude first teammate message starts the actor once."""
    teammate_message = replace(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "team-message-one",
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: '<teammate-message teammate_id="worker-one">hello</teammate-message>',
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type="teammate_transcript",
            raw_event_id="worker-transcript",
            source_position=fixture.ZERO_TEXT,
        ),
        actor_id=ids.ActorId(fixture.WORKER_ONE_ID),
        parent_actor_id=ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
    )

    result = ClaudeCanonicalTranslator().translate(teammate_message)

    actor_starts = [event for event in result.canonical_events if isinstance(event.payload, event_actor.ActorStarted)]
    assert len(actor_starts) == 1
    assert len({event.event_id for event in result.canonical_events}) == len(result.canonical_events)


def test_claude_later_teammate_message_reuses() -> None:
    """Verify claude later teammate message reuses the canonical actor start."""
    translator = ClaudeCanonicalTranslator()
    first_record = replace(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "first",
                fixture.MESSAGE_FIELD: {fixture.CONTENT_FIELD: "inspect"},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type="teammate_transcript",
            raw_event_id="first-record",
            source_position=fixture.ZERO_TEXT,
        ),
        actor_id=ids.ActorId(fixture.WORKER_ONE_ID),
        parent_actor_id=ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
    )
    later_message = replace(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "later",
                fixture.TIMESTAMP_FIELD: "2026-08-14T08:00:00Z",
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: '<teammate-message teammate_id="worker-one">done</teammate-message>',
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type="teammate_transcript",
            raw_event_id="later-message",
            source_position="500",
        ),
        actor_id=ids.ActorId(fixture.WORKER_ONE_ID),
        parent_actor_id=ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
    )

    first_start = translator.translate(first_record).canonical_events[0]
    later_start = translator.translate(later_message).canonical_events[0]

    assert encoded_event(first_start) == encoded_event(later_start)


def test_claude_lead_start_uses_first_root_record() -> None:
    """Verify claude lead start uses the first root record with a working directory."""
    translator = ClaudeCanonicalTranslator()
    plumbing = translator.translate(
        raw_event(
            {fixture.TYPE_FIELD: fixture.QUEUE_OPERATION_ID, fixture.OPERATION_FIELD: fixture.ENQUEUE},
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="queue",
            source_position=fixture.ZERO_TEXT,
        ),
    )
    root_record = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: fixture.PROMPT_ONE_ID,
                fixture.PARENT_UUID: None,
                fixture.CWD_FIELD: fixture.WORK_PATH,
                fixture.MESSAGE_FIELD: {fixture.CONTENT_FIELD: fixture.HELLO},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="root-record",
            source_position="297",
        ),
    )
    hook = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.SESSION_START_HOOK,
                fixture.CWD_FIELD: fixture.WORK_PATH,
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="session-hook",
        ),
    )

    assert plumbing.decision == fixture.IGNORED_NONSEMANTIC
    assert [encoded_event(event) for event in root_record.canonical_events[:2]] == [
        encoded_event(event) for event in hook.canonical_events
    ]


def test_claude_queue_remove_accepts_native() -> None:
    """Verify claude queue remove accepts native absorption reason."""
    translation = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.QUEUE_OPERATION_ID,
                fixture.OPERATION_FIELD: "remove",
                fixture.CONTENT_FIELD: "follow-up",
                fixture.REASON_FIELD: "absorbed_mid_turn",
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="queue-remove",
        ),
    )

    assert translation.decision == fixture.IGNORED_NONSEMANTIC


def test_hook_native_identity_reuse_preserves(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify hook native identity reuse preserves each distinct observation."""
    monkeypatch.setenv(fixture.BAQYLAU_DATA_DIR_ENV, str(tmp_path))
    first = (
        b'{"session_id":"session-one","transcript_path":"/work/session.jsonl",'
        b'"cwd":"/work","hook_event_name":"PreToolUse","hook_event_id":"hook-one",'
        b'"tool_use_id":"tool-one",'
        b'"tool_name":"Bash","tool_input":{"command":"first"}}'
    )
    changed = first.replace(b'"first"', b'"changed"')
    deliver_hook(claude_hooks.ClaudeHookGateway(), first)
    deliver_hook(claude_hooks.ClaudeHookGateway(), changed)
    # An exact retry still converges on the same immutable observation.
    deliver_hook(claude_hooks.ClaudeHookGateway(), changed)

    runtime = CanonicalRuntime(str(tmp_path / fixture.MAIN_DB_PATH))
    evidence = tuple(
        audit_record
        for audit_record in runtime.raw_event_audits.audits_for_session(ids.SessionId(fixture.SESSION_ONE_ID))
        if audit_record.raw_event.source_type == fixture.HOOK_SOURCE
    )
    assert len(evidence) == DISTINCT_HOOK_PAYLOAD_COUNT
    assert {audit_record.raw_event.payload for audit_record in evidence} == {first, changed}
    assert all(":hook-one:" in str(audit_record.raw_event.raw_event_id) for audit_record in evidence)
