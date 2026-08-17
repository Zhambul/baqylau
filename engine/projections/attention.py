"""What the session is waiting on, what it plans to do, and why.

Three folds that share one rule: a request stands until something explicitly
retires it. An attention outlives its own turn; a task outlives its list only
while some list still names it; a goal outlives everything but a clear.
"""

from __future__ import annotations

from domain.events import (
    ActorAssignmentFinished,
    ActorFinished,
    AttentionRequested,
    AttentionResolved,
    GoalChanged,
    SessionFinished,
    TaskChanged,
    TaskListChanged,
    TurnAborted,
    TurnFinished,
)
from domain.ids import ActorId, TaskId
from engine.projections.models import (
    AttentionState,
    GoalState,
    PendingAttention,
    TaskSummary,
)
from domain.records import StoredCanonicalEvent


def attention(stored_events: tuple[StoredCanonicalEvent, ...]) -> AttentionState:
    pending: dict[tuple[ActorId, str], PendingAttention] = {}
    for stored in stored_events:
        event = stored.event
        payload = event.payload
        # The key is built inside each branch rather than once above them: it
        # is only defined for the two payloads that HAVE an attention_id, and
        # hoisting it meant repeating that same isinstance pair in a
        # conditional whose None case was unreachable by construction.
        if isinstance(payload, AttentionRequested):
            pending[(event.actor_id, str(payload.attention_id))] = PendingAttention(
                event.actor_id, payload
            )
        elif isinstance(payload, AttentionResolved):
            pending.pop((event.actor_id, str(payload.attention_id)), None)
        elif isinstance(
            payload,
            (
                TurnFinished,
                TurnAborted,
                ActorAssignmentFinished,
                ActorFinished,
                SessionFinished,
            ),
        ):
            pending = {
                pending_key: attention
                for pending_key, attention in pending.items()
                if attention.actor_id != event.actor_id
            }
    return AttentionState(tuple(pending.values()))


def tasks(stored_events: tuple[StoredCanonicalEvent, ...]) -> tuple[TaskSummary, ...]:
    tasks: dict[TaskId, TaskSummary] = {}
    task_lists: dict[str, set[TaskId]] = {}
    for stored in stored_events:
        payload = stored.event.payload
        if isinstance(payload, TaskListChanged):
            previous_ids = task_lists.get(payload.list_id, set())
            current_ids = set(payload.task_ids)
            task_lists[payload.list_id] = current_ids
            retained_ids = set().union(*task_lists.values()) if task_lists else set()
            for task_id in previous_ids - current_ids:
                if task_id not in retained_ids:
                    tasks.pop(task_id, None)
        elif isinstance(payload, TaskChanged):
            # One TaskChanged branch that splits on the state, rather than two
            # elifs testing the same payload type. A deleted task is a removal;
            # every other state is a row — and only this shape makes the state
            # reaching TaskSummary provably not "deleted", which is the one
            # value it does not accept.
            state = payload.state
            if state == "deleted":
                tasks.pop(payload.task_id, None)
                for task_ids in task_lists.values():
                    task_ids.discard(payload.task_id)
            else:
                tasks[payload.task_id] = TaskSummary(
                    payload.task_id,
                    payload.label,
                    payload.subject,
                    payload.description,
                    state,
                    payload.owner_actor_id,
                )
    return tuple(tasks[task_id] for task_id in sorted(tasks, key=str))


def goal(
    stored_events: tuple[StoredCanonicalEvent, ...],
    lead_actor_id: ActorId,
) -> GoalState | None:
    goal = None
    for stored in stored_events:
        if stored.event.actor_id != lead_actor_id:
            continue
        payload = stored.event.payload
        if not isinstance(payload, GoalChanged):
            continue
        if payload.state == "cleared" or payload.objective is None:
            goal = None
        else:
            goal = GoalState(payload.objective, payload.state, payload.reason)
    return goal
