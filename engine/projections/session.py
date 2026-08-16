"""What the session and its actors are, folded from their own facts."""

from __future__ import annotations

from dataclasses import replace

from domain.events import (
    ActorDescriptionChanged,
    ActorFinished,
    ActorNameChanged,
    ActorStarted,
    ActorAssignmentFinished,
    ActorAssignmentStarted,
    EffortChanged,
    MessageCreated,
    ModelChanged,
    SessionAccountChanged,
    SessionFinished,
    SessionStarted,
    SessionTitleChanged,
    SessionWorkingDirectoryChanged,
)
from domain.ids import ActorId, SessionId
from domain.values import TextContent
from engine.projections.models import ActorSummary, SessionSummary
from engine.store.canonical import StoredCanonicalEvent


def summary(
    session_id: SessionId,
    stored_events: tuple[StoredCanonicalEvent, ...],
) -> SessionSummary | None:
    started = next((stored for stored in stored_events if isinstance(stored.event.payload, SessionStarted)), None)
    if started is None:
        return None
    payload = started.event.payload
    custom_title = None
    automatic_title = payload.title
    summary_title = None
    prompt_title = None
    working_directory = payload.working_directory
    finished_at = None
    model = payload.model
    effort = payload.effort
    account = payload.account
    prompt_count = 0
    automatic_model_change = None
    state = "running"
    for stored in stored_events:
        event = stored.event
        if isinstance(event.payload, SessionStarted):
            state = "running"
            finished_at = None
            if event.payload.working_directory:
                working_directory = event.payload.working_directory
        elif isinstance(event.payload, SessionTitleChanged):
            if event.payload.origin == "custom":
                custom_title = event.payload.title or None
            elif event.payload.origin == "automatic":
                automatic_title = event.payload.title or None
            else:
                summary_title = event.payload.title or None
        elif isinstance(event.payload, SessionWorkingDirectoryChanged):
            working_directory = event.payload.working_directory
        elif isinstance(event.payload, SessionAccountChanged):
            account = event.payload.account
        elif isinstance(event.payload, SessionFinished):
            state = "finished"
            finished_at = event.occurred_at if event.occurred_at is not None else stored.accepted_at
        elif isinstance(event.payload, ModelChanged) and event.actor_id == started.event.actor_id:
            model = event.payload.current
            automatic_model_change = event.payload if event.payload.reason == "automatic_fallback" else None
        elif isinstance(event.payload, EffortChanged) and event.actor_id == started.event.actor_id:
            effort = event.payload.current
        elif (
            isinstance(event.payload, MessageCreated)
            and event.payload.role == "user"
            and event.payload.phase == "prompt"
        ):
            prompt_count += 1
            if prompt_title is None and isinstance(event.payload.content, TextContent):
                first_line = event.payload.content.text.strip().splitlines()
                prompt_title = first_line[0][:200] if first_line else None
    title = custom_title or automatic_title or summary_title or prompt_title
    return SessionSummary(
        session_id=session_id,
        harness=started.event.harness,
        title=title,
        working_directory=working_directory,
        initial_working_directory=payload.working_directory,
        started_at=(
            started.event.occurred_at
            if started.event.occurred_at is not None
            else started.accepted_at
        ),
        finished_at=finished_at,
        lead_actor_id=started.event.actor_id,
        model=model,
        effort=effort,
        account=account,
        prompt_count=prompt_count,
        automatic_model_change=automatic_model_change,
        state=state,
    )


def actors(stored_events: tuple[StoredCanonicalEvent, ...]) -> tuple[ActorSummary, ...]:
    actors: dict[ActorId, ActorSummary] = {}
    for stored in stored_events:
        event = stored.event
        payload = event.payload
        if isinstance(payload, ActorStarted):
            actors[event.actor_id] = ActorSummary(
                actor_id=event.actor_id,
                parent_actor_id=event.parent_actor_id,
                harness=event.harness,
                role=payload.role,
                name=payload.name,
                description=None,
                model=None,
                effort=None,
                state="running",
                started_at=(
                    event.occurred_at
                    if event.occurred_at is not None
                    else stored.accepted_at
                ),
                finished_at=None,
            )
        elif event.actor_id in actors:
            actor = actors[event.actor_id]
            if isinstance(payload, ActorNameChanged):
                actors[event.actor_id] = replace(actor, name=payload.name)
            elif isinstance(payload, ActorDescriptionChanged):
                actors[event.actor_id] = replace(actor, description=payload.description)
            elif isinstance(payload, ActorFinished):
                actors[event.actor_id] = replace(
                    actor,
                    state="finished",
                    finished_at=(
                        event.occurred_at
                        if event.occurred_at is not None
                        else stored.accepted_at
                    ),
                )
            elif isinstance(payload, ActorAssignmentStarted):
                actors[event.actor_id] = replace(
                    actor,
                    state="running",
                    finished_at=None,
                )
            elif isinstance(payload, ActorAssignmentFinished):
                actors[event.actor_id] = replace(
                    actor,
                    state="finished",
                    finished_at=(
                        event.occurred_at
                        if event.occurred_at is not None
                        else stored.accepted_at
                    ),
                )
            elif isinstance(payload, ModelChanged):
                actors[event.actor_id] = replace(actor, model=payload.current)
            elif isinstance(payload, EffortChanged):
                actors[event.actor_id] = replace(actor, effort=payload.current)
    return tuple(actors[actor_id] for actor_id in sorted(actors, key=str))
