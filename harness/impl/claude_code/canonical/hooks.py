"""Claude Code hook-stream translation: lifecycle and tool-call notifications."""

from __future__ import annotations

from domain.events import (
    ActorFinished,
    ActorNameChanged,
    ActorStarted,
    CanonicalEvent,
    CompactionFinished,
    CompactionStarted,
    EffortChanged,
    EventPayload,
    GoalChanged,
    SessionFinished,
    TurnFinished,
)
from domain.values import ActorRole
from harness.impl.claude_code.canonical.messages import session_events
from harness.impl.claude_code.canonical.support import event
from harness.impl.claude_code.canonical.toolcalls import ToolCallSemantics
from harness.models import RawEvent


def effort_report(raw_event: RawEvent, document: dict) -> list[CanonicalEvent]:
    """The active effort level Claude Code reports on hooks that fire mid-turn
    (PreToolUse, PostToolUse, Stop, SubagentStop), when the current model
    supports the effort parameter. `launch_selections()` and a typed `/effort`
    both only ever see the LEAD actor; a subagent gets neither, so this is the
    only evidence its own effort is ever observed from."""
    level = document.get("effort")
    if isinstance(level, dict):
        level = level.get("level")
    if not isinstance(level, str) or not level:
        return []
    return [
        event(
            raw_event,
            "effort",
            str(raw_event.actor_id),
            "reported",
            EffortChanged(None, level, "reported_by_harness"),
        )
    ]


def translate_hook(raw_event: RawEvent, document: dict, toolcalls: ToolCallSemantics) -> list[CanonicalEvent]:
    hook_name = document.get("hook_event_name") or ""
    native_identity = str(document.get("hook_event_id") or document.get("uuid") or raw_event.source_position)
    if hook_name == "SessionStart":
        return session_events(raw_event, document)
    if hook_name == "SessionEnd":
        payload: EventPayload = SessionFinished("succeeded", document.get("reason") or None)
        return [event(raw_event, "session", str(raw_event.session_id), "finished", payload)]
    if hook_name == "Stop":
        payload = TurnFinished(None, "succeeded")
        return [
            event(raw_event, "turn", native_identity, "finished", payload),
            *effort_report(raw_event, document),
        ]
    if hook_name == "StopFailure":
        events = [
            event(
                raw_event, "turn", native_identity, "finished", TurnFinished(None, "failed")
            )
        ]
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
        return [*toolcalls.tool_started(raw_event, document), *effort_report(raw_event, document)]
    if hook_name in ("PostToolUse", "PostToolUseFailure"):
        return [
            *toolcalls.tool_finished(raw_event, document, hook_name == "PostToolUseFailure"),
            *effort_report(raw_event, document),
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
            *effort_report(raw_event, document),
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
