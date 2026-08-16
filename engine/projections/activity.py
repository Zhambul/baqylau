"""The activity fold: a session's facts become the blocks a surface renders.

ONE pass, in cursor order, building an id-keyed map of activities. Two cursors
come out beside them and they are not the same thing:

    position  where the activity first appeared — what a backward page orders by
    revision  when it last CHANGED — what a live client polls forward on

An operation that streams output for a minute keeps its position and advances
its revision, which is what lets a pane update a block in place instead of
appending a second one.
"""

from __future__ import annotations

from dataclasses import replace

from domain.events import (
    ActorNameChanged,
    ActorStarted,
    AttentionRequested,
    AttentionResolved,
    CanonicalEvent,
    ActorAssignmentFinished,
    ActorAssignmentStarted,
    CompactionFinished,
    FileAccessed,
    MessageCreated,
    OperationFinished,
    OperationProgressed,
    OperationStarted,
    ActorMessageSent,
    ReasoningCreated,
    TaskChanged,
)
from domain.ids import (
    ActorId,
    AttentionId,
    AssignmentId,
)
from engine.projections.models import (
    Activity,
    ActivityContext,
    ActivityScope,
    ActorAssignmentActivity,
    ActorMessageActivity,
    AttentionActivity,
    CompactionActivity,
    FileActivity,
    MessageActivity,
    OperationActivity,
    ReasoningActivity,
    TaskActivity,
)
from engine.store.canonical import StoredCanonicalEvent


def _context(
    stored_event: StoredCanonicalEvent,
    activity_id: str,
    actor_name: str | None,
    *,
    finished: bool = False,
) -> ActivityContext:
    event = stored_event.event
    event_time = event.occurred_at if event.occurred_at is not None else stored_event.accepted_at
    return ActivityContext(
        activity_id=activity_id,
        source_event_ids=(event.event_id,),
        session_id=event.session_id,
        actor_id=event.actor_id,
        actor_name=actor_name,
        parent_actor_id=event.parent_actor_id,
        turn_id=event.turn_id,
        started_at=None if finished else event_time,
        finished_at=event_time if finished else None,
    )


def _activity_id(event: CanonicalEvent, activity_type: str, subject_id: object) -> str:
    return f"{activity_type}:{event.actor_id}:{subject_id}"


def activities_of(
    stored_events: tuple[StoredCanonicalEvent, ...],
    scope: ActivityScope,
) -> tuple[list[Activity], dict[str, int], dict[str, int]]:
    activities: dict[str, Activity] = {}
    order: list[str] = []
    position_cursors: dict[str, int] = {}
    revision_cursors: dict[str, int] = {}
    file_operation_ids: set[str] = set()
    hidden_operation_ids: set[str] = set()
    file_activity_ids_by_operation: dict[str, list[str]] = {}
    actor_names: dict[ActorId, str] = {}
    attention_requests: dict[tuple[ActorId, AttentionId], AttentionRequested] = {}
    actor_assignment_starts: dict[tuple[ActorId, AssignmentId], ActorAssignmentActivity] = {}
    for stored in stored_events:
        event = stored.event
        assignment_for_parent = (
            isinstance(event.payload, (ActorAssignmentStarted, ActorAssignmentFinished))
            and event.parent_actor_id is not None
        )
        activity_actor_id = (
            event.parent_actor_id if assignment_for_parent else event.actor_id
        )
        if isinstance(event.payload, ActorStarted):
            actor_names[event.actor_id] = event.payload.name
        elif isinstance(event.payload, ActorNameChanged):
            actor_names[event.actor_id] = event.payload.name
        if scope.actor_id is not None and activity_actor_id != scope.actor_id:
            continue
        payload = event.payload
        actor_name = actor_names.get(activity_actor_id)
        activity: Activity | None = None
        if isinstance(payload, MessageCreated):
            activity_id = _activity_id(event, "message", payload.message_id)
            activity = MessageActivity(
                _context(stored, activity_id, actor_name),
                payload.message_id,
                payload.role,
                payload.phase,
                payload.reply_to,
                payload.content,
            )
        elif isinstance(payload, ReasoningCreated):
            activity_id = _activity_id(event, "reasoning", payload.reasoning_id)
            activity = ReasoningActivity(
                _context(stored, activity_id, actor_name),
                payload.reasoning_id,
                payload.content,
                payload.summary,
            )
        elif isinstance(payload, OperationStarted):
            activity_id = _activity_id(event, "operation", payload.operation_id)
            if payload.category in ("file_read", "file_write", "file_edit"):
                file_operation_ids.add(activity_id)
                hidden_operation_ids.add(activity_id)
                continue
            if payload.category in ("attention", "task", "message"):
                hidden_operation_ids.add(activity_id)
                continue
            activity = OperationActivity(
                _context(stored, activity_id, actor_name),
                payload.operation_id,
                payload.category,
                payload.native_name,
                payload.execution,
                payload.arguments,
                payload.description,
                payload.parent_operation_id,
                (),
                "running",
                None,
                None,
                None,
                content_event_id=(event.event_id if payload.arguments is not None else None),
                content_field=("operation_content" if payload.arguments is not None else None),
            )
        elif isinstance(payload, OperationProgressed):
            activity_id = _activity_id(event, "operation", payload.operation_id)
            if activity_id in file_operation_ids:
                for file_activity_id in file_activity_ids_by_operation.get(activity_id, ()):
                    file_activity = activities[file_activity_id]
                    if isinstance(file_activity, FileActivity):
                        activities[file_activity_id] = replace(
                            file_activity,
                            context=replace(
                                file_activity.context,
                                source_event_ids=file_activity.context.source_event_ids + (event.event_id,),
                            ),
                            progress=file_activity.progress + (payload,),
                            content_event_id=(
                                file_activity.content_event_id or event.event_id
                            ),
                            content_field=(
                                file_activity.content_field or "content"
                            ),
                        )
                        revision_cursors[file_activity_id] = stored.cursor
                continue
            if activity_id in hidden_operation_ids:
                continue
            existing = activities.get(activity_id)
            if isinstance(existing, OperationActivity):
                activity = replace(
                    existing,
                    context=replace(
                        existing.context,
                        source_event_ids=existing.context.source_event_ids + (event.event_id,),
                    ),
                    progress=existing.progress + (payload,),
                    content_event_id=event.event_id,
                    content_field="operation_content",
                )
            else:
                activity = OperationActivity(
                    _context(stored, activity_id, actor_name),
                    payload.operation_id,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    (payload,),
                    "running",
                    None,
                    None,
                    None,
                    content_event_id=event.event_id,
                    content_field="operation_content",
                )
        elif isinstance(payload, OperationFinished):
            activity_id = _activity_id(event, "operation", payload.operation_id)
            if activity_id in file_operation_ids:
                for file_activity_id in file_activity_ids_by_operation.get(activity_id, ()):
                    file_activity = activities[file_activity_id]
                    if isinstance(file_activity, FileActivity):
                        activities[file_activity_id] = replace(
                            file_activity,
                            context=replace(
                                file_activity.context,
                                source_event_ids=file_activity.context.source_event_ids + (event.event_id,),
                                finished_at=(
                                    event.occurred_at
                                    if event.occurred_at is not None
                                    else stored.accepted_at
                                ),
                            ),
                            outcome=payload.outcome,
                            result=payload.result,
                            content_event_id=(
                                event.event_id
                                if payload.result is not None
                                else file_activity.content_event_id
                            ),
                            content_field=(
                                "result"
                                if payload.result is not None
                                else file_activity.content_field
                            ),
                        )
                        revision_cursors[file_activity_id] = stored.cursor
                continue
            if activity_id in hidden_operation_ids:
                continue
            existing = activities.get(activity_id)
            if isinstance(existing, OperationActivity):
                activity = replace(
                    existing,
                    context=replace(
                        existing.context,
                        source_event_ids=existing.context.source_event_ids + (event.event_id,),
                        finished_at=(
                            event.occurred_at
                            if event.occurred_at is not None
                            else stored.accepted_at
                        ),
                    ),
                    state="finished",
                    outcome=payload.outcome,
                    result=payload.result,
                    exit_code=payload.exit_code,
                    content_event_id=(
                        event.event_id
                        if payload.result is not None
                        else existing.content_event_id
                    ),
                    content_field=(
                        "operation_content"
                        if payload.result is not None
                        else existing.content_field
                    ),
                )
            else:
                activity = OperationActivity(
                    _context(stored, activity_id, actor_name, finished=True),
                    payload.operation_id,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    (),
                    "finished",
                    payload.outcome,
                    payload.result,
                    payload.exit_code,
                    content_event_id=(event.event_id if payload.result is not None else None),
                    content_field=("operation_content" if payload.result is not None else None),
                )
        elif isinstance(payload, FileAccessed):
            activity_id = (
                _activity_id(event, "file", f"{payload.operation_id}:{payload.path}")
                if payload.operation_id is not None
                else f"file:{event.event_id}"
            )
            if payload.unified_diff is not None:
                content_field = "unified_diff"
            elif payload.content is not None:
                content_field = "content"
            else:
                content_field = None
            existing = activities.get(activity_id)
            context = _context(stored, activity_id, actor_name)
            if isinstance(existing, FileActivity):
                context = replace(
                    existing.context,
                    source_event_ids=existing.context.source_event_ids + (event.event_id,),
                )
            activity = FileActivity(
                context,
                payload,
                existing.progress if isinstance(existing, FileActivity) else (),
                existing.outcome if isinstance(existing, FileActivity) else None,
                existing.result if isinstance(existing, FileActivity) else None,
                event.event_id if content_field is not None else None,
                content_field,
            )
            if payload.operation_id is not None:
                operation_activity_id = _activity_id(event, "operation", payload.operation_id)
                file_activity_ids_by_operation.setdefault(operation_activity_id, []).append(activity_id)
        elif isinstance(payload, AttentionRequested):
            attention_requests[(event.actor_id, payload.attention_id)] = payload
            activity_id = f"attention-request:{event.event_id}"
            activity = AttentionActivity(
                _context(stored, activity_id, actor_name),
                payload.attention_id,
                payload.attention_type,
                payload.prompts,
                "requested",
                None,
                (),
                None,
                False,
                None,
            )
        elif isinstance(payload, AttentionResolved):
            request = attention_requests.pop((event.actor_id, payload.attention_id), None)
            activity_id = f"attention-resolution:{event.event_id}"
            activity = AttentionActivity(
                _context(stored, activity_id, actor_name, finished=True),
                payload.attention_id,
                request.attention_type if request is not None else None,
                request.prompts if request is not None else (),
                "resolved",
                payload.decision,
                payload.answers,
                payload.feedback,
                payload.edited,
                payload.outcome,
            )
        elif isinstance(payload, TaskChanged):
            activity_id = f"task:{event.event_id}"
            activity = TaskActivity(_context(stored, activity_id, actor_name), payload)
        elif isinstance(payload, CompactionFinished):
            activity_id = f"compaction:{event.event_id}"
            activity = CompactionActivity(
                _context(stored, activity_id, actor_name, finished=True),
                payload.before_tokens,
                payload.after_tokens,
            )
        elif isinstance(payload, ActorAssignmentStarted):
            activity_id = f"actor_assignment-start:{event.event_id}"
            activity = ActorAssignmentActivity(
                _context(stored, activity_id, actor_name),
                payload.assignment_id,
                payload.brief,
                "running",
                None,
                None,
                None,
                assigned_actor_name=payload.actor_name,
                prompt=payload.prompt,
            )
            actor_assignment_starts[(activity_actor_id, payload.assignment_id)] = activity
        elif isinstance(payload, ActorAssignmentFinished):
            activity_id = f"actor_assignment-finish:{event.event_id}"
            started = actor_assignment_starts.get((activity_actor_id, payload.assignment_id))
            context = _context(stored, activity_id, actor_name, finished=True)
            if assignment_for_parent:
                context = replace(
                    context,
                    actor_id=activity_actor_id,
                    parent_actor_id=None,
                )
            if started is not None:
                context = replace(
                    context,
                    turn_id=started.context.turn_id,
                    started_at=started.context.started_at,
                )
            activity = ActorAssignmentActivity(
                context,
                payload.assignment_id,
                started.brief if started is not None else None,
                "finished",
                payload.outcome,
                payload.result,
                payload.reason,
                assigned_actor_name=(
                    started.assigned_actor_name if started is not None else None
                ),
                prompt=started.prompt if started is not None else None,
            )
        elif isinstance(payload, ActorMessageSent):
            activity_id = _activity_id(event, "actor_message", payload.message_id)
            activity = ActorMessageActivity(
                _context(stored, activity_id, actor_name),
                payload.message_id,
                payload.recipient_actor_id,
                payload.content,
            )
        if activity is None:
            continue
        activity_id = activity.context.activity_id
        if activity_id not in activities:
            order.append(activity_id)
            position_cursors[activity_id] = stored.cursor
        activities[activity_id] = activity
        revision_cursors[activity_id] = stored.cursor
    return (
        [activities[activity_id] for activity_id in order],
        position_cursors,
        revision_cursors,
    )
