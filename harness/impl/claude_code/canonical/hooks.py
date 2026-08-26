"""Claude Code hook-stream translation: lifecycle and tool-call notifications."""

from __future__ import annotations

from domain.events import (
    ActorFinished,
    ActorNameChanged,
    ActorStarted,
    CanonicalEvent,
    CompactionStarted,
    EventPayload,
    GoalChanged,
    SessionFinished,
    TurnFinished,
)
from domain.values import ActorRole, EffortChangeReason, GoalState, Outcome
from harness.impl.claude_code.canonical import records
from harness.impl.claude_code.canonical.messages import session_events
from harness.impl.claude_code.canonical.support import event
from harness.impl.claude_code.canonical.tasks import task_hook_event
from harness.impl.claude_code.canonical.toolcalls import ToolCallSemantics
from harness.impl.claude_code.canonical.turns import TurnSemantics
from harness.impl.claude_code.ids import ClaudeCodeShellId
from harness.models import RawEvent, session_run_finished_event
from harness.models.selections import SelectionSemantics


def effort_report(
    raw_event: RawEvent,
    hook: records.HookPayload,
    selection_semantics: SelectionSemantics,
) -> list[CanonicalEvent[EventPayload]]:
    """The active effort level Claude Code reports on hooks that fire mid-turn
    (PreToolUse, PostToolUse, Stop, SubagentStop), when the current model
    supports the effort parameter. `launch_selections()` and a typed `/effort`
    both only ever see the LEAD actor; a subagent gets neither, so this is the
    only raw event its own effort is ever observed from.

    Every one of those hooks reports it, so all but the first report the level
    that is already known — a change event with nothing changed. Only a real
    transition survives `selections`."""
    level = hook.effort.level if isinstance(hook.effort, records.HookEffort) else hook.effort
    if not isinstance(level, str) or not level:
        return []
    changed = selection_semantics.effort(
        raw_event.session_id, raw_event.actor_id, level, EffortChangeReason.REPORTED_BY_HARNESS
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
    turn_semantics: TurnSemantics,
    native_identity: str,
    outcome: Outcome,
) -> CanonicalEvent[EventPayload]:
    """The Stop hook closes whatever turn is open. Its identity is that turn's,
    so the one Stop per turn is one fact; a Stop with no turn open — the daemon
    started mid-turn — falls back to the hook's own identity rather than
    colliding with the last one."""
    turn_id = turn_semantics.close(raw_event)
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
    hook: records.HookPayload,
    tool_call_semantics: ToolCallSemantics,
    turn_semantics: TurnSemantics,
    selection_semantics: SelectionSemantics,
) -> list[CanonicalEvent[EventPayload]]:
    hook_name = hook.hook_event_name or ""
    native_identity = str(hook.hook_event_id or hook.uuid or raw_event.source_position)
    if hook_name == "SessionStart":
        return session_events(raw_event, hook)
    if hook_name == "SessionEnd":
        session_finished = SessionFinished(Outcome.SUCCEEDED, hook.reason or None)
        if raw_event.terminal_window_id is not None:
            return [session_run_finished_event(raw_event, session_finished)]
        return [event(
            raw_event,
            "session",
            str(raw_event.session_id),
            "finished",
            session_finished,
        )]
    if hook_name == "Stop":
        return [
            turn_finished(raw_event, turn_semantics, native_identity, Outcome.SUCCEEDED),
            *effort_report(raw_event, hook, selection_semantics),
        ]
    if hook_name == "StopFailure":
        events = [turn_finished(raw_event, turn_semantics, native_identity, Outcome.FAILED)]
        if hook.error == "rate_limit":
            events.append(event(
                raw_event,
                "goal",
                native_identity,
                "changed",
                GoalChanged(None, GoalState.USAGE_LIMITED, "rate_limit"),
            ))
        return events
    if hook_name == "PreToolUse":
        return [
            *tool_call_semantics.tool_started(raw_event, records.ToolCallNative(
                tool_use_id=hook.tool_use_id,
                tool_name=hook.tool_name,
                tool_input=hook.tool_input,
            )),
            *effort_report(raw_event, hook, selection_semantics),
        ]
    if hook_name in ("PostToolUse", "PostToolUseFailure"):
        if hook_name == "PostToolUse" and hook.tool_name == "TaskStop":
            return [
                *tool_call_semantics.background_stopped(
                    raw_event,
                    task_id=ClaudeCodeShellId(str(
                        hook.tool_input.task_id
                        if hook.tool_input is not None
                        else ""
                    )),
                    transcript_path=str(hook.transcript_path or ""),
                ),
                *effort_report(raw_event, hook, selection_semantics),
            ]
        events = [
            *tool_call_semantics.tool_finished(raw_event, records.ToolCallNative(
                tool_use_id=hook.tool_use_id,
                tool_name=hook.tool_name,
                tool_input=hook.tool_input,
                tool_response=(
                    hook.tool_response
                    if hook.tool_response is not None
                    else hook.error
                ),
            ), hook_name == "PostToolUseFailure"),
            *effort_report(raw_event, hook, selection_semantics),
        ]
        return events
    if hook_name == "SubagentStart":
        actor_id = raw_event.actor_id
        role: ActorRole = ActorRole.TEAMMATE if raw_event.source_type == "teammate_hook" else ActorRole.CHILD
        events = [
            event(
                raw_event,
                "actor",
                str(actor_id),
                "started",
                ActorStarted(str(actor_id), role),
            )
        ]
        if hook.agent_type:
            events.append(
                event(
                    raw_event,
                    "actor",
                    str(actor_id),
                    "name",
                    ActorNameChanged(str(hook.agent_type)),
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
            *effort_report(raw_event, hook, selection_semantics),
        ]
    if hook_name in ("TaskCreated", "TaskCompleted"):
        return [task_hook_event(raw_event, hook)]
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
        # Claude writes the authoritative `compact_boundary` transcript record
        # for this same finish. That record has the exact pre-compaction token
        # count; this hook has no counts. Treat the hook as delivery plumbing so
        # one native compaction produces one finished fact and one feed entry.
        return []
    return []
