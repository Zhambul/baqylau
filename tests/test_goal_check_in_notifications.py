# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep the translation queue moving past a notification that names no work.

Claude Code 2.1 queues a goal check-in as a task notification with only a
summary. It names no task and no tool call. It once became an assignment finish
with an empty assignment ID. The public API refused that fact, and the error
stopped the translation of every later input of every session.
"""

import json

from domain import content, event_actor, outcomes
from domain.event_base import CanonicalEvent
from domain.ids import ActorId, AssignmentId, CanonicalEventId, HarnessName, RawEventId, SessionId
from domain.records import RecordedTranslationDecision
from engine.interpret.extension_mapping import public_translation
from harness.impl.claude_code.canonical.transcript_parser import parse_line
from harness.models.raw_events import RawEvent, TranslationResult

CHECK_IN = (
    "<task-notification>\n<summary>Goal check-in: background work still running</summary>\n"
    "</task-notification>\n<system-reminder>Check on the background work.</system-reminder>"
)
SESSION = SessionId("session-one")
LEAD = ActorId("lead-one")


def test_a_queued_goal_check_in_has_no_fact() -> None:
    """A notification that names no task and no tool call gives no record."""
    line = json.dumps({
        "type": "queue-operation", "operation": "enqueue", "timestamp": "2026-09-25T10:46:03.000Z",
        "sessionId": SESSION, "content": CHECK_IN,
    })

    assert parse_line(line) is None


def test_a_refused_fact_fails_only_its_input() -> None:
    """A refused fact becomes a failed verdict with no facts, so the queue moves on."""
    refused = TranslationResult((_assignment_finish(AssignmentId("")),), RecordedTranslationDecision.TRANSLATED, None)

    translation, facts = public_translation(_raw_event(), refused)

    assert facts == ()
    assert translation.decision == RecordedTranslationDecision.TRANSLATION_FAILED
    assert translation.canonical_events == ()
    assert "ActorAssignmentFinished" in (translation.reason or "")


def _assignment_finish(assignment_id: AssignmentId) -> CanonicalEvent[event_actor.ActorAssignmentFinished]:
    return CanonicalEvent(
        event_id=CanonicalEventId("event-one"), session_id=SESSION, actor_id=LEAD, turn_id=None,
        parent_actor_id=None, harness=HarnessName("claude_code"), occurred_at=None, terminal_window_id=None,
        harness_process_id=None,
        payload=event_actor.ActorAssignmentFinished(
            assignment_id, outcomes.Outcome.SUCCEEDED, content.TextContent("done"), None,
        ),
    )


def _raw_event() -> RawEvent:
    return RawEvent(
        raw_event_id=RawEventId("raw-one"), harness=HarnessName("claude_code"), source_type="transcript",
        source_name="transcript.jsonl", source_position="1", session_id=SESSION, actor_id=LEAD,
        parent_actor_id=None, observed_at=1.0, encoding="jsonl", payload=CHECK_IN.encode(),
        source_identity="transcript-one",
    )
