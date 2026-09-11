# Copyright (c) 2026 Zhambyl Yermagambet
"""Codex collaboration lifecycle tests."""

import json
from dataclasses import replace
from functools import partial
from pathlib import Path

from domain import (
    event_actor,
    event_conversation,
    ids as domain_ids,
    outcomes,
)
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from tests.harness_names import CODEX_HARNESS
from tests.plugin_tests import (
    collaboration_activity_support,
    collaboration_call_support,
    collaboration_lifecycle_support,
    collaboration_values,
    vocabulary as fixture,
)
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import text_of


def test_codex_collaboration_lifecycle_uses_child(tmp_path: Path) -> None:
    """Verify codex collaboration lifecycle uses child turn as assignment identity."""
    lifecycle = collaboration_lifecycle_support.codex_child_lifecycle(tmp_path)
    start_payload = payloads(lifecycle.started, event_actor.ActorAssignmentStarted)[0].payload
    finish_payload = payloads(lifecycle.finished, event_actor.ActorAssignmentFinished)[0].payload
    collaboration_call_support.assert_child_assignment_start(start_payload)
    collaboration_call_support.assert_child_assignment_finish(finish_payload, lifecycle.finished)
    hook_raw = replace(
        raw_event(
            {fixture.HOOK_EVENT_NAME_FIELD: "SubagentStop", fixture.AGENT_ID_FIELD: fixture.CHILD_ONE_ID},
            harness=CODEX_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="child-stop-hook",
        ),
        actor_id=collaboration_values.PRIMARY_CHILD_ACTOR,
        parent_actor_id=domain_ids.ActorId(fixture.LEAD_ONE_ID),
    )
    hook_result = lifecycle.translator.translate(hook_raw)
    assert hook_result.canonical_events == ()
    assert hook_result.decision == fixture.IGNORED_NONSEMANTIC


def test_codex_collaboration_controls_map_only(tmp_path: Path) -> None:
    """Verify codex collaboration controls map only semantic actor facts."""
    rollout_path = tmp_path / "lead.jsonl"
    collaboration_call_support.write_collaboration_calls(rollout_path)
    translator = CodexCanonicalTranslator()
    translate_rollout = partial(
        collaboration_lifecycle_support.translate_codex_rollout_from_path, translator, rollout_path,
    )
    collaboration_call_support.assert_collaboration_calls_ignored(translate_rollout)
    collaboration_call_support.assert_sent_collaboration_message(translate_rollout)
    collaboration_activity_support.assert_nonsemantic_collaboration_activities(translate_rollout)
    collaboration_activity_support.assert_completed_activity_ignored(translate_rollout)
    collaboration_activity_support.assert_collaboration_outputs_ignored(translate_rollout)


def test_codex_actor_message_correlation_survives(tmp_path: Path) -> None:
    """Verify codex actor message correlation survives translator restart."""
    call = (
        json.dumps(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.FUNCTION_CALL_ID,
                    fixture.NAME_FIELD: "send_message",
                    fixture.ARGUMENTS_FIELD: json.dumps({
                        fixture.TARGET_FIELD: fixture.ROOT_WEATHER_PATH,
                        fixture.MESSAGE_FIELD: fixture.ENCRYPTED,
                    }),
                    fixture.CALL_ID_FIELD: "send-one",
                },
            },
        )
        + "\n"
    )
    rollout_path = tmp_path / "lead.jsonl"
    rollout_path.write_text(call)
    activity = replace(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.ITEM_COMPLETED,
                    fixture.TURN_ID_FIELD: fixture.LEAD_TURN_ID,
                    fixture.ITEM_FIELD: {
                        fixture.TYPE_FIELD: fixture.SUBAGENT_ACTIVITY_KIND,
                        fixture.ID_FIELD: "send-one",
                        fixture.KIND_FIELD: "interacted",
                        fixture.AGENT_THREAD_ID_FIELD: fixture.CHILD_ONE_ID,
                        fixture.AGENT_PATH_FIELD: fixture.ROOT_WEATHER_PATH,
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="send-activity",
            source_position=str(len(call.encode())),
        ),
        source_name=str(rollout_path),
    )

    message = payloads(
        CodexCanonicalTranslator().translate(activity),
        event_conversation.MessageCreated,
    )[0].payload
    assert message.recipient_actor_id == domain_ids.ActorId(fixture.CHILD_ONE_ID)
    # The text comes from the call the backwards scan recovered, so a restart
    # loses the correlation AND the words together, or neither.
    assert text_of(message.content) == fixture.ENCRYPTED


def test_codex_child_abort_cancels_only_its() -> None:
    """Verify codex child abort cancels only its current assignment."""
    child_raw = replace(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.TURN_ABORTED_ID,
                    fixture.TURN_ID_FIELD: "child-turn-two",
                    fixture.REASON_FIELD: fixture.INTERRUPTED,
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.CHILD_ROLLOUT_ID,
            raw_event_id="child-abort",
        ),
        actor_id=domain_ids.ActorId(fixture.CHILD_ONE_ID),
        parent_actor_id=domain_ids.ActorId(fixture.LEAD_ONE_ID),
    )

    assignment = payloads(
        CodexCanonicalTranslator().translate(child_raw),
        event_actor.ActorAssignmentFinished,
    )[0].payload
    assert assignment.assignment_id == domain_ids.AssignmentId("child-turn-two")
    assert assignment.outcome == "cancelled"
    assert assignment.result is None
    assert assignment.reason == fixture.INTERRUPTED


def test_codex_child_abort_without_turn_id_closes(tmp_path: Path) -> None:
    """Verify codex child abort without turn identifier closes its started assignment."""
    rollout_path = tmp_path / "child.jsonl"
    rollout_path.write_text(
        json.dumps(
            {
                fixture.TYPE_FIELD: fixture.SESSION_META_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.SOURCE: {
                        fixture.SUBAGENT: {
                            "thread_spawn": {
                                fixture.PARENT_THREAD_ID_FIELD: fixture.LEAD_ONE_ID,
                                fixture.AGENT_PATH_FIELD: "/root/worker",
                            },
                        },
                    },
                },
            },
        )
        + "\n",
    )
    translator = CodexCanonicalTranslator()

    started = translator.translate(
        collaboration_lifecycle_support.codex_child_rollout_event(
            rollout_path,
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.TASK_STARTED_ID,
                    fixture.TURN_ID_FIELD: fixture.CHILD_TURN_ID,
                },
            },
            fixture.CHILD_START_ID,
        ),
    )
    aborted = translator.translate(
        collaboration_lifecycle_support.codex_child_rollout_event(
            rollout_path,
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {fixture.TYPE_FIELD: fixture.TURN_ABORTED_ID},
            },
            "child-abort",
        ),
    )

    assert (
        payloads(aborted, event_conversation.TurnAborted)[0].turn_id
        == payloads(
            started,
            event_conversation.TurnStarted,
        )[0].turn_id
    )
    assignment = payloads(aborted, event_actor.ActorAssignmentFinished)[0].payload
    assert assignment.assignment_id == domain_ids.AssignmentId(fixture.CHILD_TURN_ID)
    assert assignment.outcome == outcomes.Outcome.CANCELLED
