# Copyright (c) 2026 Zhambyl Yermagambet
"""Translate Claude lifecycle hooks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain import event_conversation, event_session, event_work, work_state
from domain.outcomes import Outcome
from harness.impl.claude_code.canonical import records
from harness.impl.claude_code.canonical.messages import session_events
from harness.impl.claude_code.canonical.support import event
from harness.models.raw_event_builders import CanonicalEventDraft, session_run_finished_event

if TYPE_CHECKING:
    from domain.event_base import CanonicalEvent, EventPayload
    from harness.impl.claude_code.canonical.turns import TurnSemantics
    from harness.models.raw_events import RawEvent
    from harness.models.selections import SelectionSemantics


def effort_report(
    raw_event: RawEvent,
    hook: records.HookPayload,
    selection_semantics: SelectionSemantics,
) -> list[CanonicalEvent[EventPayload]]:
    """Translate a reported effort-level change.

    Returns:
        The effort-change events.

    """
    level = hook.effort.level if isinstance(hook.effort, records.HookEffort) else hook.effort
    if not isinstance(level, str) or not level:
        return []
    changed = selection_semantics.effort(
        raw_event.session_id,
        raw_event.actor_id,
        level,
        work_state.EffortChangeReason.REPORTED_BY_HARNESS,
    )
    if changed is None:
        return []
    return [
        event(
            raw_event,
            CanonicalEventDraft(
                "effort",
                str(raw_event.actor_id),
                "reported",
                changed,
            ),
        ),
    ]


def turn_finished(
    raw_event: RawEvent,
    turn_semantics: TurnSemantics,
    native_identity: str,
    outcome: Outcome,
) -> CanonicalEvent[EventPayload]:
    """Close the turn that is open for a Stop hook.

    Returns:
        The turn-finished event.

    """
    turn_id = turn_semantics.finished_by_hook(raw_event)
    return event(
        raw_event,
        CanonicalEventDraft(
            "turn",
            str(turn_id) if turn_id else native_identity,
            "finished",
            event_conversation.TurnFinished(None, outcome),
            turn_id=turn_id,
        ),
    )


def lifecycle_hook_events(
    raw_event: RawEvent,
    hook: records.HookPayload,
    turn_semantics: TurnSemantics,
    selection_semantics: SelectionSemantics,
) -> list[CanonicalEvent[EventPayload]] | None:
    """Translate a lifecycle hook.

    Returns:
        The translated events, or None for another hook group.

    """
    hook_name = hook.hook_event_name or ""
    native_identity = str(hook.hook_event_id or hook.uuid or raw_event.source_position)
    if hook_name == "SessionStart":
        return session_events(raw_event, hook)
    if hook_name == "SessionEnd":
        return session_end_events(raw_event, hook)
    if hook_name == "Stop":
        return [
            turn_finished(raw_event, turn_semantics, native_identity, Outcome.SUCCEEDED),
            *effort_report(raw_event, hook, selection_semantics),
        ]
    if hook_name == "StopFailure":
        return _stop_failure_events(raw_event, hook, turn_semantics, native_identity)
    return None


def session_end_events(
    raw_event: RawEvent,
    hook: records.HookPayload,
) -> list[CanonicalEvent[EventPayload]]:
    """Build session finish facts without changing turn state.

    Returns:
        The finish fact for the observed native run.

    """
    session_finished = event_session.SessionFinished(
        Outcome.SUCCEEDED,
        hook.reason or None,
    )
    if raw_event.terminal_window_id is not None:
        return [session_run_finished_event(raw_event, session_finished)]
    return [
        event(
            raw_event,
            CanonicalEventDraft(
                "session",
                str(raw_event.session_id),
                "finished",
                session_finished,
            ),
        ),
    ]


def _stop_failure_events(
    raw_event: RawEvent,
    hook: records.HookPayload,
    turn_semantics: TurnSemantics,
    native_identity: str,
) -> list[CanonicalEvent[EventPayload]]:
    events = [
        turn_finished(raw_event, turn_semantics, native_identity, Outcome.FAILED),
    ]
    if hook.error == "rate_limit":
        events.append(
            event(
                raw_event,
                CanonicalEventDraft(
                    "goal",
                    native_identity,
                    "changed",
                    event_work.GoalChanged(
                        None,
                        work_state.GoalState.USAGE_LIMITED,
                        "rate_limit",
                    ),
                ),
            ),
        )
    return events
