"""What the session did: work still running, what it added up to, how long.

Every fold here reads the ACTIVITY stream rather than the raw events — a
background job is an operation whose execution says so, and a statistic counts
finished work, both of which only exist once the events have been folded.
"""

from __future__ import annotations

from types import MappingProxyType

from domain.events import (
    MessageCreated,
    SessionFinished,
    SessionStarted,
    TurnAborted,
    TurnFinished,
)
from domain.ids import ActorId, OperationId
from engine.projections.activity import activities_of
from engine.projections.models import (
    ActivityScope,
    ActivityStatistics,
    ActorMessageActivity,
    BackgroundWorkSummary,
    FileActivity,
    OperationActivity,
)
from engine.store.canonical import StoredCanonicalEvent


def background_work(
    stored_events: tuple[StoredCanonicalEvent, ...],
    scope: ActivityScope,
) -> BackgroundWorkSummary:
    activities, _positions, _revisions = activities_of(stored_events, scope)
    operations = [activity for activity in activities if isinstance(activity, OperationActivity)]
    return BackgroundWorkSummary(
        running_operation_ids=tuple(
            activity.operation_id
            for activity in operations
            if activity.state == "running" and activity.execution in ("background", "monitor")
        ),
        monitor_count=sum(activity.execution == "monitor" for activity in operations),
        background_job_count=sum(activity.execution == "background" for activity in operations),
    )


def background_operations(
    stored_events: tuple[StoredCanonicalEvent, ...],
    scope: ActivityScope,
) -> tuple[OperationActivity, ...]:
    activities, _positions, _revisions = activities_of(stored_events, scope)
    return tuple(
        activity
        for activity in activities
        if isinstance(activity, OperationActivity)
        and activity.execution in ("background", "monitor")
    )


def statistics(
    stored_events: tuple[StoredCanonicalEvent, ...],
    scope: ActivityScope,
) -> ActivityStatistics:
    activities, _positions, _revisions = activities_of(stored_events, scope)
    shell_commands = [
        activity
        for activity in activities
        if isinstance(activity, OperationActivity) and activity.category == "shell"
    ]
    files = [activity for activity in activities if isinstance(activity, FileActivity)]
    operation_counts: dict[str, int] = {}
    for activity in activities:
        if isinstance(activity, OperationActivity) and activity.category != "shell":
            operation_name = activity.native_name or activity.category or "operation"
            operation_counts[operation_name] = operation_counts.get(operation_name, 0) + 1
        elif isinstance(activity, FileActivity):
            operation_name = {
                "read": "Read",
                "created": "Write",
                "updated": "Edit",
                "deleted": "Delete",
                "renamed": "Move",
            }[activity.file.action]
            operation_counts[operation_name] = operation_counts.get(operation_name, 0) + 1
    return ActivityStatistics(
        shell_command_count=len(shell_commands),
        failed_shell_command_count=sum(
            activity.outcome == "failed" for activity in shell_commands
        ),
        file_count=len({activity.file.path for activity in files}),
        lines_added=sum(activity.file.lines_added or 0 for activity in files),
        lines_removed=sum(activity.file.lines_removed or 0 for activity in files),
        actor_message_count=sum(isinstance(activity, ActorMessageActivity) for activity in activities),
        operation_counts=MappingProxyType(operation_counts),
    )


def active_seconds(
    stored_events: tuple[StoredCanonicalEvent, ...],
    current_time: float,
) -> float:
    active_since = None
    active_seconds = 0.0
    lead_actor_id = None
    for stored in stored_events:
        event = stored.event
        event_time = event.occurred_at if event.occurred_at is not None else stored.accepted_at
        payload = event.payload
        if isinstance(payload, SessionStarted):
            lead_actor_id = event.actor_id
            active_since = event_time
        elif event.actor_id == lead_actor_id and isinstance(payload, MessageCreated):
            if payload.role == "user" and payload.phase == "prompt" and active_since is None:
                active_since = event_time
        elif event.actor_id == lead_actor_id and isinstance(payload, (TurnFinished, TurnAborted)):
            if active_since is not None:
                active_seconds += max(0.0, event_time - active_since)
                active_since = None
        elif isinstance(payload, SessionFinished) and active_since is not None:
            active_seconds += max(0.0, event_time - active_since)
            active_since = None
    if active_since is not None:
        active_seconds += max(0.0, current_time - active_since)
    return active_seconds


def operation_activity(
    stored_events: tuple[StoredCanonicalEvent, ...],
    actor_id: ActorId,
    operation_id: OperationId,
) -> OperationActivity:
    activities, _positions, _revisions = activities_of(
        stored_events, ActivityScope(actor_id=actor_id)
    )
    for activity in activities:
        if isinstance(activity, OperationActivity) and activity.operation_id == operation_id:
            return activity
    raise KeyError(str(operation_id))
