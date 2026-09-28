# Copyright (c) 2026 Zhambyl Yermagambet
"""Translate direct Claude Code collaboration notifications."""

from domain import event_shell, event_work, shells, work_state
from harness.impl.claude_code import ids as claude_ids
from harness.impl.claude_code.canonical import (
    message_collaboration_dependencies as dependencies,
    message_models,
    transcript,
)
from harness.impl.claude_code.canonical.message_launch import _assignment_finish_phase, background_outcome
from harness.models import raw_events

SHELL_EVENT_CATEGORY = "shell"


def goal_event(
    source: message_models.TranscriptSource,
    record: transcript.GoalTranscriptRecord,
) -> dependencies.event_base.CanonicalEvent[dependencies.event_base.EventPayload]:
    """Translate a goal state change.

    Returns:
        The canonical goal change event.

    """
    payload = event_work.GoalChanged(
        None if record.objective is None else str(record.objective),
        work_state.GoalState(record.state),
        None if record.reason is None else str(record.reason),
    )
    draft = dependencies.raw_event_builders.CanonicalEventDraft(
        "goal",
        source.native_identity,
        "changed",
        payload,
        occurred_at=source.occurred_at,
    )
    return dependencies.support.event(source.raw_event, draft)


def background_command_events(
    source: message_models.TranscriptSource,
    record: transcript.BackgroundCommandCompletedTranscriptRecord,
) -> list[dependencies.event_base.CanonicalEvent[dependencies.event_base.EventPayload]]:
    """Translate completion of a background command.

    Returns:
        An optional output location followed by the output completion event.

    Raises:
        TranslationError: If the command identifier is empty.

    """
    shell_id = claude_ids.shell_id_from_claude_code_call(record.operation_id)
    if not shell_id:
        msg = "Claude Code background completion has no command id"
        raise raw_events.TranslationError(
            msg,
            context=source.raw_event.source_position,
        )
    events = []
    if record.output_file is not None:
        events.append(_background_output_location_event(source, shell_id, record.output_file))
    payload = event_shell.ShellOutputFinished(shell_id, background_outcome(record.status))
    draft = dependencies.raw_event_builders.CanonicalEventDraft(
        SHELL_EVENT_CATEGORY,
        str(shell_id),
        "output_finished",
        payload,
        occurred_at=source.occurred_at,
    )
    events.append(dependencies.support.event(source.raw_event, draft))
    return events


def _background_output_location_event(
    source: message_models.TranscriptSource,
    shell_id: dependencies.ids.ShellId,
    source_path: str,
) -> dependencies.event_base.CanonicalEvent[dependencies.event_base.EventPayload]:
    payload = event_shell.ShellOutputLocated(
        shell_id=shell_id,
        source_path=source_path,
        chunk_source_type="foreground_output",
        delete_source=False,
        initial_size=0,
        initial_modified_at=0,
        wait_for_source_change=False,
        until=work_state.ShellFollowUntil.SESSION_FINISHED,
    )
    source_key = shells.shell_output_source_key(source_path)
    draft = dependencies.raw_event_builders.CanonicalEventDraft(
        SHELL_EVENT_CATEGORY,
        str(shell_id),
        f"output_located:{source_key}",
        payload,
        occurred_at=source.occurred_at,
    )
    return dependencies.support.event(source.raw_event, draft)


def monitor_event(
    source: message_models.TranscriptSource,
    record: transcript.MonitorEventTranscriptRecord,
    tool_calls: dependencies.toolcalls.ToolCallSemantics,
) -> list[dependencies.event_base.CanonicalEvent[dependencies.event_base.EventPayload]]:
    """Translate a status update for a monitored shell.

    Returns:
        A shell progress event, or no events if the monitor has no shell.

    """
    task_id = claude_ids.ClaudeCodeShellId(record.task)
    armed_shell = tool_calls.monitor_shell(source.raw_event, task_id)
    if armed_shell is None:
        return []
    ordinal = tool_calls.next_monitor_ordinal(source.raw_event, task_id)
    payload = event_shell.ShellProgressed(
        armed_shell,
        ordinal,
        dependencies.outcomes.ProgressStream.STATUS,
        dependencies.support.content(record.event),
        dependencies.outcomes.OutputMode.APPEND,
    )
    draft = dependencies.raw_event_builders.CanonicalEventDraft(
        SHELL_EVENT_CATEGORY,
        str(armed_shell),
        f"progress:status:{ordinal}",
        payload,
        occurred_at=source.occurred_at,
    )
    return [dependencies.support.event(source.raw_event, draft)]


def monitor_ended_events(
    source: message_models.TranscriptSource,
    record: transcript.MonitorEndedTranscriptRecord,
    tool_calls: dependencies.toolcalls.ToolCallSemantics,
) -> list[dependencies.event_base.CanonicalEvent[dependencies.event_base.EventPayload]]:
    """Translate a monitor's last event, then finish the monitor.

    The last event is translated first, while the arm still resolves its shell.

    Returns:
        The last event's progress, if any, and the output completion.

    """
    last = [] if record.last_event is None else monitor_event(
        source, transcript.MonitorEventTranscriptRecord(record.task, "", record.last_event), tool_calls,
    )
    ended = _monitor_output_finished(source, record, tool_calls)
    return last if ended is None else [*last, ended]


def _monitor_output_finished(
    source: message_models.TranscriptSource,
    record: transcript.MonitorEndedTranscriptRecord,
    tool_calls: dependencies.toolcalls.ToolCallSemantics,
) -> dependencies.event_base.CanonicalEvent[dependencies.event_base.EventPayload] | None:
    # A stream-ended notice names the shell directly. An expiry notice names only
    # the task, so the arm the translator saw resolves it.
    shell_id = (
        claude_ids.shell_id_from_claude_code_call(record.operation_id)
        if record.operation_id
        else tool_calls.monitor_shell(source.raw_event, record.task)
    )
    if shell_id is None:
        return None
    payload = event_shell.ShellOutputFinished(shell_id, background_outcome(record.status))
    tool_calls.monitor_finished(source.raw_event, shell_id)
    draft = dependencies.raw_event_builders.CanonicalEventDraft(
        SHELL_EVENT_CATEGORY,
        str(shell_id),
        "output_finished",
        payload,
        occurred_at=source.occurred_at,
    )
    return dependencies.support.event(source.raw_event, draft)


def assignment_finished_event(
    source: message_models.TranscriptSource,
    record: transcript.ActorAssignmentFinishedTranscriptRecord,
    tool_calls: dependencies.toolcalls.ToolCallSemantics,
) -> dependencies.event_base.CanonicalEvent[dependencies.event_base.EventPayload]:
    """Finish a tracked actor assignment.

    Returns:
        The assignment completion event with its outcome and optional result.

    """
    assignment_call = tool_calls.assignment_call(source.raw_event, record.actor_id, record.assignment_id)
    assignment_id = claude_ids.assignment_id_from_claude_code_call(assignment_call)
    tool_calls.assignment_finished(source.raw_event, record.actor_id)
    outcome = background_outcome(record.status) or dependencies.outcomes.Outcome.UNKNOWN
    payload = dependencies.event_actor.ActorAssignmentFinished(
        assignment_id,
        outcome,
        dependencies.support.content(record.result, markdown=True) if record.result else None,
        None,
    )
    draft = dependencies.raw_event_builders.CanonicalEventDraft(
        "actor_assignment",
        str(assignment_id),
        _assignment_finish_phase(record.status, record.result),
        payload,
        occurred_at=source.occurred_at,
    )
    return dependencies.support.event(source.raw_event, draft)
