"""The session half of the aggregate: what it is, what it is for, what is left.

Three writers over one row. Each owns its own fields and touches nothing else,
which is why they can be a list rather than a switch: adding a concern means
adding a writer, not editing the one that folds everything.
"""

from __future__ import annotations

from dataclasses import replace

from domain.events import (
    EventPayload,
    GoalChanged,
    MessageCreated,
    SessionAccountChanged,
    SessionFinished,
    SessionStarted,
    SessionTitleChanged,
    TaskChanged,
    TaskListChanged,
)
from domain.records import CommittedEvent
from domain.sessiondata import SessionFacts, SessionGoal, SessionTask
from domain.values import TextContent
from engine.sessiondata.contract import AggregateState, SessionDataWriter

# A prompt-derived title is one line of it, and a line of a prompt can be a
# paragraph — cut where a list row would cut it anyway.
PROMPT_TITLE_LIMIT = 200


class SessionWriter(SessionDataWriter):
    """The session's own row: born at `session.started`, and its identity,
    title, account and lifecycle from then on."""

    def write(self, canonical_event: CommittedEvent, state: AggregateState) -> AggregateState:
        event = canonical_event.event
        payload = event.payload
        if isinstance(payload, SessionStarted):
            born = _born(canonical_event)
            if state.session is None:
                return replace(state, session=born)
            # A session that starts again is the same session RESUMED: the
            # lifecycle reopens, and everything already folded about the work —
            # its title, its goal, its tasks — stands.
            return replace(
                state,
                session=replace(
                    state.session,
                    state="running",
                    finished_at=None,
                    working_directory=(
                        born.working_directory or state.session.working_directory
                    ),
                ),
            )
        session = state.session
        if session is None:
            return state
        if isinstance(payload, SessionTitleChanged):
            return replace(state, session=_titled(session, payload))
        if isinstance(payload, SessionAccountChanged):
            return replace(state, session=replace(session, account=payload.account))
        if isinstance(payload, SessionFinished):
            return replace(
                state,
                session=replace(
                    session, state="finished", finished_at=canonical_event.happened_at
                ),
            )
        if (
            isinstance(payload, MessageCreated)
            and _is_prompt(payload)
            and session.prompt_title_internal is None
        ):
            return replace(state, session=_prompt_titled(session, payload))
        return state


def _born(canonical_event: CommittedEvent) -> SessionFacts:
    event = canonical_event.event
    payload = event.payload
    assert isinstance(payload, SessionStarted)
    return SessionFacts(
        session_id=event.session_id,
        harness=event.harness,
        state="running",
        working_directory=payload.working_directory,
        started_at=canonical_event.happened_at,
        lead_actor_id=event.actor_id,
        account=payload.account,
        automatic_title_internal=payload.title,
    )


def _is_prompt(payload: EventPayload) -> bool:
    return (
        isinstance(payload, MessageCreated)
        and payload.role == "user"
        and payload.phase == "prompt"
    )


def _titled(session: SessionFacts, payload: SessionTitleChanged) -> SessionFacts:
    title = payload.title or None
    if payload.origin == "custom":
        session = replace(session, custom_title_internal=title)
    elif payload.origin == "automatic":
        session = replace(session, automatic_title_internal=title)
    else:
        session = replace(session, summary_title_internal=title)
    return _retitled(session)


def _prompt_titled(session: SessionFacts, payload: MessageCreated) -> SessionFacts:
    if not isinstance(payload.content, TextContent):
        return session
    lines = payload.content.text.strip().splitlines()
    if not lines:
        return session
    return _retitled(replace(session, prompt_title_internal=lines[0][:PROMPT_TITLE_LIMIT]))


def _retitled(session: SessionFacts) -> SessionFacts:
    """One precedence, in one place: what a person chose beats what the harness
    named, which beats a summary of it, which beats the first thing asked."""
    return replace(
        session,
        title=(
            session.custom_title_internal
            or session.automatic_title_internal
            or session.summary_title_internal
            or session.prompt_title_internal
        ),
    )


class GoalWriter(SessionDataWriter):
    """The session's objective, and whether it was reached.

    Seven native goal states collapse to two fields here: a cleared goal is no
    goal, and of the rest only `completed` changes what a reader does.
    """

    def write(self, canonical_event: CommittedEvent, state: AggregateState) -> AggregateState:
        payload = canonical_event.event.payload
        if not isinstance(payload, GoalChanged) or state.session is None:
            return state
        if payload.state == "cleared":
            return replace(state, session=replace(state.session, goal=None))
        return replace(
            state,
            session=replace(
                state.session,
                goal=SessionGoal(payload.objective, payload.state == "completed"),
            ),
        )


class TaskWriter(SessionDataWriter):
    """The task list: each task's own facts, ordered by the membership fact.

    Two events, two jobs. `task.changed` is what a task IS; `task.list_changed`
    is which tasks there are and in what order — a task the list stopped naming
    is gone from it, even though its own last state still stands.
    """

    def write(self, canonical_event: CommittedEvent, state: AggregateState) -> AggregateState:
        payload = canonical_event.event.payload
        session = state.session
        if session is None:
            return state
        if isinstance(payload, TaskChanged):
            return replace(state, session=_task_changed(session, payload))
        if isinstance(payload, TaskListChanged):
            return replace(
                state,
                session=_ordered(replace(session, task_order_internal=payload.task_ids)),
            )
        return state


def _task_changed(session: SessionFacts, payload: TaskChanged) -> SessionFacts:
    task = SessionTask(
        task_id=payload.task_id,
        subject=payload.subject,
        description=payload.description,
        state=payload.state,
        owner_actor_id=payload.owner_actor_id,
    )
    known = {existing.task_id: existing for existing in session.tasks}
    known[task.task_id] = task
    order = session.task_order_internal or tuple(known)
    if task.task_id not in order:
        # A task nothing has listed yet still belongs to the session: the
        # membership fact and the task's own fact arrive in either order.
        order = (*order, task.task_id)
    return _ordered(replace(session, tasks=tuple(known.values()), task_order_internal=order))


def _ordered(session: SessionFacts) -> SessionFacts:
    """`tasks` in the order the list declared, and holding only what it names."""
    known = {task.task_id: task for task in session.tasks}
    order = session.task_order_internal or tuple(known)
    return replace(
        session,
        tasks=tuple(known[task_id] for task_id in order if task_id in known),
    )
