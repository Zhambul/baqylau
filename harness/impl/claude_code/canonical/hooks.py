"""Claude Code hook-stream translation: lifecycle and tool-call notifications."""

from __future__ import annotations

from domain.events import (
    ActorFinished,
    ActorNameChanged,
    ActorStarted,
    CanonicalEvent,
    CompactionFinished,
    CompactionStarted,
    EventPayload,
    GoalChanged,
    SessionFinished,
    TurnFinished,
)
from domain.values import ActorRole, Outcome
from harness.impl.claude_code.canonical.messages import session_events
from harness.impl.claude_code.canonical.support import event
from harness.impl.claude_code.canonical.toolcalls import ToolCallSemantics
from harness.impl.claude_code.canonical.turns import TurnSemantics
from harness.models import RawEvent
from harness.models.selections import SelectionSemantics


def effort_report(
    raw_event: RawEvent,
    document: dict,
    selections: SelectionSemantics,
) -> list[CanonicalEvent]:
    """The active effort level Claude Code reports on hooks that fire mid-turn
    (PreToolUse, PostToolUse, Stop, SubagentStop), when the current model
    supports the effort parameter. `launch_selections()` and a typed `/effort`
    both only ever see the LEAD actor; a subagent gets neither, so this is the
    only evidence its own effort is ever observed from.

    Every one of those hooks reports it, so all but the first report the level
    that is already known — a change event with nothing changed. Only a real
    transition survives `selections`."""
    level = document.get("effort")
    if isinstance(level, dict):
        level = level.get("level")
    if not isinstance(level, str) or not level:
        return []
    changed = selections.effort(
        raw_event.session_id, raw_event.actor_id, level, "reported_by_harness"
    )
    if changed is None:
        return []
    return [
        event(
            raw_event,
            "effort",
            str(raw_event.actor_id),
            "reported",
            changed,
        )
    ]


def turn_finished(
    raw_event: RawEvent,
    turns: TurnSemantics,
    native_identity: str,
    outcome: Outcome,
) -> CanonicalEvent:
    """The Stop hook closes whatever turn is open. Its identity is that turn's,
    so the one Stop per turn is one fact; a Stop with no turn open — the daemon
    started mid-turn — falls back to the hook's own identity rather than
    colliding with the last one."""
    turn_id = turns.close(raw_event)
    return event(
        raw_event,
        "turn",
        str(turn_id) if turn_id else native_identity,
        "finished",
        TurnFinished(None, outcome),
        turn_id=turn_id,
    )


def translate_hook(
    raw_event: RawEvent,
    document: dict,
    toolcalls: ToolCallSemantics,
    turns: TurnSemantics,
    selections: SelectionSemantics,
) -> list[CanonicalEvent]:
    hook_name = document.get("hook_event_name") or ""
    native_identity = str(document.get("hook_event_id") or document.get("uuid") or raw_event.source_position)
    if hook_name == "SessionStart":
        return session_events(raw_event, document)
    if hook_name == "SessionEnd":
        payload: EventPayload = SessionFinished("succeeded", document.get("reason") or None)
        return [event(raw_event, "session", str(raw_event.session_id), "finished", payload)]
    if hook_name == "Stop":
        return [
            turn_finished(raw_event, turns, native_identity, "succeeded"),
            *effort_report(raw_event, document, selections),
        ]
    if hook_name == "StopFailure":
        events = [turn_finished(raw_event, turns, native_identity, "failed")]
        if document.get("error") == "rate_limit":
            events.append(event(
                raw_event,
                "goal",
                native_identity,
                "changed",
                GoalChanged(None, "usage_limited", "rate_limit"),
            ))
        return events
    if hook_name == "PreToolUse":
        return [
            *toolcalls.tool_started(raw_event, document),
            *effort_report(raw_event, document, selections),
        ]
    if hook_name in ("PostToolUse", "PostToolUseFailure"):
        return [
            *toolcalls.tool_finished(raw_event, document, hook_name == "PostToolUseFailure"),
            *effort_report(raw_event, document, selections),
        ]
    if hook_name == "SubagentStart":
        actor_id = raw_event.actor_id
        role: ActorRole = "teammate" if raw_event.source_type == "teammate_hook" else "child"
        events = [
            event(
                raw_event,
                "actor",
                str(actor_id),
                "started",
                ActorStarted(str(actor_id), role),
            )
        ]
        if document.get("agent_type"):
            events.append(
                event(
                    raw_event,
                    "actor",
                    str(actor_id),
                    "name",
                    ActorNameChanged(str(document["agent_type"])),
                )
            )
        return events
    if hook_name == "SubagentStop":
        # The authoritative "this agent's own loop ended" fact. A `<task-notification>`
        # in the PARENT transcript (messages.py) is the only other source of a
        # subagent's completion, and Claude Code suppresses it while the agent has
        # a live background child of its own — which left agents that spawned a
        # `run_in_background` command stuck "running" forever on the dashboard even
        # after their own conversation had genuinely stopped. This hook fires
        # regardless, straight from the child's own process.
        return [
            event(raw_event, "actor", str(raw_event.actor_id), "finished", ActorFinished(None)),
            *effort_report(raw_event, document, selections),
        ]
    if hook_name in ("TaskCreated", "TaskCompleted"):
        return []
    if hook_name == "PreCompact":
        return [
            event(
                raw_event,
                "compaction",
                native_identity,
                "started",
                CompactionStarted(None),
            )
        ]
    if hook_name == "PostCompact":
        return [
            event(
                raw_event,
                "compaction",
                native_identity,
                "finished",
                CompactionFinished(None, None),
            )
        ]
    return []
